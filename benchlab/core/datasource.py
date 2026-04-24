"""
Data Source Abstraction Layer for BENCHLAB tools

Provides a unified interface for tools to consume telemetry data from:
- Direct serial connection (pycore)
- FastAPI server
- MQTT broker
"""

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

logger = logging.getLogger("benchlab.core.datasource")

# Import retry utilities for robust connection handling
from .retry import retry, RetryPolicy


class DataSource(ABC):
    """Abstract base class for all data sources.
    
    All data sources must implement this interface to provide
    a consistent API for tools to consume telemetry data.
    """
    
    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the data source.
        
        Returns:
            True if connection successful, False otherwise
        """
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to the data source."""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to the data source.
        
        Returns:
            True if connected, False otherwise
        """
        pass
    
    @abstractmethod
    def list_devices(self) -> List[Dict[str, Any]]:
        """Get list of available devices.
        
        Returns:
            List of device info dictionaries with at least 'uid' and 'port' keys
        """
        pass
    
    @abstractmethod
    def get_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get latest telemetry data for a device.
        
        Args:
            uid: Device unique identifier
            
        Returns:
            Dictionary of sensor data, or None if unavailable
        """
        pass
    
    @abstractmethod
    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get device information (firmware, etc.).
        
        Args:
            uid: Device unique identifier
            
        Returns:
            Dictionary with device info, or None if unavailable
        """
        pass
    
    @property
    @abstractmethod
    def source_type(self) -> str:
        """Return the type of data source (e.g., 'direct', 'fastapi', 'mqtt')."""
        pass


class DirectDataSource(DataSource):
    """Data source that connects directly to serial port via pycore.
    
    This is used when running a single tool that can exclusively claim
    the serial port.
    """
    
    def __init__(self, *, config: Optional["SerialConfig"] = None, port: Optional[str] = None, poll_interval: float = 1.0):
        """Initialize direct data source.

        Parameters are now wrapped in a :class:`SerialConfig` model for
        validation.  For backward compatibility the original ``port`` and
        ``poll_interval`` arguments are still accepted and will be used to
        construct a temporary ``SerialConfig`` if ``config`` is omitted.
        """
        # Lazy import to avoid circular dependency
        from .config import SerialConfig

        if config is None:
            config = SerialConfig(port=port, poll_interval=poll_interval)
        self.port = config.port
        self.poll_interval = config.poll_interval
        self._connected = False
        self._ser = None
        self._lock = threading.Lock()
        self._latest_data: Dict[str, Dict[str, Any]] = {}
        self._device_info: Dict[str, Dict[str, Any]] = {}
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ser_handles: Dict[str, Any] = {}
        
        # Import pycore
        try:
            from benchlab_pycore.core import (
                read_sensors, read_device, read_uid, 
                translate_sensor_struct, get_benchlab_ports
            )
            from benchlab_pycore.core.serial_io import open_serial_connection
            self._pycore = {
                'read_sensors': read_sensors,
                'read_device': read_device,
                'read_uid': read_uid,
                'translate_sensor_struct': translate_sensor_struct,
                'get_benchlab_ports': get_benchlab_ports,
                'open_serial_connection': open_serial_connection,
            }
        except ImportError as e:
            logger.error(f"Failed to import benchlab_pycore: {e}")
            self._pycore = None
    
    @retry(RetryPolicy(max_retries=3, backoff_factor=2.0, base_delay=0.5, allowed_exceptions=(Exception,)))
    def connect(self) -> bool:
        if self._pycore is None:
            return False
        if self._connected:
            return True

        ports = self._pycore['get_benchlab_ports']()
        if self.port is not None:
            # Caller specified a port — only connect to that one
            ports = [p for p in ports if p.get('port') == self.port]

        if not ports:
            logger.error("No BenchLab devices found")
            return False

        # Open a connection to every detected port
        for port_info in ports:
            port = port_info.get('port')
            if not port:
                continue
            try:
                ser = self._pycore['open_serial_connection'](port)
                if not ser:
                    continue
                uid = self._pycore['read_uid'](ser)
                info = self._pycore['read_device'](ser) or {}
                if uid:
                    self._device_info[uid] = {**info, 'uid': uid, 'port': port}
                    self._ser_handles[uid] = ser   # store per-uid handle
                    logger.info(f"Connected to device {uid} on {port}")
                else:
                    ser.close()
            except Exception as e:
                logger.debug(f"Failed to probe {port}: {e}")

        if not self._device_info:
            logger.error("Could not connect to any BenchLab device")
            return False

        # Keep self._ser pointing at the first device for legacy callers
        self.port = next(iter(self._device_info.values()))['port']
        first_uid = next(iter(self._device_info))
        self._ser = self._ser_handles[first_uid]

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        self._connected = True
        return True
    
    def disconnect(self) -> None:
        """Disconnect from the serial device."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._connected = False
        logger.info("Disconnected from device")
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected
    
    def list_devices(self) -> List[Dict[str, Any]]:
        """List available devices."""
        if self._pycore is None:
            return []
        
        # If already connected, return the device we're connected to
        # without trying to open the port again (which would fail)
        if self._connected and self._device_info:
            devices = []
            for uid, info in self._device_info.items():
                devices.append({
                    'uid': uid,
                    'port': info.get('port', self.port),
                    'firmware': info.get('FwVersion', '?'),
                })
            return devices
        
        # Not connected - probe available ports
        devices = []
        ports = self._pycore['get_benchlab_ports']()
        for port_info in ports:
            port = port_info.get('port')
            if port:
                try:
                    ser = self._pycore['open_serial_connection'](port)
                    if ser:
                        uid = self._pycore['read_uid'](ser)
                        info = self._pycore['read_device'](ser)
                        ser.close()
                        if uid:
                            devices.append({
                                'uid': uid,
                                'port': port,
                                'firmware': info.get('FwVersion', '?') if info else '?',
                            })
                except Exception as e:
                    logger.debug(f"Failed to probe {port}: {e}")
        return devices
    
    def get_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get latest telemetry data."""
        with self._lock:
            return self._latest_data.get(uid, None)
    
    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get device info."""
        return self._device_info.get(uid, None)
    
    @property
    def source_type(self) -> str:
        return "direct"
    
    def _worker_loop(self):
        while not self._stop_event.is_set():
            for uid, ser in list(self._ser_handles.items()):
                try:
                    sensors = self._pycore['read_sensors'](ser)
                    if sensors:
                        data = self._pycore['translate_sensor_struct'](sensors)
                        data['timestamp'] = datetime.now(UTC).isoformat()
                        with self._lock:
                            self._latest_data[uid] = data
                except Exception as e:
                    logger.warning(f"Error reading sensors from {uid}: {e}")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    self._ser_handles.pop(uid, None)
            time.sleep(self.poll_interval)


class FastAPIDataSource(DataSource):
    """Data source that connects to a FastAPI server.
    
    This is used when multiple tools need to share data from a single
    serial connection managed by the FastAPI server.
    """

    def __init__(self, *, config: Optional["FastAPIConfig"] = None, base_url: str = "http://127.0.0.1:8000", timeout: float = 5.0):
        """Initialize FastAPI data source.

        Parameters can be supplied via a :class:`FastAPIConfig` instance for
        validation.  For backward compatibility the original ``base_url`` and
        ``timeout`` arguments are still accepted.
        """
        # Lazy import to avoid circular dependency
        from .config import FastAPIConfig

        if config is None:
            config = FastAPIConfig(base_url=base_url, timeout=timeout)
        self.base_url = config.base_url.rstrip('/')
        self.timeout = config.timeout
        self._connected = False
        self._session = None

        try:
            import requests
            self._requests = requests
        except ImportError:
            logger.error("requests library not available")
            self._requests = None
    
    @retry(RetryPolicy(max_retries=3, backoff_factor=2.0, base_delay=0.5, allowed_exceptions=(Exception,)))
    def connect(self) -> bool:
        """Connect to the FastAPI server and verify device access with retry logic."""
        if self._requests is None:
            return False
        
        try:
            self._session = self._requests.Session()
            # Test connection with health check
            response = self._session.get(
                f"{self.base_url}/health", 
                timeout=self.timeout
            )
            if response.status_code == 200:
                health = response.json()
                logger.info(f"FastAPI health: {health}")
                # Server is running - connected flag is about server availability,
                # not device availability. Device check happens in list_devices().
                self._connected = True
                logger.info(f"Connected to FastAPI server at {self.base_url}")
                return True
            else:
                logger.error(f"FastAPI health check returned {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to connect to FastAPI server: {e}")
        
        return False
    
    def disconnect(self) -> None:
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        self._connected = False
        logger.info("Disconnected from FastAPI server")
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected
    
    def list_devices(self) -> List[Dict[str, Any]]:
        """Get list of devices from server."""
        if not self._connected or self._session is None:
            return []
        
        try:
            response = self._session.get(
                f"{self.base_url}/devices",
                timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to list devices: {e}")
        
        return []
    
    def get_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get telemetry for a device."""
        if not self._connected or self._session is None:
            return None
        
        try:
            response = self._session.get(
                f"{self.base_url}/device/{uid}/telemetry",
                timeout=self.timeout
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, dict) else None
        except Exception as e:
            logger.error(f"Failed to get telemetry for {uid}: {e}")
        
        return None
    
    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get device info."""
        if not self._connected or self._session is None:
            return None
        
        try:
            response = self._session.get(
                f"{self.base_url}/device/{uid}/info",
                timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get device info for {uid}: {e}")
        
        return None
    
    @property
    def source_type(self) -> str:
        return "fastapi"


class MQTTDataSource(DataSource):
    """Data source that subscribes to an MQTT broker.
    
    This is used when multiple tools need to share data via MQTT topics.
    """

    def __init__(self, *, config: Optional["MQTTConfig"] = None, broker: str = "localhost", port: int = 1883, topic_prefix: str = "benchlab", timeout: float = 5.0):
        """Initialize MQTT data source.

        Parameters can be supplied via a :class:`MQTTConfig` model for validation.
        For backward compatibility the individual arguments are still accepted.
        """
        # Lazy import to avoid circular dependency
        from .config import MQTTConfig

        if config is None:
            config = MQTTConfig(broker=broker, port=port, topic_prefix=topic_prefix, timeout=timeout)
        self.broker = config.broker
        self.port = config.port
        self.topic_prefix = config.topic_prefix
        self.timeout = config.timeout
        self._connected = False
        self._client = None
        self._lock = threading.Lock()
        self._latest_data: Dict[str, Dict[str, Any]] = {}
        self._device_info: Dict[str, Dict[str, Any]] = {}
        self._stop_event = threading.Event()
        
        try:
            import paho.mqtt.client as mqtt
            self._mqtt = mqtt
        except ImportError:
            logger.error("paho-mqtt library not available")
            self._mqtt = None
    
    @retry(RetryPolicy(max_retries=3, backoff_factor=2.0, base_delay=0.5, allowed_exceptions=(Exception,)))
    def connect(self) -> bool:
        """Connect to the MQTT broker with retry logic."""
        if self._mqtt is None:
            return False
        
        try:
            # Handle paho-mqtt v2.x vs v1.x API
            try:
                from paho.mqtt.enums import CallbackAPIVersion
                self._client = self._mqtt.Client(
                    callback_api_version=CallbackAPIVersion.VERSION2,
                    client_id=f"benchlab_datasource_{int(time.time())}",
                    protocol=self._mqtt.MQTTv5 if hasattr(self._mqtt, 'MQTTv5') else self._mqtt.MQTTv311
                )
            except (ImportError, TypeError):
                self._client = self._mqtt.Client(
                    client_id=f"benchlab_datasource_{int(time.time())}",
                    protocol=self._mqtt.MQTTv311
                )
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect
            
            self._client.connect(self.broker, self.port, keepalive=60)
            self._client.loop_start()
            
            # Wait for connection
            start_time = time.time()
            while not self._connected and (time.time() - start_time) < self.timeout:
                time.sleep(0.1)
            
            return self._connected
            
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        self._stop_event.set()
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        logger.info("Disconnected from MQTT broker")
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected
    
    def list_devices(self) -> List[Dict[str, Any]]:
        """List known devices."""
        # Wait a bit for MQTT messages to arrive if no devices are known yet
        start_time = time.time()
        while time.time() - start_time < 2.0:  # Wait up to 2 seconds
            with self._lock:
                if self._device_info:
                    break
            time.sleep(0.1)
        
        with self._lock:
            return [
                {'uid': uid, 'port': info.get('com_port', 'unknown')}
                for uid, info in self._device_info.items()
            ]
    
    def get_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get latest telemetry for a device."""
        with self._lock:
            return self._latest_data.get(uid, None)
    
    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get device info."""
        with self._lock:
            return self._device_info.get(uid, None)
    
    @property
    def source_type(self) -> str:
        return "mqtt"
    
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """MQTT connect callback (v2.x compatible)."""
        if rc == 0:
            self._connected = True
            # Subscribe to telemetry topics
            telemetry_topic = f"{self.topic_prefix}/+/telemetry"
            info_topic = f"{self.topic_prefix}/+/info"
            self._client.subscribe([
                (telemetry_topic, 1),
                (info_topic, 1),
            ])
            logger.info(f"Connected to MQTT broker, subscribed to {telemetry_topic}")
        else:
            logger.error(f"MQTT connection failed with rc={rc}")
    
    def _on_message(self, client, userdata, msg, properties=None):
        """MQTT message callback (v2.x compatible)."""
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            
            # Parse topic to extract UID
            # Topic format: benchlab/{uid}/telemetry or benchlab/{uid}/info
            parts = msg.topic.split('/')
            if len(parts) >= 3 and parts[0] == 'benchlab':
                uid = parts[1]
                msg_type = parts[2] if len(parts) > 2 else None
                
                with self._lock:
                    if msg_type == 'telemetry':
                        self._latest_data[uid] = payload
                    elif msg_type == 'info':
                        self._device_info[uid] = payload
                        
        except Exception as e:
            logger.debug(f"Error processing MQTT message: {e}")
    
    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        """MQTT disconnect callback (v2.x compatible)."""
        self._connected = False
        if rc != 0:
            logger.warning(f"MQTT disconnected unexpectedly (rc={rc})")


def create_datasource(
    source_type: str,
    **kwargs
) -> DataSource:
    """Factory function to create a DataSource instance.
    
    Args:
        source_type: Type of data source ('direct', 'fastapi', 'mqtt')
        **kwargs: Arguments passed to the data source constructor
        
    Returns:
        DataSource instance
        
    Raises:
        ValueError: If source_type is not recognized
    """
    if source_type == 'direct':
        return DirectDataSource(**kwargs)
    elif source_type == 'fastapi':
        return FastAPIDataSource(**kwargs)
    elif source_type == 'mqtt':
        return MQTTDataSource(**kwargs)
    else:
        raise ValueError(f"Unknown data source type: {source_type}")