import logging
from .device import DeviceFinder

logger = logging.getLogger(__name__)

def run_wigidash():
    devices = DeviceFinder.get_all_devices()
    if not devices:
        logger.error("No WigiDash devices found.")
        return

    wigi = devices[0]
    logger.info(f"Found device: VID=0x{wigi.VENDOR_ID:04X}, PID=0x{wigi.PRODUCT_ID:04X}")

    # Ping the device
    logger.info("Pinging device...")
    if not wigi.ping():
        logger.error("Ping failed. Cannot communicate with device.")
        return
    logger.info("Ping OK.")

    # Read device info
    cmds = {
        "Device ID": wigi.CMD_DEVICE_ID,
        "USB Interface Version": wigi.CMD_USBIF_VERSION,
        "Hardware Version": wigi.CMD_HW_VERSION,
        "Firmware Version": wigi.CMD_FW_VERSION,
        "UID": wigi.CMD_UID,
    }

    for name, cmd in cmds.items():
        data = wigi.read_info(cmd)
        if data:
            hex_str = " ".join(f"{b:02X}" for b in data)
            logger.info(f"{name}: {hex_str}")
        else:
            logger.error(f"Failed to read {name}.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_wigidash()
