# BENCHLAB Firmware Flash Tool

Flashes new firmware onto BENCHLAB1/2 devices (STM32F303RB). There is no
custom bootloader in the firmware -- instead, the running firmware can be
commanded over its normal USB CDC protocol to jump into ST's factory ROM
DFU bootloader, which this tool then talks to over standard USB DFU (via a
pure-Python `pyusb`-based client, so no external flashing binary is
required).

The tool owns the full round trip per device: it confirms the device is
connected in normal CDC mode, commands it into DFU mode, erases/writes/
verifies the new firmware, and leaves DFU mode so the device re-enumerates
as CDC again automatically. You never need to manually plug/unplug the
device or put it into DFU mode yourself.

## Supported firmware images

- **`.bin`** -- primary supported format. This is what's shipped in
  release artifacts, and is flashed directly.
- **`.hex`** -- supported as a developer convenience for local builds.
- **`.elf`** -- **not supported for flashing.** ELF files carry debug
  symbols/sections, not a flat memory image. Release artifacts ship a
  `.bin`/`.hex` alongside the `.elf` for the same build -- use those.

## Minimum firmware version for BENCHLAB1

`CMD_BOOTLOADER` (the command this tool uses to request a DFU-mode jump)
only has a working handler starting at BENCHLAB1 `FIRMWARE_VERSION 0x04`.
Older units (0x02/0x03) predate it entirely and silently ignore the
command. This tool checks the firmware version before attempting anything
and will refuse with a clear error on unsupported units -- those require a
physical BOOT0/jumper bootloader entry, which is outside this tool's scope.

BENCHLAB2 firmware has always shipped with `CMD_BOOTLOADER` support.

## Usage

```
# List connected devices
python -m benchlab -flash --list

# Flash a single device (auto-selected if only one is connected)
python -m benchlab -flash --file benchlab1-fw06-v0.6.0.bin

# Flash a specific device
python -m benchlab -flash --port COM4 --file firmware.bin

# Flash every connected BENCHLAB device with the same image
python -m benchlab -flash --all --file firmware.bin

# Verify currently-flashed firmware against an image, without writing
python -m benchlab -flash --port COM4 --file firmware.bin --verify-only

# Skip the confirmation prompt (for scripts/automation)
python -m benchlab -flash --all --file firmware.bin --yes
```

When flashing multiple devices with `--all` (or a comma-separated
`--port`), devices are flashed strictly one at a time. If one device fails
(timeout, verify mismatch, etc.), the rest of the batch still proceeds --
a summary of per-device outcomes is printed at the end, and the command
exits non-zero if any device failed.

## Windows: USB driver setup (Zadig)

ST's ROM DFU device (VID `0483`, PID `DF11`) does not automatically bind to
a driver pyusb/libusb can use on stock Windows. If `--list`/flashing times
out waiting for the DFU device to appear, you likely need to bind a
WinUSB (or libusbK) driver to it using
[Zadig](https://zadig.akeo.ie/):

1. Start the flash operation so the device enters DFU mode (or put it into
   DFU mode and let it stay there briefly).
2. Open Zadig, enable "List All Devices", find the device (it should show
   up as an STM32 BOOTLOADER or similar, VID/PID `0483`/`DF11`).
3. Select the WinUSB driver and click "Install Driver" / "Replace Driver".
4. Re-run the flash command.

This is a one-time setup per machine/USB port combination. This is a
Windows driver-binding limitation, independent of this tool being pure
Python -- the same bind step is needed by any USB DFU tool on Windows.

## Linux

No special setup is usually required beyond standard udev rules for
non-root USB access to VID `0483`/PID `DF11`, similar to other USB
peripherals.

## Safety notes

- **BENCHLAB2's calibration data** lives in the last 8KB of flash
  (`0x0801E000`+), carved out as an "EEPROM" region. This tool only erases
  the specific 2KB pages the firmware image occupies (never a mass-erase),
  and validates that the image never extends into that region before
  touching flash -- calibration data is never at risk from a normal
  firmware flash.
- **Flashing is destructive.** Interrupting a flash mid-write could leave
  the device's application unusable, though ST's ROM bootloader itself
  (which lives in protected system memory) should remain accessible for
  recovery. Use `--verify-only` first if you want to check compatibility
  without writing anything.
- The tool validates (where possible) that the image's filename matches
  the connected device's variant, and will refuse a clear mismatch (e.g.
  a `benchlab2-*` image against a device reporting BENCHLAB1). An
  unrecognized filename produces a warning rather than a hard block, since
  not all images follow the release naming convention.
