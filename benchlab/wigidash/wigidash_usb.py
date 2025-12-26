# wigidash_usb.py

import logging
import os
import sys
import usb.core
import usb.util

from benchlab.wigidash.benchlab_utils import get_logger

is_linux = sys.platform.startswith("linux")
if is_linux and os.geteuid() != 0:
    print("ERROR: WigiDash USB access on Linux requires root privileges. Please run with sudo.")
    sys.exit(1)

logger = get_logger("WigidashUsb")

def scan_wigidash(vendor_id=0x28DA, product_id=0xEF01):
    """
    Scan for all connected Wigidash USB devices.
    Returns a list of USBDevice instances (already connected).
    """
    devices = []
    found = usb.core.find(idVendor=vendor_id, idProduct=product_id, find_all=True)
    for dev in found:
        try:
            dev.set_configuration()
            serial = usb.util.get_string(dev, dev.iSerialNumber)
            usb_dev = USBDevice(vendor_id, product_id, serial=serial)
            usb_dev.dev = dev
            logger.info(f"Wigidash device found and configured: VID:0x{vendor_id:04X}, PID:0x{product_id:04X}, Serial: {serial}")
            devices.append(usb_dev)
        except usb.core.USBError as e:
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
            logger.info(f"USB device configured successfully, serial: {self.serial}")
        except usb.core.USBError as e:
            logger.warning(f"USB set_configuration failed (ignored): {e}")

    def disconnect(self):
        """Cleanup USB resources"""
        if self.dev:
            usb.util.dispose_resources(self.dev)
            logger.info("USB device resources disposed")
            self.dev = None

    def ctrl_transfer_in(self, cmd, wValue=0, wIndex=0, length=0, timeout=2000):
        """Perform IN control transfer"""
        try:
            data = self.dev.ctrl_transfer(0x80 | 0x21, cmd, wValue, wIndex, length, timeout=timeout)
            logger.debug(f"IN transfer cmd=0x{cmd:02X}, length={length} → {data}")
            return data
        except usb.core.USBError as e:
            logger.error(f"IN transfer failed: {e}")
            raise RuntimeError(f"IN transfer failed: {e}")

    def ctrl_transfer_out(self, cmd, wValue=0, wIndex=0, data=None, timeout=2000):
        """Perform OUT control transfer"""
        try:
            self.dev.ctrl_transfer(0x00 | 0x21, cmd, wValue, wIndex, data, timeout=timeout)
            logger.debug(f"OUT transfer cmd=0x{cmd:02X}, data={data}")
        except usb.core.USBError as e:
            logger.error(f"OUT transfer failed: {e}")
            raise RuntimeError(f"OUT transfer failed: {e}")

    def bulk_write(self, endpoint, data, timeout=2000):
        """Write data via bulk transfer"""
        try:
            written = self.dev.write(endpoint, data, timeout=timeout)
            logger.debug(f"Bulk write to ep=0x{endpoint:02X}, len={len(data)} bytes")
            return written
        except usb.core.USBError as e:
            logger.error(f"Bulk write failed: {e}")
            raise RuntimeError(f"Bulk write failed: {e}")

