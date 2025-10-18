"""
Serial communication helpers for BENCHLAB devices
"""

import os
import sys
import logging
import serial
import serial.tools.list_ports
from ctypes import sizeof
from benchlab.core.structures import (
    VendorDataStruct, SensorStruct, BENCHLAB_CMD
)

# --- Logger setup ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger = logging.getLogger("mqtt_bridge.serial_io")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Serial port helpers ---
def get_benchlab_ports():
    """Return all Benchlab COM ports without opening them."""
    ports = serial.tools.list_ports.comports()
    benchlab_ports = []
    for p in ports:
        hwid = p.hwid.upper()
        if "VID:PID=0483:5740" in hwid:
            benchlab_ports.append({"port": p.device})
    return benchlab_ports

def find_benchlab_devices():
    devices = []
    for port, desc, hwid in serial.tools.list_ports.comports():
        if hwid.startswith("USB VID:PID=0483:5740"):
            try:
                ser = serial.Serial(port, baudrate=115200, timeout=1)
                ser.write(b"i\n")
                uid = ser.readline().decode(errors="ignore").strip()
                fw  = ser.readline().decode(errors="ignore").strip()
                ser.close()
                logger.info("Found device on %s: UID=%s, FW=%s", port, uid, fw)
            except Exception as e:
                uid, fw = "?", "?"
                logger.warning("Failed to read device on %s: %s", port, e)
            devices.append({"port": port, "uid": uid, "fw": fw})
    return devices

def get_fleet_info():
    fleet = []
    benchlab_ports = [p["port"] for p in get_benchlab_ports()]

    for port in benchlab_ports:
        try:
            ser = serial.Serial(port, 115200, timeout=1)
            device_info = read_device(ser)
            uid = read_uid(ser)
            fleet.append({
                "port": port,
                "firmware": device_info.get('FwVersion') if device_info else "?",
                "uid": uid
            })
            ser.close()
            logger.debug("Added device to fleet: %s", uid)
        except Exception as e:
            logger.warning("Failed to get device info from %s: %s", port, e)
    return fleet

def open_serial_connection(port=None):
    if not port:
        return None
    try:
        ser = serial.Serial(port, baudrate=115200, timeout=1)
        logger.info("Opened serial port %s", port)
        return ser
    except serial.SerialException as e:
        logger.error("Could not open port %s: %s", port, e)
        return None

# --- UART helpers ---
def read_uart(ser, cmd, size):
    ser.write(cmd.toByte())
    buffer = ser.read(size)
    if len(buffer) != size:
        logger.warning("UART read incomplete: expected %d, got %d bytes", size, len(buffer))
        return None
    return buffer

def read_device(ser):
    try:
        buffer = read_uart(ser, BENCHLAB_CMD.UART_CMD_READ_VENDOR_DATA, sizeof(VendorDataStruct))
        if buffer is None:
            logger.warning("Failed to read vendor data from device")
            return None
        vendor_struct = VendorDataStruct.from_buffer_copy(buffer)
        return {
            'VendorId': vendor_struct.VendorId,
            'ProductId': vendor_struct.ProductId,
            'FwVersion': vendor_struct.FwVersion
        }
    except (serial.SerialException, OSError, ValueError) as e:
        logger.warning("Failed to read device: %s", e)
        return None

def read_sensors(ser):
    buffer = read_uart(ser, BENCHLAB_CMD.UART_CMD_READ_SENSORS, sizeof(SensorStruct))
    if buffer is None:
        logger.warning("Failed to read sensors from device")
        return None
    return SensorStruct.from_buffer_copy(buffer)

def read_uid(ser, retries=3, delay=0.2):
    for attempt in range(1, retries + 1):
        buffer = read_uart(ser, BENCHLAB_CMD.UART_CMD_READ_UID, 12)
        if buffer:
            uid_hex = buffer.hex().upper()
            logger.debug("Raw UID bytes: %s", buffer)
            logger.debug("UID read: %s", uid_hex)
            return uid_hex
        else:
            logger.warning("Attempt %d: Failed to read UID from device", attempt)
            time.sleep(delay)
    logger.error("Failed to read UID after %d attempts", retries)
    return None

