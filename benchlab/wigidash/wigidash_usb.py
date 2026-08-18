# wigidash_usb.py

import glob
import os
import re
import sys
import usb.core
import usb.util

from benchlab.wigidash.benchlab_utils import get_logger

is_linux = sys.platform.startswith("linux")
is_windows = sys.platform.startswith("win")

logger = get_logger("WigidashUsb")

_usb_backend = None
if is_windows:
    try:
        import libusb_package
        _usb_backend = libusb_package.get_libusb1_backend()
        logger.debug("Using bundled libusb-package backend for pyusb")
    except Exception as e:
        # Fall back to pyusb's normal backend discovery (e.g. a system-wide
        # libusb-1.0.dll on PATH) if libusb-package isn't installed/usable.
        logger.debug(
            "libusb-package backend unavailable, falling back to "
            f"default discovery: {e}")

_PERMISSION_ERROR_SIGNALS = (
    "access denied",
    "insufficient permissions",
    "errno 13",
    "operation not permitted",
    "permission denied",
)

_UDEV_RULES_DIRS = (
    "/etc/udev/rules.d",
    "/usr/lib/udev/rules.d",
    "/lib/udev/rules.d")


def _udev_rule_hint(vendor_id, product_id):
    """Return the udev rule text + setup steps for granting non-root
    USB access."""
    return (
        f'Create /etc/udev/rules.d/99-wigidash.rules with:\n'
        f'  SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vendor_id:04x}", '
        f'ATTR{{idProduct}}=="{product_id:04x}", TAG+="uaccess"\n'
        "Then run: sudo udevadm control --reload-rules "
        "&& sudo udevadm trigger"
    )


def _udev_rule_exists(vendor_id, product_id, rules_dirs=_UDEV_RULES_DIRS):
    """Best-effort scan of udev rule files for one referencing this VID:PID."""
    vid_hex = f"{vendor_id:04x}"
    pid_hex = f"{product_id:04x}"
    pattern = re.compile(
        rf'idVendor.{{0,5}}{vid_hex}.{{0,80}}idProduct.{{0,5}}{pid_hex}'
        rf'|idProduct.{{0,5}}{pid_hex}.{{0,80}}idVendor.{{0,5}}{vid_hex}',
        re.IGNORECASE,
    )
    for rules_dir in rules_dirs:
        for path in glob.glob(os.path.join(rules_dir, "*.rules")):
            try:
                with open(path, "r", errors="ignore") as f:
                    if pattern.search(f.read()):
                        return True
            except OSError:
                continue
    return False


def check_linux_usb_permissions(vendor_id=0x28DA, product_id=0xEF01):
    """
    Best-effort check that the current user can access the WigiDash USB
    device without root (via an existing udev rule, or an already-accessible
    device node). Linux-only diagnostic — never a hard gate.

    Returns (True, None) if access looks fine or the check is inconclusive,
    (False, <actionable message>) if it looks like it will fail.
    """
    if not is_linux:
        return True, None

    if os.geteuid() == 0:
        return True, None

    if _udev_rule_exists(vendor_id, product_id):
        return True, None

    # No matching rule found — see if a device node is nonetheless already
    # accessible (e.g. user is in the right group via some other mechanism).
    try:
        found = usb.core.find(
            idVendor=vendor_id,
            idProduct=product_id,
            find_all=True)
        for dev in found:
            bus, addr = getattr(
                dev, "bus", None), getattr(
                dev, "address", None)
            if bus is None or addr is None:
                continue
            node = f"/dev/bus/usb/{bus:03d}/{addr:03d}"
            if os.path.exists(node) and os.access(node, os.R_OK | os.W_OK):
                return True, None
    except Exception:
        pass  # inconclusive — fall through to the actionable warning

    return False, (
        "No udev rule found granting non-root access to the WigiDash "
        f"(VID:0x{vendor_id:04X}, PID:0x{product_id:04X}). "
        + _udev_rule_hint(vendor_id, product_id)
    )


def _is_permission_error(exc) -> bool:
    msg = str(exc).lower()
    return any(signal in msg for signal in _PERMISSION_ERROR_SIGNALS)


def scan_wigidash(vendor_id=0x28DA, product_id=0xEF01):
    """
    Scan for all connected Wigidash USB devices.
    Returns a list of USBDevice instances (already connected).
    """
    if is_linux:
        ok, hint = check_linux_usb_permissions(vendor_id, product_id)
        if not ok:
            logger.warning(f"WigiDash USB access may be misconfigured: {hint}")

    devices = []
    find_kwargs = {
        "idVendor": vendor_id,
        "idProduct": product_id,
        "find_all": True}
    if _usb_backend is not None:
        find_kwargs["backend"] = _usb_backend
    found = usb.core.find(**find_kwargs)
    for dev in found:
        try:
            dev.set_configuration()
            serial = usb.util.get_string(dev, dev.iSerialNumber)
            usb_dev = USBDevice(vendor_id, product_id, serial=serial)
            usb_dev.dev = dev
            logger.info(
                "Wigidash device found and configured: "
                f"VID:0x{vendor_id:04X}, PID:0x{product_id:04X}, "
                f"Serial: {serial}")
            devices.append(usb_dev)
        except usb.core.USBError as e:
            if is_linux and _is_permission_error(e):
                logger.warning(
                    f"Failed to configure device {dev}: {e}. "
                    f"{_udev_rule_hint(vendor_id, product_id)}"
                )
            else:
                logger.warning(f"Failed to configure device {dev}: {e}")
    return devices


class USBDevice:
    def __init__(self, vendor_id, product_id, serial=None, dev_obj=None):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.serial = serial
        self.dev = dev_obj

    def connect(self):
        if self.dev is None:
            raise RuntimeError("USB device object not attached!")
        try:
            self.dev.set_configuration()
            logger.info(
                f"USB device configured successfully, serial: {self.serial}")
        except usb.core.USBError as e:
            if is_linux and _is_permission_error(e):
                logger.warning(
                    f"USB set_configuration failed (ignored): {e}. "
                    f"{_udev_rule_hint(self.vendor_id, self.product_id)}"
                )
            else:
                logger.warning(f"USB set_configuration failed (ignored): {e}")

    def disconnect(self):
        """Cleanup USB resources"""
        if self.dev:
            usb.util.dispose_resources(self.dev)
            logger.info("USB device resources disposed")
            self.dev = None

    def ctrl_transfer_in(
            self,
            cmd,
            wValue=0,
            wIndex=0,
            length=0,
            timeout=2000):
        """Perform IN control transfer"""
        try:
            data = self.dev.ctrl_transfer(
                0x80 | 0x21, cmd, wValue, wIndex, length, timeout=timeout)
            logger.debug(
                f"IN transfer cmd=0x{cmd:02X}, length={length} → {data}")
            return data
        except usb.core.USBError as e:
            logger.error(f"IN transfer failed: {e}")
            raise RuntimeError(f"IN transfer failed: {e}")

    def ctrl_transfer_out(
            self,
            cmd,
            wValue=0,
            wIndex=0,
            data=None,
            timeout=2000):
        """Perform OUT control transfer"""
        try:
            self.dev.ctrl_transfer(
                0x00 | 0x21,
                cmd,
                wValue,
                wIndex,
                data,
                timeout=timeout)
            logger.debug(f"OUT transfer cmd=0x{cmd:02X}, data={data}")
        except usb.core.USBError as e:
            logger.error(f"OUT transfer failed: {e}")
            raise RuntimeError(f"OUT transfer failed: {e}")

    def bulk_write(self, endpoint, data, timeout=2000):
        """Write data via bulk transfer"""
        try:
            written = self.dev.write(endpoint, data, timeout=timeout)
            logger.debug(
                f"Bulk write to ep=0x{endpoint:02X}, len={len(data)} bytes")
            return written
        except usb.core.USBError as e:
            logger.error(f"Bulk write failed: {e}")
            raise RuntimeError(f"Bulk write failed: {e}")
