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


class USBDevice:
    def __init__(self, vendor_id, product_id):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.dev = None

    def connect(self):
        """Find and connect to USB device"""
        logger.info(f"Connecting to USB device VID:0x{self.vendor_id:04X}, PID:0x{self.product_id:04X}")
        self.dev = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)
        if self.dev is None:
            logger.error(f"USB device not found (VID: 0x{self.vendor_id:04X}, PID: 0x{self.product_id:04X})")
            raise RuntimeError(f"Device not found (VID: 0x{self.vendor_id:04X}, PID: 0x{self.product_id:04X})")
        
        try:
            self.dev.set_configuration()
            logger.info("USB device configuration set successfully")
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

