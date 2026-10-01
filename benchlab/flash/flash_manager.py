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

from benchlab.config.config_client import create_config_client
from benchlab.config.config_manager import ConfigManager
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
    def __init__(self, source: str = "direct",
                 base_url: Optional[str] = None,
                 token: Optional[str] = None):
        """
        Args:
            source: 'direct' (raw serial, the only mode validated against
                real hardware), 'named_pipe', or 'service_http'. For
                named_pipe/service_http, the C# BenchLab service owns the
                serial port and must be the one to trigger the bootloader
                jump (via its existing SendBootloader pipe command / POST
                /device/{uid}/bootloader) and release the port -- this
                tool then takes over the bare USB device directly via
                pyusb, identically to the direct-mode flow.
            base_url: C# service base URL (service_http only).
            token: Optional X-Benchlab-Token (service_http only).
        """
        self.source = source
        self.base_url = base_url
        self.token = token

    def discover_devices(self) -> List[dict]:
        if self.source == "direct":
            return discover_devices()
        return ConfigManager(
            source=self.source, base_url=self.base_url,
            token=self.token).discover_devices()

    def identify_device(self, identifier: str) -> Optional[dict]:
        """Read vendor/product/firmware info for `identifier` (a COM port
        for 'direct', a pipe name for 'named_pipe', a device UID for
        'service_http'), and gate on it being a recognized BENCHLAB
        device. Returns None if the device can't be reached/read, or isn't
        recognized (e.g. already in DFU mode, which exposes neither a CDC
        port nor a named pipe).

        Always returns the same shape regardless of source --
        {VendorId, ProductId, FwVersion, uid} -- normalizing named_pipe/
        service_http's camelCase ConfigClient fields to match direct
        mode's, so every other FlashManager method stays source-agnostic.
        """
        if self.source == "direct":
            return self._identify_direct(identifier)
        return self._identify_via_config_client(identifier)

    def _identify_direct(self, port: str) -> Optional[dict]:
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

    def _identify_via_config_client(
            self, identifier: str) -> Optional[dict]:
        try:
            client = create_config_client(
                self.source, identifier, base_url=self.base_url,
                token=self.token)
        except Exception as e:
            logger.debug("Could not connect to %s: %s", identifier, e)
            return None

        try:
            info = client.get_device_info()
        finally:
            client.close()

        if not info:
            return None
        product_id = info.get("productId")
        if product_id not in _KNOWN_PRODUCT_IDS:
            return None
        return {
            "VendorId": info.get("vendorId"),
            "ProductId": product_id,
            "FwVersion": info.get("firmwareVersion"),
            "uid": info.get("guid") or info.get("uid"),
            "port": info.get("port"),
        }

    def assert_cdc_mode(self, identifier: str) -> dict:
        """Confirm `identifier` identifies a BENCHLAB device currently
        reachable over its normal control channel (CDC for direct, the
        pipe/HTTP API otherwise) -- i.e. not already in DFU mode. Raises
        ConnectionError with an actionable message otherwise."""
        info = self.identify_device(identifier)
        if info is None:
            raise ConnectionError(
                f"Could not identify a BENCHLAB device at {identifier}. "
                "It may already be in DFU mode (e.g. from an interrupted "
                "previous flash attempt) or may not be a BENCHLAB device. "
                "Try --list, or power-cycle the device and reconnect.")
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

    def enter_dfu_mode(
            self, identifier: str, port: Optional[str] = None,
            timeout: float = 5.0) -> None:
        """Send the bootloader-jump command and wait for the physical CDC
        port to disappear (the firmware defers the actual jump to its next
        100ms task tick). `port` is the OS-level COM port to poll for
        disappearance -- for 'direct' it's the same as `identifier`; for
        'named_pipe'/'service_http' it must be looked up from the device
        info first (the pipe/HTTP identifier isn't a COM port), since USB
        enumeration is physical and OS-level regardless of which control
        channel sent the jump command.
        """
        if self.source == "direct":
            port = identifier
            ser = open_serial_connection(identifier)
            if ser is None:
                raise ConnectionError(
                    f"Could not open {identifier} to enter DFU mode")
            try:
                config_io.enter_bootloader(ser)
            finally:
                ser.close()
        else:
            if port is None:
                raise ValueError(
                    "port is required for named_pipe/service_http -- look "
                    "it up from identify_device()'s result first")
            client = create_config_client(
                self.source, identifier, base_url=self.base_url,
                token=self.token)
            try:
                if not client.enter_bootloader():
                    raise ConnectionError(
                        f"The device at {identifier} rejected the "
                        "bootloader jump")
            finally:
                client.close()

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
        the device's physical CDC port to reappear. No separate vendor
        command is needed: the freshly-flashed app's normal boot path
        re-initializes USB CDC on its own.

        For 'direct', re-identifies the device by port once it reappears
        and returns the fresh info. For 'named_pipe'/'service_http', only
        confirms the port physically came back -- the C# service owns
        re-detecting/re-opening it on its own schedule, and the
        pipe-name/UID used to identify the device before flashing may not
        be immediately valid again, so this doesn't attempt to re-query
        through the service here.
        """
        dev = dfu.DfuDevice(usb_dev)
        dev.set_address_pointer(image.FLASH_BASE)
        dev.leave_dfu()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ports = {p.get("port") for p in get_benchlab_ports()}
            if port in ports:
                if self.source == "direct":
                    return self.identify_device(port)
                return None
            time.sleep(0.2)

        logger.warning(
            "%s did not reappear within %.0fs after leaving DFU mode -- "
            "this may just be slow re-enumeration, verify manually.",
            port, timeout)
        return None

    def _erase_verify_leave(
            self, usb_dev, data: bytes, product_id: int, port: Optional[str],
            verify_only: bool,
            progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> tuple:
        """Shared tail of the flashing sequence once a usb_dev and
        product_id are known, used by both flash_one (CDC-driven) and
        flash_bare_dfu (device already manually jumpered into DFU)."""
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

        if port is not None:
            self.leave_and_reboot(usb_dev, port)
        else:
            # No CDC port to wait for -- just trigger the DfuSe leave
            # sequence so the device reboots into the newly-flashed app.
            dfu.DfuDevice(usb_dev).leave_dfu()
        return ok, message

    def flash_one(self, identifier: str, data: bytes, product_id: int,
                  verify_only: bool = False,
                  progress_cb: Optional[Callable[[int, int], None]] = None
                  ) -> FlashResult:
        """Run the full single-device sequence for a device currently
        reachable over its normal control channel. `identifier` is a COM
        port for 'direct', a pipe name for 'named_pipe', or a device UID
        for 'service_http'. Never raises -- failures at any step are
        caught and returned as a failed FlashResult so flash_many() can
        continue with the rest of a batch."""
        uid = None
        try:
            info = self.assert_cdc_mode(identifier)
            uid = info.get("uid")
            self.check_bootloader_supported(info, product_id)

            # The physical COM port to watch disappear/reappear -- for
            # 'direct' the identifier already is the port; for
            # named_pipe/service_http it comes from the device info.
            port = identifier if self.source == "direct" else info.get(
                "port")
            if port is None:
                raise ConnectionError(
                    f"Device info for {identifier} did not include a "
                    "physical port to monitor")

            self.enter_dfu_mode(identifier, port=port)
            devices = self.wait_for_dfu_device(expected_count=1)
            usb_dev = devices[0]

            ok, message = self._erase_verify_leave(
                usb_dev, data, product_id, port, verify_only, progress_cb)
            return FlashResult(
                port=identifier, uid=uid, ok=ok, message=message)
        except Exception as e:
            logger.exception("Flashing %s failed", identifier)
            return FlashResult(
                port=identifier, uid=uid, ok=False, message=str(e))

    def flash_bare_dfu(
            self, product_id: int, data: bytes, verify_only: bool = False,
            progress_cb: Optional[Callable[[int, int], None]] = None,
            timeout: float = 10.0) -> FlashResult:
        """Flash a device that's already sitting in USB DFU mode with no
        CDC/serial port at all -- e.g. an old BL1 unit manually jumpered
        into its bootloader via BOOT0, since CMD_BOOTLOADER doesn't exist
        on firmware below MIN_BOOTLOADER_FW_VERSION.

        There's no way to read VendorId/ProductId/FwVersion from a bare
        DFU device (that's only exposed over the normal CDC protocol), so
        the caller must supply `product_id` explicitly -- see
        flash_tool.py's --dfu/--variant handling, which prompts for it
        interactively when not given on the command line.

        Requires exactly one DFU device to be present (same reasoning as
        flash_many's sequential-only policy: DFU mode exposes no
        BENCHLAB-specific identity, so multiple bare DFU devices can't be
        disambiguated)."""
        try:
            devices = self.wait_for_dfu_device(
                expected_count=1, timeout=timeout)
            usb_dev = devices[0]
            ok, message = self._erase_verify_leave(
                usb_dev, data, product_id, None, verify_only, progress_cb)
            return FlashResult(
                port="<bare DFU>", uid=None, ok=ok, message=message)
        except Exception as e:
            logger.exception("Flashing bare DFU device failed")
            return FlashResult(
                port="<bare DFU>", uid=None, ok=False, message=str(e))

    def flash_many(
            self, identifiers: List[str], image_path: Path,
            verify_only: bool = False,
            progress_cb: Optional[Callable[[str, int, int], None]] = None
    ) -> List[FlashResult]:
        """Flash (or verify) `image_path` on every device in `identifiers`
        (COM ports for 'direct', pipe names for 'named_pipe', device UIDs
        for 'service_http'), strictly sequentially -- DFU mode exposes no
        BENCHLAB-specific identity, so devices are never staged in DFU
        concurrently (there would be no way to tell them apart). One
        device's failure never stops the rest of the batch; all are
        attempted and every outcome is returned."""
        data = image.load_image(Path(image_path))
        results = []
        for identifier in identifiers:
            info = self.identify_device(identifier)
            if info is None:
                results.append(FlashResult(
                    port=identifier, uid=None, ok=False,
                    message=f"Could not identify a BENCHLAB device at "
                            f"{identifier} before starting"))
                continue

            product_id = info.get("ProductId")

            def cb(written, total, _identifier=identifier):
                if progress_cb:
                    progress_cb(_identifier, written, total)

            results.append(self.flash_one(
                identifier, data, product_id, verify_only=verify_only,
                progress_cb=cb))
        return results
