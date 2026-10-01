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
- **`.elf`** -- supported by extracting its loadable (`PT_LOAD`) segments
  and flattening them into a single image starting at `0x08000000`, the
  same shape as a release `.bin` (confirmed byte-for-byte identical to the
  official `.bin` for both BENCHLAB1 and BENCHLAB2 release builds). Gaps
  between segments are padded with `0xFF`. Segments outside the internal
  flash address range (e.g. a `PT_LOAD` segment destined for SRAM) are
  rejected with a clear error rather than silently producing a bad image.

## Minimum firmware version for BENCHLAB1

`CMD_BOOTLOADER` (the command this tool uses to request a DFU-mode jump)
only has a working handler starting at BENCHLAB1 `FIRMWARE_VERSION 0x04`.
Older units (0x02/0x03) predate it entirely and silently ignore the
command. This tool checks the firmware version before attempting anything
and will refuse with a clear error on unsupported units -- see
[Flashing a unit with no software bootloader support](#flashing-a-unit-with-no-software-bootloader-support-dfu)
below for how to flash those via a physical BOOT0/jumper entry instead.

BENCHLAB2 firmware has always shipped with `CMD_BOOTLOADER` support.

## Usage

Running `python -m benchlab -flash` with no arguments starts a guided
interactive mode: it lists connected devices (port, variant, firmware
version, UID), lets you pick one, several, or all of them by number, asks
for the firmware file path, and requires two separate confirmations before
touching flash. This is the easiest way to flash a device and the
recommended starting point.

For scripting/automation, the same operations are available as flags:

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

When flashing multiple devices (`--all`, a comma-separated `--port`, or a
multi-selection in interactive mode), devices are flashed strictly one at a
time. If one device fails (timeout, verify mismatch, etc.), the rest of the
batch still proceeds -- a summary of per-device outcomes is printed at the
end, and the command exits non-zero if any device failed.

## Flashing via the C# BenchLab service (`--source`)

By default (`--source direct`) this tool opens the serial port itself,
which only works if nothing else currently holds it. If the C# BenchLab
service is running (the typical Windows setup) and already owns the port,
use `--source named_pipe` or `--source service_http` instead -- the
service sends the bootloader jump on this tool's behalf (via its existing
`SendBootloader` pipe command / `POST /device/{uid}/bootloader` endpoint)
and releases the port, then this tool takes over the bare USB device
directly via `pyusb`, identically to the direct-mode flow. You do not need
to stop the service.

```
# Via the service's named pipe (Windows only)
python -m benchlab -flash --source named_pipe --all --file firmware.bin

# Via the service's HTTP API
python -m benchlab -flash --source service_http --all --file firmware.bin

# ...with a non-default service URL or token auth enabled
python -m benchlab -flash --source service_http \
    --service-url http://localhost:8585 --service-token secret \
    --all --file firmware.bin
```

With `--source named_pipe`/`service_http`, `--port` takes the service's
pipe name / device UID respectively (as shown by `--list`), not a raw COM
port -- the physical COM port is still looked up automatically from the
device's reported info for the actual DFU handoff.

After leaving DFU mode, this tool only confirms the physical port came
back -- it does not re-query the service for the freshly-flashed device's
new firmware version, since the service reconnects to it on its own
schedule. Run `--list`/`-config --source <same> --list` again afterward if
you want to confirm the new version.

## Flashing a unit with no software bootloader support (`--dfu`)

BENCHLAB1 units on `FIRMWARE_VERSION` below `0x04` have no working
`CMD_BOOTLOADER` handler, so this tool can't command them into DFU mode
over CDC. For those units, put the device into DFU mode yourself via its
physical BOOT0 jumper/pin (see your hardware's documentation for the exact
procedure), then use `--dfu`:

```
# Variant guessed from the filename, with a y/n confirmation
python -m benchlab -flash --dfu --file benchlab-original-fw06-v0.6.0.bin

# Variant given explicitly (skips the filename guess/prompt)
python -m benchlab -flash --dfu --variant benchlab1 --file firmware.bin
```

A device manually jumpered into DFU mode has no CDC port, so it can't
report its own `VendorId`/`ProductId`/`FwVersion` the way a CDC-connected
device can -- `--dfu` mode has no way to auto-detect or cross-check the
variant against the connected hardware. If `--variant` isn't given, the
tool guesses from the image's filename and asks you to confirm, falling
back to an explicit prompt if the filename doesn't match the release
naming convention. Double check you're selecting the correct variant --
flashing the wrong variant's image onto a device will not match the
BENCHLAB2 EEPROM-boundary safety check for the actual connected hardware.

`--dfu` only supports a single device at a time (bare USB DFU devices
expose no BENCHLAB-specific identity, so multiple devices in DFU mode
simultaneously can't be told apart -- make sure only one is connected).

After flashing, **remove the BOOT0 jumper/strap and manually power-cycle
the device.** The tool triggers the device's reset as part of leaving DFU
mode, but if BOOT0 is still physically held, the STM32 boot ROM re-checks
it on every reset (not just power-on) and will keep re-entering DFU
instead of starting the newly-flashed application -- this is expected
hardware behavior, not a sign the flash failed. Confirmed on real
hardware: the device correctly completes the DfuSe leave sequence and
resets, but only boots the new firmware once BOOT0 is removed and power
is cycled.

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
