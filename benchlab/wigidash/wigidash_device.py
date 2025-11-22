# wigidash_device.py

import logging
import time
import struct
import sys
import threading
from ctypes import c_uint16, c_uint32, sizeof

from benchlab.wigidash.benchlab_utils import get_logger

logger = get_logger("WigidashDevice")

class DeviceTouchInfo:
    """Touch input information"""
    def __init__(self):
        self.Type = 0  # DeviceTouchAction
        self.X = 0
        self.Y = 0

class WigidashDevice:
    # Device configuration
    EVC2_DEVICE_ID = 0xEF01
    FW_VERSION_SUPPORT = [0x0006]
    HW_VERSION_SUPPORT = [0x00]
    DEVICE_TIMEOUT = 1000
    BUF_SIZE = 4096 // 2

    # Command codes - System
    CMD_PING = 0x00
    CMD_DEVICE_ID = 0x01
    CMD_USBIF_VERSION = 0x02
    CMD_HW_VERSION = 0x03
    CMD_FW_VERSION = 0x04
    CMD_UID = 0x05
    CMD_DEVICE_CMD = 0x06

    # Command codes - Configuration
    CMD_CONFIG_GET = 0x10
    CMD_CONFIG_SET = 0x11
    CMD_TIMEOUT_CLEAR = 0x12
    CMD_TIMEOUT_SET = 0x13
    CMD_CONFIG_STORE = 0x14
    CMD_CONFIG_RESET = 0x15

    # Command codes - SDRAM/Widget
    CMD_SDRAM_WRITE = 0x60
    CMD_SDRAM_WIDGET_WRITE = 0x61
    CMD_SDRAM_WIDGET_PTR = 0x62
    CMD_SDRAM_WIDGET_WRITE_CLEAR = 0x63
    CMD_SDRAM_WIDGET_WRITE_STATUS = 0x64

    # Command codes - Screen Configuration
    CMD_SCREENCFG_CLEAR = 0x90
    CMD_SCREENCFG_WIDGET_ADD = 0x91
    CMD_SCREENCFG_WIDGET_REMOVE = 0x92
    CMD_SCREENCFG_WIDGET_MOVE = 0x93
    CMD_SCREENCFG_WIDGET_STATUS = 0x9F

    # Command codes - Brightness
    CMD_GET_BRIGHTNESS = 0x50
    CMD_SET_BRIGHTNESS = 0x51

    # Command codes - Input
    CMD_WIDGET_GET_TOUCH = 0x33

    # Command codes - UI
    CMD_UI = 0x70
    CMD_CHANGE_PAGE = 0x20

    # Command codes - Flash
    CMD_FLASH_ERASE_SECTOR = 0x10
    CMD_FLASH_SEND_DATA = 0x11
    CMD_FLASH_WRITE_SECTOR = 0x12
    CMD_FLASH_VERIFY_SECTOR = 0x13
    CMD_FLASH_READ_CRC32 = 0x14
    CMD_FLASH_GET_RESULT = 0x15

    # Command codes - SPI Flash
    CMD_SPI_FLASH_DATA_WRITE = 0xE0
    CMD_SPI_FLASH_DATA_WRITE_STATUS = 0xE1
    CMD_SPI_FLASH_DATA_ERASE = 0xE2
    CMD_SPI_FLASH_DATA_ERASE_STATUS = 0xE3
    CMD_SPI_FLASH_DATA_WRITE_FINISHED = 0xE4

    # Flash constants
    FLASH_SECTOR_SIZE = 128 * 1024  # 128kB
    FLASH_SECTORS = 7
    FLASH_SIZE = 7 * FLASH_SECTOR_SIZE
    FIRMWARE_STATUS_TIMEOUT = 10  # seconds

    # Status codes
    HAL_OK = 0x00
    HAL_ERROR = 0x01
    HAL_BUSY = 0x02
    HAL_TIMEOUT = 0x03

    USBD_OK = 0x00
    USBD_BUSY = 0x01
    USBD_FAIL = 0x02

    FLASH_ACTION_NONE = 0
    FLASH_ACTION_ERASE = 1
    FLASH_ACTION_WRITE = 2
    FLASH_ACTION_VERIFY = 3

    FLASH_RESULT_BUSY = 0
    FLASH_RESULT_OK = 1
    FLASH_RESULT_FAIL = 2

    def __init__(self, usb_device):
        self.usb = usb_device
        self.brightness = 100
        self.transfer_aborted = False

    def init_device(self):
        """Initialize device and verify connection"""
        logger.info("Pinging Wigidash Device...")
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_PING, length=3)
        logger.info(f"Received {len(data)} bytes: {' '.join(f'{b:02X}' for b in data)}")
        return data

    def verify_device(self):
        """Verify device identity and firmware version"""
        device_id = self.get_device_id()
        fw_version = self.get_fw_version()
        
        if device_id != self.EVC2_DEVICE_ID:
            return False
        if fw_version not in self.FW_VERSION_SUPPORT:
            return False
        return True

    def ping(self):
        """Ping device to check connection"""
        try:
            data = self.usb.ctrl_transfer_in(cmd=self.CMD_PING, length=3)
            logger.debug(f"Ping response: {data}")
            return data is not None
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return False

    def get_device_id(self):
        """Get device ID"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_DEVICE_ID, length=2)
        return int.from_bytes(data[:2], byteorder='little')

    def get_hw_version(self):
        """Get hardware version"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_HW_VERSION, length=2)
        return int.from_bytes(data[:2], byteorder='little')

    def get_fw_version(self):
        """Get firmware version"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_FW_VERSION, length=2)
        return int.from_bytes(data[:2], byteorder='little')

    def get_uid(self):
        """Get device UID"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_UID, length=16)
        return data

    def reset(self, option=0):
        """Reset device with option"""
        wValue = option & 0xFFFF
        self.usb.ctrl_transfer_out(cmd=self.CMD_DEVICE_CMD, wValue=wValue, data=None)

    def get_brightness(self):
        """Get brightness level (0-100)"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_GET_BRIGHTNESS, length=1)
        self.brightness = data[0]
        return self.brightness

    def set_brightness(self, level):
        """Set brightness level (0-100)"""
        level = max(0, min(100, level))
        data = bytes([level])
        logger.debug(f"Setting brightness to {level}")
        self.usb.ctrl_transfer_out(cmd=self.CMD_SET_BRIGHTNESS, data=data)
        self.brightness = level

    def add_widget(self, widget_config, page=0, widget_id=0):
        """Add a widget to the device"""
        wValue = (page << 8) | widget_id
        widget_bytes = bytes(widget_config)
        logger.debug(f"Adding widget page={page}, id={widget_id}, size={len(widget_bytes)} bytes")
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_SCREENCFG_WIDGET_ADD,
            wValue=wValue,
            data=widget_bytes,
            timeout=2000
        )
        time.sleep(0.1)

    def remove_widget(self, page=0, widget_id=0):
        """Remove a widget from the device"""
        wValue = (page << 8) | widget_id
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_SCREENCFG_WIDGET_REMOVE,
            wValue=wValue,
            data=None
        )

    def move_widget(self, x, y, page=0, widget_id=0):
        """Move a widget to new position"""
        wValue = (page << 8) | widget_id
        data = struct.pack('<HH', x & 0xFFFF, y & 0xFFFF)
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_SCREENCFG_WIDGET_MOVE,
            wValue=wValue,
            data=data
        )

    def clear_page(self, page=0):
        """Clear all widgets from a page"""
        wValue = page & 0xFFFF
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_SCREENCFG_CLEAR,
            wValue=wValue,
            data=None
        )

    def change_page(self, page=0):
        """Change the displayed page"""
        wValue = self.CMD_CHANGE_PAGE + page
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_UI,
            wValue=wValue,
            data=None,
            timeout=2000
        )

    def write_widget_color(self, color, widget_width=1016, widget_height=592, page=0, widget_id=0):
        """Write a solid color to a widget"""
        # Create color buffer
        color_buffer = (c_uint16 * (widget_width * widget_height))()
        for i in range(widget_width * widget_height):
            color_buffer[i] = color

        # Create config buffer (offset + length)
        config_buffer = bytearray(8)
        offset = 0
        length = sizeof(color_buffer)

        struct.pack_into('<II', config_buffer, 0, offset, length)

        # Send configuration
        wValue = (page << 8) | widget_id
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_SDRAM_WIDGET_WRITE,
            wValue=wValue,
            data=config_buffer,
            timeout=2000
        )

        # Send color data
        self.usb.bulk_write(0x01, bytearray(color_buffer), timeout=2000)

    def write_to_widget(self, page, widget_id, offset, data):
        """Write arbitrary data to widget at offset"""
        logger.debug(f"Writing to widget page={page}, id={widget_id}, offset={offset}, length={len(data)}")
        # Create config buffer (offset + length)
        config_buffer = bytearray(8)
        struct.pack_into('<II', config_buffer, 0, offset, len(data))

        # Send configuration
        wValue = (page << 8) | widget_id
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_SDRAM_WIDGET_WRITE,
            wValue=wValue,
            data=config_buffer,
            timeout=2000
        )

        # Send data
        wrote = self.usb.bulk_write(0x01, bytearray(data), timeout=2000)
        logger.debug(f"Wrote {wrote}/{len(data)} bytes")

        if wrote != len(data):
            logger.warning("Data write incomplete, clearing widget")
            # Clear on error
            self.usb.ctrl_transfer_out(
                cmd=self.CMD_SDRAM_WIDGET_WRITE_CLEAR,
                wValue=(0 << 8 | 0),
                data=None
            )
        
        return wrote

    def get_click_info(self):
        """Get touch input information"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_WIDGET_GET_TOUCH, length=8)
        
        touch_info = DeviceTouchInfo()
        if len(data) >= 8:
            touch_info.Type = data[0]
            touch_info.X = int.from_bytes(data[2:4], byteorder='little', signed=True)
            touch_info.Y = int.from_bytes(data[4:6], byteorder='little', signed=True)
            screen_state = data[6]
            sleep_state = data[7] != 0
            
            return touch_info, screen_state, sleep_state
        
        return touch_info, 0, False

    def get_config(self):
        """Get device configuration"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_CONFIG_GET, length=64)
        
        if len(data) >= 40:
            backlight = data[1]
            screen_timeout = int.from_bytes(data[2:6], byteorder='little')
            nickname = data[6:38].decode('utf-8', errors='ignore').rstrip('\x00')
            vcom = int.from_bytes(data[38:40], byteorder='little')
            avdd = int.from_bytes(data[40:42], byteorder='little')
            display_offset_x = data[42] if len(data) > 42 else 0
            display_offset_y = data[43] if len(data) > 43 else 0
            
            return {
                'backlight': backlight,
                'screen_timeout': screen_timeout,
                'nickname': nickname,
                'vcom': vcom,
                'avdd': avdd,
                'display_offset_x': display_offset_x,
                'display_offset_y': display_offset_y
            }
        
        return None

    def set_config(self, backlight=None, screen_timeout=None, nickname=None, display_offset_x=None, display_offset_y=None):
        """Set device configuration"""
        # Get current config first
        config = self.get_config()
        if config is None:
            return False
        
        # Update with provided values
        if backlight is not None:
            config['backlight'] = max(0, min(100, backlight))
        if screen_timeout is not None:
            config['screen_timeout'] = screen_timeout
        if nickname is not None:
            config['nickname'] = nickname[:32]
        if display_offset_x is not None:
            config['display_offset_x'] = display_offset_x
        if display_offset_y is not None:
            config['display_offset_y'] = display_offset_y
        
        # Pack and send config
        config_data = bytearray(64)
        config_data[0] = 1  # Version
        config_data[1] = config['backlight']
        struct.pack_into('<I', config_data, 2, config['screen_timeout'])
        nickname_bytes = config['nickname'].encode('utf-8')[:32]
        config_data[6:6+len(nickname_bytes)] = nickname_bytes
        struct.pack_into('<H', config_data, 38, config['vcom'])
        struct.pack_into('<H', config_data, 40, config['avdd'])
        config_data[42] = config['display_offset_x']
        config_data[43] = config['display_offset_y']
        
        # Calculate CRC16
        crc = self.crc16_calc(config_data, 44)
        struct.pack_into('<H', config_data, 44, crc)
        
        self.usb.ctrl_transfer_out(cmd=self.CMD_CONFIG_SET, data=config_data)
        return True

    def store_config(self):
        """Store configuration to device"""
        self.usb.ctrl_transfer_out(cmd=self.CMD_CONFIG_STORE, data=None)

    def reset_config(self):
        """Reset configuration to defaults"""
        self.usb.ctrl_transfer_out(cmd=self.CMD_CONFIG_RESET, data=None)

    def clear_screen_timeout(self):
        """Clear screen timeout"""
        self.usb.ctrl_transfer_out(cmd=self.CMD_TIMEOUT_CLEAR, data=None)

    def snooze_device(self):
        """Snooze device (set timeout)"""
        self.usb.ctrl_transfer_out(cmd=self.CMD_TIMEOUT_SET, data=None)

    def send_ui_cmd(self, cmd):
        """Send generic UI command"""
        wValue = cmd & 0xFFFF
        self.usb.ctrl_transfer_out(cmd=self.CMD_UI, wValue=wValue, data=None)

    def erase_firmware(self):
        """Erase firmware sectors"""
        self.usb.ctrl_transfer_out(cmd=self.CMD_FLASH_ERASE_SECTOR, wValue=0, data=None)
        
        # Wait for completion
        timeout = self.FIRMWARE_STATUS_TIMEOUT
        while timeout > 0:
            time.sleep(1)
            data = self.usb.ctrl_transfer_in(cmd=self.CMD_FLASH_GET_RESULT, length=2)
            if len(data) >= 2:
                action = data[0]
                status = data[1]
                if action == self.FLASH_ACTION_NONE and status == self.FLASH_RESULT_OK:
                    return True
            timeout -= 1
        
        return False

    def write_firmware_sector(self, sector, sector_data):
        """Write a firmware sector"""
        if sector >= self.FLASH_SECTORS:
            return False
        if len(sector_data) != self.FLASH_SECTOR_SIZE:
            return False

        # Prepare to receive sector data
        self.usb.ctrl_transfer_out(cmd=self.CMD_FLASH_SEND_DATA, data=None)

        # Send sector data
        wrote = self.usb.bulk_write(0x01, bytearray(sector_data))
        if wrote != len(sector_data):
            return False

        # Write to flash with CRC
        crc32 = self.crc32_calc(sector_data)
        crc_buf = struct.pack('<I', crc32)
        self.usb.ctrl_transfer_out(
            cmd=self.CMD_FLASH_WRITE_SECTOR,
            wValue=sector,
            data=crc_buf
        )

        # Wait for completion
        timeout = self.FIRMWARE_STATUS_TIMEOUT
        while timeout > 0:
            time.sleep(0.5)
            data = self.usb.ctrl_transfer_in(cmd=self.CMD_FLASH_GET_RESULT, length=2)
            if len(data) >= 2:
                action = data[0]
                status = data[1]
                if action == self.FLASH_ACTION_NONE and status == self.FLASH_RESULT_OK:
                    return True
            timeout -= 1

        return False

    def verify_firmware_sector(self, sector, sector_data):
        """Verify firmware sector by CRC32"""
        crc32 = self.crc32_calc(sector_data)
        
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_FLASH_READ_CRC32, wValue=sector, length=4)
        if len(data) >= 4:
            flash_crc = int.from_bytes(data[:4], byteorder='little')
            return flash_crc == crc32
        
        return False

    def write_firmware(self, firmware_data):
        """Write complete firmware (896kB = 7 sectors)"""
        if len(firmware_data) != self.FLASH_SECTOR_SIZE * 7:
            return False

        # Erase flash
        if not self.erase_firmware():
            return False

        # Write each sector
        for sector in range(7):
            sector_data = firmware_data[sector * self.FLASH_SECTOR_SIZE:(sector + 1) * self.FLASH_SECTOR_SIZE]
            if not self.write_firmware_sector(sector, sector_data):
                return False

        return True

    def check_app_mode(self):
        """Check if device is in app mode or bootloader mode"""
        data = self.usb.ctrl_transfer_in(cmd=self.CMD_PING, length=3)
        
        if len(data) >= 3:
            if data[0] == ord('W') and data[1] == ord('D'):
                return True  # App mode
            elif data[0] == ord('B') and data[1] == ord('L'):
                return False  # Bootloader mode
        
        return None

    @staticmethod
    def crc16_calc(data, length):
        """Calculate CRC16"""
        crc = 0xFFFF
        for i in range(length):
            x = (crc >> 8) ^ data[i]
            x ^= x >> 4
            crc = ((crc << 8) ^ (x << 12) ^ (x << 5) ^ x) & 0xFFFF
        return crc

    @staticmethod
    def crc32_calc(data):
        """Calculate CRC32 using simple algorithm"""
        crc = 0xFFFFFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xEDB88320
                else:
                    crc >>= 1
        return crc ^ 0xFFFFFFFF