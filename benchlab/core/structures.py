"""
Core structures, enums, and constants for BENCHLAB
"""

from ctypes import *
from enum import IntEnum

# --- BENCHLAB Constants ---
BENCHLAB_VENDOR_ID = 0xEE
BENCHLAB_PRODUCT_ID = 0x10
BENCHLAB_FIRMWARE_VERSION = 0x01
SENSOR_VIN_NUM = 13
SENSOR_POWER_NUM = 11
FAN_NUM = 9


# --- Structures ---
class VendorDataStruct(Structure):
    _fields_ = [
        ('VendorId', c_uint8),
        ('ProductId', c_uint8),
        ('FwVersion', c_uint8),
    ]


class PowerSensor(Structure):
    _fields_ = [
        ('Voltage', c_int16),
        ('Current', c_int32),
        ('Power', c_int32),
    ]


class FanSensor(Structure):
    _fields_ = [
        ('Enable', c_uint8),
        ('Duty', c_uint8),
        ('Tach', c_uint16),
    ]


class SensorStruct(Structure):
    _fields_ = [
        ('Vin', c_int16 * SENSOR_VIN_NUM),
        ('Vdd', c_uint16),
        ('Vref', c_uint16),
        ('Tchip', c_int16),
        ('Ts', c_int16 * 4),
        ('Tamb', c_int16),
        ('Hum', c_int16),
        ('FanSwitchStatus', c_uint8),
        ('RGBSwitchStatus', c_uint8),
        ('RGBExtStatus', c_uint8),
        ('FanExtDuty', c_uint8),
        ('PowerReadings', PowerSensor * SENSOR_POWER_NUM),
        ('Fans', FanSensor * FAN_NUM),
    ]


# --- Enums ---
class BENCHLAB_CMD(IntEnum):
    UART_CMD_WELCOME = 0
    UART_CMD_READ_SENSORS = 1
    UART_CMD_ACTION = 2
    UART_CMD_READ_NAME = 3
    UART_CMD_WRITE_NAME = 4
    UART_CMD_READ_FAN_PROFILE = 5
    UART_CMD_WRITE_FAN_PROFILE = 6
    UART_CMD_READ_RGB = 7
    UART_CMD_WRITE_RGB = 8
    UART_CMD_READ_CALIBRATION = 9
    UART_CMD_WRITE_CALIBRATION = 10
    UART_CMD_LOAD_CALIBRATION = 11
    UART_CMD_STORE_CALIBRATION = 12
    UART_CMD_READ_UID = 13
    UART_CMD_READ_VENDOR_DATA = 14

    def toByte(self):
        return self.value.to_bytes(1, 'little')
