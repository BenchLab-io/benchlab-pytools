"""
Configuration Client Abstraction

Provides unified interface for reading/writing device configuration
via both direct serial (pycore) and named pipe (Windows service) sources.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

logger = logging.getLogger("benchlab.config.client")

DISCOVERY_PIPE_NAME = "BenchlabDiscovery"


def query_named_pipe(
        pipe_name: str,
        command: str,
        payload=None,
        timeout_ms: int = 5000):
    """Send a single command to a named pipe and return the parsed JSON
    response.

    Standalone helper (not tied to a device-scoped NamedPipeConfigClient) so
    the BenchlabDiscovery pipe -- which isn't associated with any one device
    -- can be queried the same way, e.g. for ListDevices. Mirrors
    NamedPipeConfigClient._send_command's open/write/read/close sequence.

    Returns the parsed JSON response (dict or list), or None on failure.
    """
    import win32file
    import win32pipe
    import pywintypes

    path = f"\\\\.\\pipe\\{pipe_name}"
    handle = None
    try:
        win32pipe.WaitNamedPipe(path, timeout_ms)
        handle = win32file.CreateFile(
            path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None
        )

        win32file.WriteFile(handle, (command + "\n").encode("utf-8"))
        if payload is not None:
            if isinstance(payload, str):
                win32file.WriteFile(handle, (payload + "\n").encode("utf-8"))
            else:
                win32file.WriteFile(
                    handle, (json.dumps(payload) + "\n").encode("utf-8"))

        chunks = []
        while True:
            try:
                _, data = win32file.ReadFile(handle, 4096)
                if not data:
                    break
                chunks.append(data)
            except pywintypes.error as e:
                if e.winerror == 109:  # Pipe closed
                    break
                raise

        response_text = b"".join(chunks).decode(
            "utf-8", errors="replace").strip()
        if not response_text:
            return None
        return json.loads(response_text)

    except (pywintypes.error, json.JSONDecodeError) as e:
        logger.error(f"Pipe command failed on {pipe_name}: {e}")
        return None
    finally:
        if handle is not None:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass


class ConfigClient(ABC):
    """Abstract base class for device configuration clients."""

    @abstractmethod
    def get_device_info(self) -> Optional[Dict[str, Any]]:
        """Get device information."""
        pass

    @abstractmethod
    def read_device_name(self) -> Optional[str]:
        """Read device friendly name."""
        pass

    @abstractmethod
    def write_device_name(self, name: str) -> bool:
        """Write device friendly name."""
        pass

    @abstractmethod
    def read_fan_config(self, profile_id: int,
                        fan_id: int) -> Optional[Dict[str, Any]]:
        """Read fan configuration."""
        pass

    @abstractmethod
    def write_fan_config(self, profile_id: int, fan_id: int,
                         config: Dict[str, Any]) -> bool:
        """Write fan configuration."""
        pass

    @abstractmethod
    def read_rgb_config(self, profile_id: int) -> Optional[Dict[str, Any]]:
        """Read RGB configuration."""
        pass

    @abstractmethod
    def write_rgb_config(self, profile_id: int,
                         config: Dict[str, Any]) -> bool:
        """Write RGB configuration."""
        pass

    @abstractmethod
    def read_calibration(self) -> Optional[Dict[str, Any]]:
        """Read calibration data."""
        pass

    @abstractmethod
    def write_calibration(self, calibration: Dict[str, Any]) -> bool:
        """Write calibration data."""
        pass

    @abstractmethod
    def save_config(self) -> bool:
        """Save configuration to device flash."""
        pass

    @abstractmethod
    def load_config(self) -> bool:
        """Load configuration from device flash."""
        pass

    @abstractmethod
    def reset_config(self) -> bool:
        """Reset configuration to factory defaults."""
        pass

    @abstractmethod
    def close(self):
        """Close connection and cleanup resources."""
        pass


class DirectConfigClient(ConfigClient):
    """Configuration client using direct serial connection via pycore."""

    def __init__(self, port: str):
        """Initialize direct serial client.

        Args:
            port: Serial port name (e.g., 'COM4')
        """
        import serial
        from benchlab_pycore.core import read_device

        self.port = port
        self.ser = serial.Serial(port, 115200, timeout=1)

        # Get device info to determine variant
        info = read_device(self.ser)
        if not info:
            raise ConnectionError(f"Failed to read device info from {port}")

        self.product_id = info.get('ProductId')
        logger.info(
            f"Connected to device on {port}, "
            f"Product ID: 0x{self.product_id:02X}")

    def get_device_info(self) -> Optional[Dict[str, Any]]:
        """Get device information."""
        from benchlab_pycore.core import read_device, read_uid

        info = read_device(self.ser)
        if not info:
            return None

        uid = read_uid(self.ser)
        return {
            'VendorId': info.get('VendorId'),
            'ProductId': info.get('ProductId'),
            'FwVersion': info.get('FwVersion'),
            'uid': uid,
            'port': self.port,
        }

    def read_device_name(self) -> Optional[str]:
        """Read device friendly name."""
        from benchlab_pycore.core import read_name
        return read_name(self.ser)

    def write_device_name(self, name: str) -> bool:
        """Write device friendly name."""
        from benchlab_pycore.core import write_name
        return write_name(self.ser, name)

    def read_fan_config(self, profile_id: int,
                        fan_id: int) -> Optional[Dict[str, Any]]:
        """Read fan configuration."""
        from benchlab_pycore.core import read_fan_profile

        config = read_fan_profile(
            self.ser,
            fan_profile=profile_id,
            fan_id=fan_id)
        if not config:
            return None

        # Convert struct to dict
        return {
            'FanMode': config.FanMode,
            'TempSource': config.TempSource,
            'Temp': list(config.Temp),
            'Duty': list(config.Duty),
            'RampStep': config.RampStep,
            'FixedDuty': config.FixedDuty,
            'MinDuty': config.MinDuty,
            'MaxDuty': config.MaxDuty,
            'FanStop': config.FanStop,
        }

    def write_fan_config(self, profile_id: int, fan_id: int,
                         config: Dict[str, Any]) -> bool:
        """Write fan configuration."""
        from benchlab_pycore.core import write_fan_profile, FanConfigStruct

        # Convert dict to struct
        cfg = FanConfigStruct()
        cfg.FanMode = config.get('FanMode', 0)
        cfg.TempSource = config.get('TempSource', 0)

        # Handle array fields properly
        temp_list = config.get('Temp', [300, 600])
        duty_list = config.get('Duty', [30, 80])

        # Get array types from struct
        for field_name, field_type in FanConfigStruct._fields_:
            if field_name == 'Temp':
                cfg.Temp = field_type(*temp_list)
            elif field_name == 'Duty':
                cfg.Duty = field_type(*duty_list)

        cfg.RampStep = config.get('RampStep', 5)
        cfg.FixedDuty = config.get('FixedDuty', 50)
        cfg.MinDuty = config.get('MinDuty', 20)
        cfg.MaxDuty = config.get('MaxDuty', 100)
        cfg.FanStop = config.get('FanStop', 0)

        return write_fan_profile(
            self.ser,
            fan_profile=profile_id,
            fan_id=fan_id,
            config=cfg)

    def read_rgb_config(self, profile_id: int) -> Optional[Dict[str, Any]]:
        """Read RGB configuration."""
        from benchlab_pycore.core import read_rgb_profile

        config = read_rgb_profile(self.ser, rgb_profile=profile_id)
        if not config:
            return None

        return {
            'Mode': config.Mode,
            'Red': config.Red,
            'Green': config.Green,
            'Blue': config.Blue,
            'Direction': config.Direction,
            'Speed': config.Speed,
        }

    def write_rgb_config(self, profile_id: int,
                         config: Dict[str, Any]) -> bool:
        """Write RGB configuration."""
        from benchlab_pycore.core import write_rgb_profile, RGBConfigStruct

        cfg = RGBConfigStruct()
        cfg.Mode = config.get('Mode', 0)
        cfg.Red = config.get('Red', 255)
        cfg.Green = config.get('Green', 255)
        cfg.Blue = config.get('Blue', 255)
        cfg.Direction = config.get('Direction', 0)
        cfg.Speed = config.get('Speed', 50)

        return write_rgb_profile(self.ser, rgb_profile=profile_id, config=cfg)

    def _struct_to_dict(self, obj) -> Any:
        """Recursively convert ctypes structures to dictionaries."""
        import ctypes

        # Handle ctypes Structure
        if isinstance(obj, ctypes.Structure):
            result = {}
            for field_name, field_type in obj._fields_:
                value = getattr(obj, field_name)
                result[field_name] = self._struct_to_dict(value)
            return result
        # Handle arrays/lists
        elif hasattr(obj, '__len__') and not isinstance(obj, (str, bytes)):
            return [self._struct_to_dict(item) for item in obj]
        # Handle primitives
        else:
            return obj

    def read_calibration(self) -> Optional[Dict[str, Any]]:
        """Read calibration data."""
        from benchlab_pycore.core import read_calibration

        cal = read_calibration(self.ser, product_id=self.product_id)
        if not cal:
            return None

        # Convert calibration struct to dict for JSON serialization
        try:
            return self._struct_to_dict(cal)
        except Exception as e:
            logger.warning(f"Could not convert calibration to dict: {e}")
            return None

    def _dict_to_struct(self, data: Any, struct_type):
        """Recursively convert dictionaries back to ctypes structures."""

        if isinstance(data, dict):
            # Create struct instance
            struct_obj = struct_type()
            # Set each field
            for field_name, field_type in struct_type._fields_:
                if field_name in data:
                    value = data[field_name]
                    # Get the actual field type (unwrap if it's an array)
                    if hasattr(
                            field_type,
                            '_type_') and hasattr(
                            field_type,
                            '_length_'):
                        # It's an array - convert list to array
                        if isinstance(value, list):
                            array_type = field_type
                            # Check if array elements are structures
                            elem_type = field_type._type_
                            if hasattr(elem_type, '_fields_'):
                                # Array of structures
                                converted = [
                                    self._dict_to_struct(
                                        item, elem_type) for item in value]
                                setattr(
                                    struct_obj, field_name, array_type(
                                        *converted))
                            else:
                                # Array of primitives
                                setattr(
                                    struct_obj,
                                    field_name,
                                    array_type(
                                        *value))
                        else:
                            setattr(struct_obj, field_name, value)
                    elif hasattr(field_type, '_fields_'):
                        # It's a nested structure
                        setattr(
                            struct_obj,
                            field_name,
                            self._dict_to_struct(
                                value,
                                field_type))
                    else:
                        # Primitive type
                        setattr(struct_obj, field_name, value)
            return struct_obj
        else:
            return data

    def write_calibration(self, calibration: Dict[str, Any]) -> bool:
        """Write calibration data."""
        from benchlab_pycore.core import (
            write_calibration,
            CalibrationStruct,
            CalibrationStructBL2,
            BENCHLAB_BL2_PRODUCT_ID,
        )

        # Select the struct type matching the connected device's variant --
        # BL2 has 8 temp sensors vs Original's 4, so the struct sizes and
        # array lengths differ. Mirrors benchlab_pycore.core.read_calibration's
        # own product_id -> struct_type selection.
        struct_type = (
            CalibrationStructBL2
            if self.product_id == BENCHLAB_BL2_PRODUCT_ID
            else CalibrationStruct
        )

        # Reconstruct calibration struct from dict
        try:
            cal = self._dict_to_struct(calibration, struct_type)
        except Exception as e:
            logger.error(f"Failed to reconstruct calibration struct: {e}")
            return False

        return write_calibration(
            self.ser,
            calibration=cal,
            product_id=self.product_id)

    def save_config(self) -> bool:
        """Save configuration to device flash."""
        from benchlab_pycore.core.config_io import save_config
        return save_config(self.ser)

    def load_config(self) -> bool:
        """Load configuration from device flash."""
        from benchlab_pycore.core.config_io import load_config
        return load_config(self.ser)

    def reset_config(self) -> bool:
        """Reset configuration to factory defaults."""
        from benchlab_pycore.core.config_io import reset_config
        return reset_config(self.ser)

    def close(self):
        """Close serial connection."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.info(f"Closed connection to {self.port}")


class NamedPipeConfigClient(ConfigClient):
    """Configuration client using Windows named pipes."""

    def __init__(self, pipe_name: str):
        """Initialize named pipe client.

        Args:
            pipe_name: Named pipe name (e.g., 'BenchlabSensorPipe_10_ABC123')
        """
        import sys
        if not sys.platform.startswith("win"):
            raise RuntimeError(
                "NamedPipeConfigClient is only supported on Windows")

        self.pipe_name = pipe_name
        self.handle = None
        logger.info(f"Initialized named pipe client for {pipe_name}")

    def _open_pipe(self):
        """Open named pipe connection."""
        import win32file
        import win32pipe
        import pywintypes

        path = f"\\\\.\\pipe\\{self.pipe_name}"
        try:
            win32pipe.WaitNamedPipe(path, 5000)
        except pywintypes.error as e:
            raise ConnectionError(
                f"Pipe '{self.pipe_name}' not available: {e}") from e

        self.handle = win32file.CreateFile(
            path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None
        )

    def _close_pipe(self):
        """Close named pipe connection."""
        if self.handle:
            import win32file
            try:
                win32file.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None

    def _read_response(self) -> str:
        """Read full response from pipe."""
        import win32file
        import pywintypes

        chunks = []
        while True:
            try:
                result, data = win32file.ReadFile(self.handle, 4096)
                if not data:
                    break
                chunks.append(data)
            except pywintypes.error as e:
                if e.winerror == 109:  # Pipe closed
                    break
                raise

        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    def _send_command(self, command: str,
                      payload=None) -> Optional[Dict[str, Any]]:
        """Send command to pipe and get response."""
        import win32file
        import pywintypes

        try:
            self._close_pipe()
            self._open_pipe()

            # Write command
            win32file.WriteFile(self.handle, (command + "\n").encode("utf-8"))

            # Write payload if provided
            if payload is not None:
                if isinstance(payload, str):
                    win32file.WriteFile(
                        self.handle, (payload + "\n").encode("utf-8"))
                else:
                    win32file.WriteFile(
                        self.handle,
                        (json.dumps(payload) + "\n").encode("utf-8"))

            # Read response
            response_text = self._read_response()
            if not response_text:
                return None

            return json.loads(response_text)

        except (pywintypes.error, json.JSONDecodeError) as e:
            logger.error(f"Pipe command failed: {e}")
            return None
        finally:
            self._close_pipe()

    def get_device_info(self) -> Optional[Dict[str, Any]]:
        """Get device information."""
        return self._send_command("GetDeviceInfo")

    def read_device_name(self) -> Optional[str]:
        """Read device friendly name."""
        info = self.get_device_info()
        return info.get('deviceName') if info else None

    def write_device_name(self, name: str) -> bool:
        """Write device friendly name."""
        result = self._send_command("SetDeviceName", payload=name)
        return result and result.get('success', False)

    def read_fan_config(self, profile_id: int,
                        fan_id: int) -> Optional[Dict[str, Any]]:
        """Read fan configuration."""
        return self._send_command(f"ReadFanConfig:{profile_id}:{fan_id}")

    def write_fan_config(self, profile_id: int, fan_id: int,
                         config: Dict[str, Any]) -> bool:
        """Write fan configuration."""
        result = self._send_command(
            f"WriteFanConfig:{profile_id}:{fan_id}",
            payload=config)
        return result and result.get('success', False)

    def read_rgb_config(self, profile_id: int) -> Optional[Dict[str, Any]]:
        """Read RGB configuration."""
        return self._send_command(f"ReadRgbConfig:{profile_id}")

    def write_rgb_config(self, profile_id: int,
                         config: Dict[str, Any]) -> bool:
        """Write RGB configuration."""
        result = self._send_command(
            f"WriteRgbConfig:{profile_id}", payload=config)
        return result and result.get('success', False)

    def read_calibration(self) -> Optional[Dict[str, Any]]:
        """Read calibration data."""
        return self._send_command("ReadCalibration")

    def write_calibration(self, calibration: Dict[str, Any]) -> bool:
        """Write calibration data."""
        result = self._send_command("WriteCalibration", payload=calibration)
        return result and result.get('success', False)

    def save_config(self) -> bool:
        """Save configuration to device flash."""
        result = self._send_command("SaveConfig")
        return result and result.get('success', False)

    def load_config(self) -> bool:
        """Load configuration from device flash."""
        result = self._send_command("LoadConfig")
        return result and result.get('success', False)

    def reset_config(self) -> bool:
        """Reset configuration to factory defaults."""
        result = self._send_command("ResetConfig")
        return result and result.get('success', False)

    def close(self):
        """Close pipe connection."""
        self._close_pipe()
        logger.info(f"Closed named pipe connection to {self.pipe_name}")


def create_config_client(source: str, identifier: str) -> ConfigClient:
    """Factory function to create appropriate config client.

    Args:
        source: 'direct' or 'named_pipe'
        identifier: Port name for direct, pipe name for named_pipe

    Returns:
        ConfigClient instance

    Raises:
        ValueError: If source type is invalid
        ConnectionError: If connection fails
    """
    if source == 'direct':
        return DirectConfigClient(identifier)
    elif source == 'named_pipe':
        return NamedPipeConfigClient(identifier)
    else:
        raise ValueError(f"Invalid source type: {source}")
