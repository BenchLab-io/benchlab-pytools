"""Orchestration for the firmware flash tool: CDC -> enter bootloader ->
DFU erase/write/verify -> leave DFU -> back to CDC.

No argparse/CLI here -- see flash_tool.py for the command-line interface.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from benchlab_pycore.core import (
    BENCHLAB_BL2_PRODUCT_ID,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
    config_io,
    get_benchlab_ports,
    read_device,
)

from benchlab.core.discovery import discover_devices
from benchlab.core.shared_serial import open_serial_connection
from benchlab.flash import dfu, image

logger = logging.getLogger("benchlab.flash.manager")

# CMD_BOOTLOADER has no working handler on BL1 firmware below this version
# (confirmed via firmware git history, commit c722244 "BENCHLAB 2
# bring-up..." -- versions 0x02/0x03 predate it entirely and silently
# ignore the opcode).
MIN_BOOTLOADER_FW_VERSION = 0x04

_KNOWN_PRODUCT_IDS = (BENCHLAB_ORIGINAL_PRODUCT_ID, BENCHLAB_BL2_PRODUCT_ID)
_BENCHLAB_VENDOR_ID = 0xEE


class UnsupportedFirmwareError(Exception):
    """Raised when the connected device's firmware can't enter DFU mode
    via the software command (too old)."""


@dataclass
class FlashResult:
    port: str
    uid: Optional[str]
    ok: bool
    message: str


class FlashManager:
    def discover_devices(self) -> List[dict]:
        return discover_devices()

    def identify_device(self, port: str) -> Optional[dict]:
        """Open `port`, read vendor/product/firmware info, and gate on it
        being a recognized BENCHLAB device. Returns None if the port can't
        be opened/read, or isn't a recognized device (e.g. already in DFU
        mode, which doesn't expose a CDC port at all)."""
        ser = open_serial_connection(port)
        if ser is None:
            return None
        try:
            info = read_device(ser)
        finally:
            ser.close()

        if not info:
            return None
        if info.get("VendorId") != _BENCHLAB_VENDOR_ID:
            return None
        if info.get("ProductId") not in _KNOWN_PRODUCT_IDS:
            return None
        return info

    def assert_cdc_mode(self, port: str) -> dict:
        """Confirm `port` is a BENCHLAB device currently running in normal
        CDC mode (not already in DFU). Raises ConnectionError with an
        actionable message otherwise."""
        info = self.identify_device(port)
        if info is None:
            raise ConnectionError(
                f"Could not identify a BENCHLAB device on {port}. It may "
                "already be in DFU mode (e.g. from an interrupted previous "
                "flash attempt) or may not be a BENCHLAB device. Try "
                "--list, or power-cycle the device and reconnect.")
        return info

    def check_bootloader_supported(
            self, device_info: dict, product_id: int) -> None:
        if (product_id == BENCHLAB_ORIGINAL_PRODUCT_ID
                and device_info.get("FwVersion", 0)
                < MIN_BOOTLOADER_FW_VERSION):
            raise UnsupportedFirmwareError(
                "This device's firmware "
                f"(0x{device_info.get('FwVersion', 0):02X}) predates "
                f"CMD_BOOTLOADER support (requires "
                f"0x{MIN_BOOTLOADER_FW_VERSION:02X}+). It cannot be put "
                "into DFU mode via software -- use a physical BOOT0/jumper "
                "bootloader entry instead.")

    def enter_dfu_mode(self, port: str, timeout: float = 5.0) -> None:
        """Send the bootloader-jump command and wait for the CDC port to
        disappear (the firmware defers the actual jump to its next 100ms
        task tick)."""
        ser = open_serial_connection(port)
        if ser is None:
            raise ConnectionError(f"Could not open {port} to enter DFU mode")
        try:
            config_io.enter_bootloader(ser)
        finally:
            ser.close()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ports = {p.get("port") for p in get_benchlab_ports()}
            if port not in ports:
                return
            time.sleep(0.2)
        raise TimeoutError(
            f"{port} did not disappear after requesting DFU bootloader "
            "entry")

    def wait_for_dfu_device(
            self, expected_count: int = 1, timeout: float = 10.0) -> list:
        """Poll for `expected_count` USB DFU device(s) (VID 0x0483 / PID
        0xDF11) to appear. Raises TimeoutError if they don't show up in
        time -- on Windows this is most commonly a driver-binding issue
        (ST's ROM DFU device needs a WinUSB/libusbK driver bound via
        Zadig; it does not auto-bind on stock Windows)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            devices = dfu.find_dfu_devices()
            if len(devices) == expected_count:
                return devices
            time.sleep(0.2)
        raise TimeoutError(
            f"Timed out waiting for {expected_count} DFU device(s) to "
            "appear. On Windows, check Device Manager: the DFU device may "
            "need a WinUSB driver bound via Zadig before pyusb can see it.")

    def erase_and_flash(
            self, usb_dev, data: bytes, product_id: int,
            progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> None:
        image.validate_image_size(data, product_id)
        dev = dfu.DfuDevice(usb_dev)

        first_page = (image.FLASH_BASE // image.PAGE_SIZE) * image.PAGE_SIZE
        end_addr = image.FLASH_BASE + len(data)
        addr = first_page
        while addr < end_addr:
            dev.erase_page(addr)
            addr += image.PAGE_SIZE

        dev.set_address_pointer(image.FLASH_BASE)
        dev.wait_while_state(dfu.STATE_DFU_DOWNLOAD_BUSY)

        total = len(data)
        written = 0
        block_num = 2  # DfuSe reserves block 0/1 for commands/address.
        while written < total:
            chunk = data[written:written + dev.transfer_size]
            dev.download(block_num, chunk)
            dev.wait_while_state(dfu.STATE_DFU_DOWNLOAD_BUSY)
            written += len(chunk)
            block_num += 1
            if progress_cb:
                progress_cb(written, total)

    def verify_image(self, usb_dev, data: bytes, product_id: int) -> bool:
        image.validate_image_size(data, product_id)
        dev = dfu.DfuDevice(usb_dev)
        dev.set_address_pointer(image.FLASH_BASE)
        # set_address_pointer leaves the device in dfuDNLOAD-IDLE (it's a
        # DNLOAD under the hood); DFU_UPLOAD only works from dfuIDLE, so an
        # ABORT is required in between (confirmed against real hardware --
        # uploading directly from dfuDNLOAD-IDLE fails with a USB pipe
        # error on this ROM bootloader).
        dev.abort()

        total = len(data)
        read_back = bytearray()
        block_num = 2
        while len(read_back) < total:
            remaining = total - len(read_back)
            chunk = dev.upload(block_num, min(dev.transfer_size, remaining))
            if not chunk:
                break
            read_back.extend(chunk)
            block_num += 1

        return bytes(read_back[:total]) == data

    def leave_and_reboot(
            self, usb_dev, port: str,
            timeout: float = 10.0) -> Optional[dict]:
        """Leave DFU mode (standard DfuSe "leave" sequence) and wait for
        the device's CDC port to reappear. No separate vendor command is
        needed: the freshly-flashed app's normal boot path re-initializes
        USB CDC on its own."""
        dev = dfu.DfuDevice(usb_dev)
        dev.set_address_pointer(image.FLASH_BASE)
        dev.leave_dfu()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ports = {p.get("port") for p in get_benchlab_ports()}
            if port in ports:
                return self.identify_device(port)
            time.sleep(0.2)

        logger.warning(
            "%s did not reappear within %.0fs after leaving DFU mode -- "
            "this may just be slow re-enumeration, verify manually.",
            port, timeout)
        return None

    def flash_one(self, port: str, data: bytes, product_id: int,
                  verify_only: bool = False,
                  progress_cb: Optional[Callable[[int, int], None]] = None
                  ) -> FlashResult:
        """Run the full single-device sequence. Never raises -- failures
        at any step are caught and returned as a failed FlashResult so
        flash_many() can continue with the rest of a batch."""
        uid = None
        try:
            info = self.assert_cdc_mode(port)
            uid = info.get("uid")
            self.check_bootloader_supported(info, product_id)

            self.enter_dfu_mode(port)
            devices = self.wait_for_dfu_device(expected_count=1)
            usb_dev = devices[0]

            if verify_only:
                ok = self.verify_image(usb_dev, data, product_id)
                message = (
                    "Verify OK: flashed firmware matches image"
                    if ok else
                    "Verify FAILED: flashed firmware differs from image")
            else:
                self.erase_and_flash(
                    usb_dev, data, product_id, progress_cb=progress_cb)
                ok = self.verify_image(usb_dev, data, product_id)
                message = (
                    "Flashed and verified successfully"
                    if ok else
                    "Flash completed but verification failed")

            self.leave_and_reboot(usb_dev, port)
            return FlashResult(port=port, uid=uid, ok=ok, message=message)
        except Exception as e:
            logger.exception("Flashing %s failed", port)
            return FlashResult(
                port=port, uid=uid, ok=False, message=str(e))

    def flash_many(
            self, ports: List[str], image_path: Path,
            verify_only: bool = False,
            progress_cb: Optional[Callable[[str, int, int], None]] = None
    ) -> List[FlashResult]:
        """Flash (or verify) `image_path` on every port in `ports`,
        strictly sequentially -- DFU mode exposes no BENCHLAB-specific
        identity, so devices are never staged in DFU concurrently (there
        would be no way to tell them apart). One device's failure never
        stops the rest of the batch; all are attempted and every outcome
        is returned."""
        data = image.load_image(Path(image_path))
        results = []
        for port in ports:
            info = self.identify_device(port)
            if info is None:
                results.append(FlashResult(
                    port=port, uid=None, ok=False,
                    message=f"Could not identify a BENCHLAB device on "
                            f"{port} before starting"))
                continue

            product_id = info.get("ProductId")

            def cb(written, total, _port=port):
                if progress_cb:
                    progress_cb(_port, written, total)

            results.append(self.flash_one(
                port, data, product_id, verify_only=verify_only,
                progress_cb=cb))
        return results
