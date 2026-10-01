"""Pure-Python USB DFU 1.1 + STMicro DfuSe (AN3156) client.

Adapted from the pydfu.py implementation distributed with MicroPython/OpenMV
tooling (MIT License, Copyright (c) the MicroPython and OpenMV contributors),
trimmed to the subset of DFU/DfuSe needed to flash a single contiguous image
to an STM32's internal flash: GETSTATUS/CLRSTATUS/ABORT, DNLOAD/UPLOAD, and
the DfuSe vendor command extensions (SET ADDRESS POINTER, ERASE PAGE, LEAVE).

This module is a thin protocol layer only -- no device-selection policy, no
CLI. See flash_manager.py for the orchestration (CDC -> enter bootloader ->
DFU -> verify -> leave) and image.py for firmware image handling.
"""

import logging
import struct
import sys
import time
from collections import namedtuple

import usb.core
import usb.util

logger = logging.getLogger("benchlab.flash.dfu")

# ST's well-known ROM DFU bootloader identifier (fixed in silicon, not
# configurable firmware-side).
ST_DFU_VENDOR_ID = 0x0483
ST_DFU_PRODUCT_ID = 0xDF11

# USB DFU 1.1 (and DfuSe) request codes, bmRequestType 0x21 (host->device,
# class, interface) / 0xa1 (device->host, class, interface).
_DFU_DETACH = 0
_DFU_DNLOAD = 1
_DFU_UPLOAD = 2
_DFU_GETSTATUS = 3
_DFU_CLRSTATUS = 4
_DFU_GETSTATE = 5
_DFU_ABORT = 6

# dfuDEVICE states (bState) relevant to this client. Public names (no
# leading underscore) since flash_manager.py passes STATE_DFU_DOWNLOAD_BUSY
# to wait_while_state().
STATE_DFU_IDLE = 2
STATE_DFU_DOWNLOAD_BUSY = 4
STATE_DFU_DOWNLOAD_IDLE = 5
STATE_DFU_ERROR = 10

# DfuSe vendor commands, sent as the payload of a DNLOAD to block number 0.
_DFUSE_CMD_SET_ADDRESS_POINTER = 0x21
_DFUSE_CMD_ERASE_PAGE = 0x41

_DEFAULT_TRANSFER_SIZE = 2048  # STM32F303 DfuSe default wTransferSize.

_usb_backend = None
if sys.platform.startswith("win"):
    try:
        import libusb_package
        _usb_backend = libusb_package.get_libusb1_backend()
        logger.debug("Using bundled libusb-package backend for pyusb")
    except Exception as e:
        # Fall back to pyusb's normal backend discovery (e.g. a system-wide
        # libusb-1.0.dll on PATH) if libusb-package isn't installed/usable.
        logger.debug(
            "libusb-package backend unavailable, falling back to "
            "default discovery: %s", e)


DfuStatus = namedtuple(
    "DfuStatus", ["bStatus", "bwPollTimeout", "bState", "iString"])


class DfuError(Exception):
    """Raised when the device reports a DFU error status."""


def find_dfu_devices(vid=ST_DFU_VENDOR_ID, pid=ST_DFU_PRODUCT_ID):
    """Enumerate connected USB DFU devices matching vid/pid.

    Returns a list of usb.core.Device, not yet claimed/configured.
    """
    find_kwargs = {"idVendor": vid, "idProduct": pid, "find_all": True}
    if _usb_backend is not None:
        find_kwargs["backend"] = _usb_backend
    return list(usb.core.find(**find_kwargs))


class DfuDevice:
    """Wraps a usb.core.Device and speaks DFU 1.1 + DfuSe over it."""

    def __init__(self, dev, alt_setting=0):
        self.dev = dev
        self.alt_setting = alt_setting
        self.transfer_size = _DEFAULT_TRANSFER_SIZE

        dev.set_configuration()
        cfg = dev.get_active_configuration()
        intf = usb.util.find_descriptor(
            cfg, bInterfaceClass=0xFE, bInterfaceSubClass=1)
        if intf is None:
            raise DfuError("No DFU interface found on device")
        self.interface = intf.bInterfaceNumber

        # Explicitly claim the interface and select the alt setting -- on
        # Windows (libusb1 backend over WinUSB), control transfers reliably
        # fail with "Pipe error" (LIBUSB_ERROR_PIPE) without this, even
        # though set_configuration() alone is enough on Linux. DfuSe devices
        # also commonly expose multiple alt settings (e.g. one per flash
        # region) -- alt 0 is internal flash.
        usb.util.claim_interface(dev, self.interface)
        dev.set_interface_altsetting(
            interface=self.interface, alternate_setting=self.alt_setting)

        # DFU functional descriptor carries wTransferSize; not all devices
        # expose it through pyusb's generic descriptor parsing, so fall back
        # to the DfuSe default if it can't be read.
        try:
            extra = bytes(intf.extra_descriptors)
            if len(extra) >= 9 and extra[1] == 0x21:
                self.transfer_size = struct.unpack("<H", extra[5:7])[0]
        except Exception:
            pass

        # A previous interrupted/failed session (e.g. a crashed flash
        # attempt) can leave the device in any non-idle state --
        # dfuERROR, dfuDNLOAD-IDLE from a half-finished address-pointer
        # set, even dfuMANIFEST-WAIT-RESET. Per the DFU spec (and confirmed
        # against real hardware), only CLRSTATUS recovers from dfuERROR;
        # ABORT leaves it untouched and itself fails with a pipe error from
        # that state. ABORT does reliably return to dfuIDLE from any other
        # non-idle state. So: clear first if in error, then abort if still
        # not idle -- callers always start from a known state.
        try:
            status = self.get_status()
            if status.bState == STATE_DFU_ERROR:
                self.clear_status()
                status = self.get_status()
            if status.bState != STATE_DFU_IDLE:
                self.abort()
        except usb.core.USBError:
            pass

    def _ctrl_transfer(self, request, value, data_or_length):
        """Thin seam over usb.util.control transfers -- unit tests
        monkeypatch this method directly rather than mocking pyusb."""
        if request in (_DFU_DNLOAD,):
            bm_request_type = 0x21
        elif request in (_DFU_UPLOAD, _DFU_GETSTATUS, _DFU_GETSTATE):
            bm_request_type = 0xA1
        else:
            bm_request_type = 0x21
        return self.dev.ctrl_transfer(
            bm_request_type, request, value, self.interface, data_or_length)

    def detach(self, timeout=1000):
        self._ctrl_transfer(_DFU_DETACH, timeout, None)

    def download(self, block_num, data):
        self._ctrl_transfer(_DFU_DNLOAD, block_num, data)

    def upload(self, block_num, length):
        return bytes(self._ctrl_transfer(_DFU_UPLOAD, block_num, length))

    def get_status(self):
        raw = bytes(self._ctrl_transfer(_DFU_GETSTATUS, 0, 6))
        b_status = raw[0]
        bw_poll_timeout = raw[1] | (raw[2] << 8) | (raw[3] << 16)
        b_state = raw[4]
        i_string = raw[5]
        return DfuStatus(b_status, bw_poll_timeout, b_state, i_string)

    def clear_status(self):
        self._ctrl_transfer(_DFU_CLRSTATUS, 0, None)

    def abort(self):
        self._ctrl_transfer(_DFU_ABORT, 0, None)

    def wait_while_state(self, busy_state, timeout=10.0):
        """Poll GETSTATUS, respecting bwPollTimeout, until the device
        leaves busy_state. Raises DfuError on dfuERROR, TimeoutError if
        timeout elapses first."""
        deadline = time.monotonic() + timeout
        status = self.get_status()
        while status.bState == busy_state:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "Timed out waiting for DFU state to leave "
                    f"{busy_state}")
            time.sleep(max(status.bwPollTimeout, 1) / 1000.0)
            status = self.get_status()
        if status.bState == STATE_DFU_ERROR:
            raise DfuError(
                f"Device reported DFU error (bStatus={status.bStatus})")
        return status

    # -- DfuSe vendor extensions (AN3156) --------------------------------

    def set_address_pointer(self, addr):
        payload = bytes([_DFUSE_CMD_SET_ADDRESS_POINTER]) + struct.pack(
            "<I", addr)
        self.download(0, payload)
        self.wait_while_state(STATE_DFU_DOWNLOAD_BUSY)

    def erase_page(self, addr):
        payload = bytes([_DFUSE_CMD_ERASE_PAGE]) + struct.pack("<I", addr)
        self.download(0, payload)
        self.wait_while_state(STATE_DFU_DOWNLOAD_BUSY)

    def leave_dfu(self):
        """Trigger the DfuSe "leave DFU" transition: an empty DNLOAD after
        the address pointer is set makes the ROM bootloader jump to the
        application at that address once GETSTATUS is polled. The device
        disappears from the bus during/after this -- callers should not
        treat a transport error here as a hard failure."""
        self.download(2, b"")
        try:
            self.get_status()
        except usb.core.USBError:
            # Expected: the device may vanish mid-transaction as it jumps.
            pass
