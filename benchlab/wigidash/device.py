import usb.core
import usb.util
import logging

logger = logging.getLogger(__name__)

class WigiDashDevice:
    """
    Minimal interface for the G.SKILL WigiDash device.
    Supports ping, UI commands, and reading device info.
    """

    VENDOR_ID = 0x28DA
    PRODUCT_ID = 0xEF01

    # Device commands
    CMD_PING = 0x00
    CMD_DEVICE_ID = 0x01
    CMD_USBIF_VERSION = 0x02
    CMD_HW_VERSION = 0x03
    CMD_FW_VERSION = 0x04
    CMD_UID = 0x05
    CMD_DEVICE_CMD = 0x06

    def __init__(self):
        self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
        if self.dev is None:
            raise ValueError("No WigiDash device found.")

        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
                logger.warning("Detached kernel driver.")
        except Exception as e:
            logger.warning(f"Could not detach kernel driver: {e}")

        try:
            self.dev.set_configuration()
            usb.util.claim_interface(self.dev, 0)
        except Exception as e:
            logger.warning(f"Could not claim interface: {e}")

        logger.info(f"Connected to WigiDash (VID=0x{self.VENDOR_ID:04X}, PID=0x{self.PRODUCT_ID:04X})")

    def ping(self) -> bool:
        try:
            self.dev.ctrl_transfer(0x40, self.CMD_PING, 0, 0, b"")
            logger.info("Ping sent successfully.")
            return True
        except usb.core.USBError as e:
            logger.error(f"Ping failed: {e}")
            return False

    def send_ui_cmd(self, cmd: int) -> bool:
        try:
            self.dev.ctrl_transfer(0x40, 0x70, cmd, 0, b"")
            logger.info(f"UI command 0x{cmd:04X} sent successfully.")
            return True
        except usb.core.USBError as e:
            logger.error(f"SendUiCmd failed: {e}")
            return False

    def read_info(self, cmd: int, length: int = 8) -> bytes:
        """
        Generic read from the device for info commands.
        CMD_DEVICE_ID, CMD_USBIF_VERSION, CMD_HW_VERSION, CMD_FW_VERSION, CMD_UID
        """
        try:
            data = self.dev.ctrl_transfer(0xC0, cmd, 0, 0, length)
            logger.info(f"Read info cmd=0x{cmd:02X}: {data}")
            return data
        except usb.core.USBError as e:
            logger.error(f"Read info failed (cmd=0x{cmd:02X}): {e}")
            return bytes()

    def close(self):
        try:
            usb.util.release_interface(self.dev, 0)
        except Exception:
            pass
        usb.util.dispose_resources(self.dev)


class DeviceFinder:
    @staticmethod
    def get_all_devices():
        try:
            dev = WigiDashDevice()
            return [dev]
        except ValueError:
            return []
