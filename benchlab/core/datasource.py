"""
Data Source Abstraction Layer for BENCHLAB tools

Provides a unified interface for tools to consume telemetry data from:
- Direct serial connection (pycore)
- FastAPI server (Python benchlab service)
- MQTT broker
- Named pipe (Windows C# BenchLab service)
- Service HTTP API (C# BenchLab service REST API)
"""

from .retry import retry, RetryPolicy
import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import SerialConfig, FastAPIConfig, MQTTConfig

logger = logging.getLogger("benchlab.core.datasource")

# Import retry utilities for robust connection handling


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
            List of device info dictionaries with at least 'uid' and
            'port' keys
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
        """Return the type of data source (e.g., 'direct', 'fastapi',
        'mqtt')."""
        pass


class DirectDataSource(DataSource):
    """Data source that connects directly to serial port via pycore.

    This is used when running a single tool that can exclusively claim
    the serial port.
    """

    def __init__(
            self,
            *,
            config: Optional["SerialConfig"] = None,
            port: Optional[str] = None,
            poll_interval: float = 1.0):
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
                translate_sensor_struct, get_benchlab_ports,
                BENCHLAB_ORIGINAL_PRODUCT_ID,
            )
            try:
                from benchlab_pycore.core import BENCHLAB_BL2_PRODUCT_ID
            except ImportError:
                from benchlab_pycore.core import (
                    BENCHLAB_CFE_PRODUCT_ID as BENCHLAB_BL2_PRODUCT_ID)
            # benchlab_pycore.core.serial_io has no connection-opening helper;
            # use the local wrapper instead (see benchlab.core.shared_serial).
            from benchlab.core.shared_serial import open_serial_connection
            self._pycore = {
                'read_sensors': read_sensors,
                'read_device': read_device,
                'read_uid': read_uid,
                'translate_sensor_struct': translate_sensor_struct,
                'get_benchlab_ports': get_benchlab_ports,
                'open_serial_connection': open_serial_connection,
                'BENCHLAB_ORIGINAL_PRODUCT_ID': BENCHLAB_ORIGINAL_PRODUCT_ID,
                'BENCHLAB_BL2_PRODUCT_ID': BENCHLAB_BL2_PRODUCT_ID,
            }
        except ImportError as e:
            logger.error(f"Failed to import benchlab_pycore: {e}")
            self._pycore = None

    @retry(RetryPolicy(max_retries=3, backoff_factor=2.0,
           base_delay=0.5, allowed_exceptions=(Exception,)))
    def connect(self) -> bool:
        if self._pycore is None:
            return False
        if self._connected:
            return True

        ports = self._pycore['get_benchlab_ports']()
        if self.port is not None:
            ports = [p for p in ports if p.get('port') == self.port]

        if not ports:
            logger.error("No BenchLab devices found")
            return False

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
                    product_id = info.get(
                        'ProductId',
                        self._pycore['BENCHLAB_ORIGINAL_PRODUCT_ID'])
                    is_bl2 = (
                        product_id
                        == self._pycore['BENCHLAB_BL2_PRODUCT_ID'])
                    variant = "BL2" if is_bl2 else "ORIGINAL"
                    self._device_info[uid] = {
                        **info, 'uid': uid, 'port': port, 'variant': variant}
                    self._ser_handles[uid] = ser
                    logger.info(f"Connected to device {uid} on {port}")
                else:
                    ser.close()
            except Exception as e:
                logger.debug(f"Failed to probe {port}: {e}")

        if not self._device_info:
            logger.error("Could not connect to any BenchLab device")
            return False

        self.port = next(iter(self._device_info.values()))['port']
        first_uid = next(iter(self._device_info))
        self._ser = self._ser_handles[first_uid]

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        self._connected = True
        return True

    def disconnect(self) -> None:
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
        return self._connected

    def list_devices(self) -> List[Dict[str, Any]]:
        if self._pycore is None:
            return []

        if self._connected and self._device_info:
            devices = []
            for uid, info in self._device_info.items():
                devices.append({
                    'uid': uid,
                    'port': info.get('port', self.port),
                    'firmware': info.get('FwVersion', '?'),
                    'variant': info.get('variant', 'ORIGINAL'),
                    'VendorId': info.get('VendorId', 0),
                    'ProductId': info.get('ProductId', 0),
                    'FwVersion': info.get('FwVersion', 0),
                })
            return devices

        devices = []
        ports = self._pycore['get_benchlab_ports']()
        for port_info in ports:
            port = port_info.get('port')
            if port:
                try:
                    ser = self._pycore['open_serial_connection'](port)
                    if ser:
                        uid = self._pycore['read_uid'](ser)
                        info = self._pycore['read_device'](ser) or {}
                        ser.close()
                        if uid:
                            product_id = info.get(
                                'ProductId',
                                self._pycore[
                                    'BENCHLAB_ORIGINAL_PRODUCT_ID'])
                            is_bl2 = (
                                product_id
                                == self._pycore['BENCHLAB_BL2_PRODUCT_ID'])
                            variant = "BL2" if is_bl2 else "ORIGINAL"
                            devices.append({
                                'uid': uid,
                                'port': port,
                                'firmware': info.get('FwVersion', '?'),
                                'variant': variant,
                            })
                except Exception as e:
                    logger.debug(f"Failed to probe {port}: {e}")
        return devices

    def get_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._latest_data.get(uid, None)

    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        return self._device_info.get(uid, None)

    @property
    def source_type(self) -> str:
        return "direct"

    def _worker_loop(self):
        while not self._stop_event.is_set():
            for uid, ser in list(self._ser_handles.items()):
                try:
                    # Get product_id for this device to ensure correct sensor
                    # interpretation
                    device_info = self._device_info.get(uid, {})
                    product_id = device_info.get(
                        'ProductId',
                        self._pycore['BENCHLAB_ORIGINAL_PRODUCT_ID'])
                    sensors = self._pycore['read_sensors'](
                        ser, product_id=product_id)
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

    def __init__(
            self,
            *,
            config: Optional["FastAPIConfig"] = None,
            base_url: str = "http://127.0.0.1:8000",
            timeout: float = 5.0):
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

    @retry(RetryPolicy(max_retries=3, backoff_factor=2.0,
           base_delay=0.5, allowed_exceptions=(Exception,)))
    def connect(self) -> bool:
        if self._requests is None:
            return False

        try:
            self._session = self._requests.Session()
            response = self._session.get(
                f"{self.base_url}/health",
                timeout=self.timeout
            )
            if response.status_code == 200:
                health = response.json()
                logger.info(f"FastAPI health: {health}")
                self._connected = True
                logger.info(f"Connected to FastAPI server at {self.base_url}")
                return True
            else:
                logger.error(
                    f"FastAPI health check returned {
                        response.status_code}")
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
        return self._connected

    def list_devices(self) -> List[Dict[str, Any]]:
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


def resolve_mqtt_protocol(value, mqtt_module):
    """Resolve *value* to one of paho's MQTTv31/MQTTv311/MQTTv5 int constants.

    Accepts the int constants themselves, or common name/version strings
    (case-insensitive, e.g. "MQTTv5", "v3.1.1", "5"). Raises ValueError
    immediately on anything unrecognized, so a bad MQTT_PROTOCOL value is
    caught at startup instead of crashing silently inside paho's background
    network thread once a broker actually accepts the connection.

    *mqtt_module* is the imported ``paho.mqtt.client`` module, passed in
    rather than imported at module scope here since paho-mqtt is an
    optional/lazy dependency of this module (see MQTTDataSource.__init__).
    """
    aliases = {
        "3": mqtt_module.MQTTv31,
        "3.1": mqtt_module.MQTTv31,
        "v3.1": mqtt_module.MQTTv31,
        "mqttv31": mqtt_module.MQTTv31,
        "4": mqtt_module.MQTTv311,
        "3.1.1": mqtt_module.MQTTv311,
        "v3.1.1": mqtt_module.MQTTv311,
        "mqttv311": mqtt_module.MQTTv311,
        "mqttv3.1.1": mqtt_module.MQTTv311,
        "5": mqtt_module.MQTTv5,
        "v5": mqtt_module.MQTTv5,
        "mqttv5": mqtt_module.MQTTv5,
    }

    if value in (
            mqtt_module.MQTTv31,
            mqtt_module.MQTTv311,
            mqtt_module.MQTTv5):
        return value

    key = str(value).strip().lower()
    if key in aliases:
        return aliases[key]

    raise ValueError(
        f"Unrecognized MQTT_PROTOCOL value: {value!r}. "
        f"Expected one of: MQTTv31 (3.1), MQTTv311 (3.1.1, default), MQTTv5."
    )


class MQTTDataSource(DataSource):
    """Data source that subscribes to an MQTT broker."""

    def __init__(
            self,
            *,
            config: Optional["MQTTConfig"] = None,
            broker: str = "localhost",
            port: int = 1883,
            topic_prefix: str = "benchlab",
            timeout: float = 5.0,
            protocol: Optional[str] = None):
        from .config import MQTTConfig
        import os

        if config is None:
            config = MQTTConfig(
                broker=broker,
                port=port,
                topic_prefix=topic_prefix,
                timeout=timeout)
        self.broker = config.broker
        self.port = config.port
        self.topic_prefix = config.topic_prefix
        self.timeout = config.timeout
        # Falls back to MQTT_PROTOCOL env var (same variable the publisher
        # side reads) so both producer and consumer resolve consistently,
        # then to MQTTv311 if neither is set.
        self._protocol_setting = (
            protocol if protocol is not None
            else os.getenv("MQTT_PROTOCOL", "MQTTv311"))
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

    @retry(RetryPolicy(max_retries=3, backoff_factor=2.0,
           base_delay=0.5, allowed_exceptions=(Exception,)))
    def connect(self) -> bool:
        if self._mqtt is None:
            return False

        try:
            resolved_protocol = resolve_mqtt_protocol(
                self._protocol_setting, self._mqtt)
            try:
                from paho.mqtt.enums import CallbackAPIVersion
                self._client = self._mqtt.Client(
                    callback_api_version=CallbackAPIVersion.VERSION2,
                    client_id=f"benchlab_datasource_{int(time.time())}",
                    protocol=resolved_protocol
                )
            except (ImportError, TypeError):
                self._client = self._mqtt.Client(
                    client_id=f"benchlab_datasource_{int(time.time())}",
                    protocol=resolved_protocol
                )
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect

            self._client.connect(self.broker, self.port, keepalive=60)
            self._client.loop_start()

            start_time = time.time()
            while not self._connected and (
                    time.time() - start_time) < self.timeout:
                time.sleep(0.1)

            return self._connected

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    def disconnect(self) -> None:
        self._stop_event.set()
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        logger.info("Disconnected from MQTT broker")

    def is_connected(self) -> bool:
        return self._connected

    def list_devices(self) -> List[Dict[str, Any]]:
        start_time = time.time()
        while time.time() - start_time < 2.0:
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
        with self._lock:
            return self._latest_data.get(uid, None)

    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._device_info.get(uid, None)

    @property
    def source_type(self) -> str:
        return "mqtt"

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected = True
            telemetry_topic = f"{self.topic_prefix}/+/telemetry"
            info_topic = f"{self.topic_prefix}/+/info"
            self._client.subscribe([
                (telemetry_topic, 1),
                (info_topic, 1),
            ])
            logger.info(
                f"Connected to MQTT broker, subscribed to {telemetry_topic}")
        else:
            logger.error(f"MQTT connection failed with rc={rc}")

    def _on_message(self, client, userdata, msg, properties=None):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            parts = msg.topic.split('/')
            if len(parts) >= 3 and parts[0] == self.topic_prefix:
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
        self._connected = False
        if rc != 0:
            logger.warning(f"MQTT disconnected unexpectedly (rc={rc})")


def _normalise_cs_telemetry(sensors_raw: list) -> Dict[str, Any]:
    """Normalise a C# SensorList into the flat dict shape the TUI expects.

    The C# service serialises sensors with ShortName keys like 'SYS_P',
    'T_CHIP', 'FAN1_T', 'V1' etc.  The TUI (and other Python tools) expect
    keys like 'SYS_Power', 'Chip_Temp', 'Fan1_RPM', 'VIN_0' etc.

    Any key not listed in the mapping is passed through unchanged so that
    future sensors are visible even without an explicit mapping entry.
    """
    # Map C# ShortName → TUI key
    SHORT_NAME_MAP: Dict[str, str] = {
        # Power summary
        "SYS_P": "SYS_Power",
        "CPU_P": "CPU_Power",
        "GPU_P": "GPU_Power",
        "MB_P": "MB_Power",

        # Rail power  (EPS, ATX, PCIE, HPWR)
        "EPS1_P": "EPS1_Power", "EPS1_V": "EPS1_Voltage",
        "EPS1_I": "EPS1_Current",
        "EPS2_P": "EPS2_Power", "EPS2_V": "EPS2_Voltage",
        "EPS2_I": "EPS2_Current",
        "ATX3V_P": "ATX3V_Power", "ATX3V_V": "ATX3V_Voltage",
        "ATX3V_I": "ATX3V_Current",
        "ATX5V_P": "ATX5V_Power", "ATX5V_V": "ATX5V_Voltage",
        "ATX5V_I": "ATX5V_Current",
        "ATX5VSB_P": "ATX5VSB_Power", "ATX5VSB_V": "ATX5VSB_Voltage",
        "ATX5VSB_I": "ATX5VSB_Current",
        "ATX12V_P": "ATX12V_Power", "ATX12V_V": "ATX12V_Voltage",
        "ATX12V_I": "ATX12V_Current",
        "PCIE1_P": "PCIE1_Power", "PCIE1_V": "PCIE1_Voltage",
        "PCIE1_I": "PCIE1_Current",
        "PCIE2_P": "PCIE2_Power", "PCIE2_V": "PCIE2_Voltage",
        "PCIE2_I": "PCIE2_Current",
        "PCIE3_P": "PCIE3_Power", "PCIE3_V": "PCIE3_Voltage",
        "PCIE3_I": "PCIE3_Current",
        "HPWR1_P": "HPWR1_Power", "HPWR1_V": "HPWR1_Voltage",
        "HPWR1_I": "HPWR1_Current",
        "HPWR2_P": "HPWR2_Power", "HPWR2_V": "HPWR2_Voltage",
        "HPWR2_I": "HPWR2_Current",

        # Board voltages
        "VDD": "Vdd",
        "VREF": "Vref",

        # VIN voltage measurements V1..V13 → VIN_0..VIN_12
        **{f"V{i}": f"VIN_{i - 1}" for i in range(1, 14)},

        # Temperatures
        "T_CHIP": "Chip_Temp",
        "T_AMB": "Ambient_Temp",
        "HUM": "Humidity",
        "TS1": "TS_1",
        "TS2": "TS_2",
        "TS3": "TS_3",
        "TS4": "TS_4",

        # Fans — T=tach(RPM), D=duty
        **{f"FAN{i}_T": f"Fan{i}_RPM" for i in range(1, 10)},
        **{f"FAN{i}_D": f"Fan{i}_Duty" for i in range(1, 10)},
        "FAN_EXT": "FanExtDuty",
    }

    result: Dict[str, Any] = {}
    for s in sensors_raw:
        short = s.get("ShortName") or s.get("shortName", "")
        value = s.get("Value") if s.get(
            "Value") is not None else s.get("value", 0.0)

        # Skip sentinel value (double.MinValue serialised as very large
        # negative)
        if isinstance(value, float) and value < -1e300:
            continue

        tui_key = SHORT_NAME_MAP.get(short, short)
        result[tui_key] = value

    return result


class NamedPipeDataSource(DataSource):
    """Data source that connects to the C# BenchLab Windows service via
    named pipes.

    Uses the BenchlabDiscovery pipe to enumerate devices, then connects to
    each device's individual BenchlabSensorPipe_XX_YYY pipe for telemetry.

    Windows-only. Will fail fast with a clear error on other platforms.
    """

    DISCOVERY_PIPE = "BenchlabDiscovery"
    PIPE_TIMEOUT_MS = 5000

    def __init__(self, *, timeout: float = 5.0, poll_interval: float = 1.0):
        """Initialize named pipe data source.

        Args:
            timeout: Seconds to wait for pipe connection.
            poll_interval: Seconds between sensor polls.
        """
        import sys
        if not sys.platform.startswith("win"):
            raise RuntimeError(
                "NamedPipeDataSource is only supported on Windows. "
                "The C# BenchLab service uses Windows named pipes."
            )

        self.timeout = timeout
        self.poll_interval = poll_interval

        self._connected = False
        self._lock = threading.Lock()
        # uid/guid -> device info
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._pipe_names: Dict[str, str] = {}            # uid -> pipe name
        # uid -> latest sensors
        self._telemetry: Dict[str, Dict[str, Any]] = {}
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def connect(self) -> bool:
        """Connect by querying the discovery pipe for available devices."""
        try:
            devices = self._query_discovery_pipe("ListDevices")
        except Exception as e:
            logger.error(
                f"Cannot connect to BenchLab named pipe service: {e}\n"
                "  Make sure the BenchLab Windows service is running."
            )
            return False

        if not isinstance(devices, list) or not devices:
            logger.error(
                "BenchLab service is running but returned no devices. "
                "Check that a BENCHLAB device is connected via USB."
            )
            return False

        with self._lock:
            self._devices.clear()
            self._pipe_names.clear()
            for d in devices:
                uid = d.get("guid") or d.get("deviceName", "unknown")
                self._devices[uid] = {
                    "uid": uid,
                    "port": d.get("port", "unknown"),
                    "name": d.get("deviceName", "unknown"),
                    "productId": d.get("productId", 0),
                    "status": d.get("status", "unknown"),
                    "sensorCount": d.get("sensorCount", 0),
                    "pipeName": d.get("pipeName", ""),
                }
                self._pipe_names[uid] = d.get("pipeName", "")

        self._connected = True
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info(
            f"Connected to BenchLab named pipe service with "
            f"{len(self._devices)} device(s)")
        return True

    def disconnect(self) -> None:
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        with self._lock:
            self._connected = False
            self._devices.clear()
            self._pipe_names.clear()
            self._telemetry.clear()
        logger.info("Disconnected from BenchLab named pipe service")

    def is_connected(self) -> bool:
        return self._connected

    def list_devices(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._devices.values())

    def get_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._telemetry.get(uid)

    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._devices.get(uid)

    @property
    def source_type(self) -> str:
        return "named_pipe"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_pipe(self, pipe_name: str):
        """Open a named pipe and return the file handle."""
        import win32file  # type: ignore
        import win32pipe  # type: ignore
        import pywintypes  # type: ignore

        path = f"\\\\.\\pipe\\{pipe_name}"
        # WaitNamedPipe gives up to timeout_ms for a server instance to become
        # available
        try:
            win32pipe.WaitNamedPipe(path, self.PIPE_TIMEOUT_MS)
        except pywintypes.error as e:
            raise ConnectionError(
                f"Pipe '{pipe_name}' not available: {e}") from e

        handle = win32file.CreateFile(
            path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        return handle

    def _pipe_readline(self, handle) -> str:
        """Read one line from a pipe handle (reads until newline)."""
        import win32file  # type: ignore

        buf = b""
        while True:
            _, chunk = win32file.ReadFile(handle, 4096)
            buf += chunk
            if b"\n" in buf:
                line, _ = buf.split(b"\n", 1)
                return line.decode("utf-8-sig").strip()

    def _pipe_writeline(self, handle, text: str) -> None:
        """Write a line to a pipe handle."""
        import win32file  # type: ignore

        win32file.WriteFile(handle, (text + "\n").encode("utf-8"))

    def _query_discovery_pipe(self, command: str) -> Any:
        """Send a command to the discovery pipe and return parsed JSON
        response."""
        import win32file  # type: ignore

        handle = self._open_pipe(self.DISCOVERY_PIPE)
        try:
            self._pipe_writeline(handle, command)
            response = self._pipe_readline(handle)
            return json.loads(response)
        finally:
            win32file.CloseHandle(handle)

    def _query_sensor_pipe(self, pipe_name: str, command: str) -> Any:
        """Send a command to a device sensor pipe and return parsed JSON
        response."""
        import win32file  # type: ignore

        handle = self._open_pipe(pipe_name)
        try:
            self._pipe_writeline(handle, command)
            response = self._pipe_readline(handle)
            return json.loads(response)
        finally:
            win32file.CloseHandle(handle)

    def _worker_loop(self) -> None:
        """Background thread: poll sensor data for all devices."""
        # Prime each device on first iteration
        first_run = True

        while not self._stop_event.is_set():
            with self._lock:
                items = list(self._pipe_names.items())

            for uid, pipe_name in items:
                if not pipe_name:
                    continue
                try:
                    # First call primes the device; second returns real data.
                    # On subsequent loops we only call once.
                    if first_run:
                        self._query_sensor_pipe(
                            pipe_name, "GetUpdatedSensorList")

                    data = self._query_sensor_pipe(
                        pipe_name, "GetUpdatedSensorList")
                    sensors_raw = data.get("sensors", [])
                    normalised = _normalise_cs_telemetry(sensors_raw)
                    normalised["timestamp"] = datetime.now(UTC).isoformat()

                    with self._lock:
                        self._telemetry[uid] = normalised

                except Exception as e:
                    logger.debug(f"Error polling sensor pipe {pipe_name}: {e}")

            first_run = False
            self._stop_event.wait(self.poll_interval)


class ServiceHttpDataSource(DataSource):
    """Data source that connects to the C# BenchLab service HTTP API.

    Auto-detects the service at http://localhost:8585 (the C# service default).
    This is a thin REST client — it does not start or manage the service.
    If the service is not running it fails fast with a helpful message.

    The C# service HTTP API is different from the Python FastAPI server:
    - C# service runs on port 8585 by default
    - Endpoint shape differs slightly (uses /device/{uid}/... pattern)
    - Sensor values come from the C# SensorList serialisation
    """

    DEFAULT_URL = "http://localhost:8585"

    def __init__(
            self,
            *,
            base_url: str = DEFAULT_URL,
            timeout: float = 5.0,
            poll_interval: float = 1.0):
        """Initialize service HTTP data source.

        Args:
            base_url: Base URL of the C# BenchLab service. Defaults to
                      http://localhost:8585.
            timeout: HTTP request timeout in seconds.
            poll_interval: Seconds between telemetry polls in background
                      worker.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval

        self._connected = False
        self._session = None
        self._lock = threading.Lock()
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._telemetry: Dict[str, Dict[str, Any]] = {}
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        try:
            import requests
            self._requests = requests
        except ImportError:
            logger.error(
                "requests library not available — pip install requests")
            self._requests = None

    def connect(self) -> bool:
        """Connect by verifying the C# service health endpoint."""
        if self._requests is None:
            return False

        self._session = self._requests.Session()

        try:
            resp = self._session.get(
                f"{self.base_url}/health", timeout=self.timeout)
        except Exception as e:
            logger.error(
                f"Cannot reach BenchLab service at {self.base_url}: {e}\n"
                "  Make sure the BenchLab Windows service is running and "
                "listening on the correct port."
            )
            self._session.close()
            self._session = None
            return False

        if resp.status_code != 200:
            logger.error(
                f"BenchLab service health check failed (HTTP {
                    resp.status_code}) " f"at {
                    self.base_url}/health")
            return False

        # Fetch initial device list
        devices = self._fetch_devices()
        if not devices:
            logger.warning(
                "BenchLab service is reachable but returned no devices. "
                "Check that a BENCHLAB device is connected via USB."
            )
            # Still consider connected — devices may appear later
        else:
            with self._lock:
                for d in devices:
                    uid = d.get("uid", "")
                    if uid:
                        self._devices[uid] = d

        self._connected = True
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info(
            f"Connected to BenchLab service HTTP API at {
                self.base_url}")
        return True

    def disconnect(self) -> None:
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        with self._lock:
            self._connected = False
            self._devices.clear()
            self._telemetry.clear()
        logger.info("Disconnected from BenchLab service HTTP API")

    def is_connected(self) -> bool:
        return self._connected

    def list_devices(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._devices.values())

    def get_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._telemetry.get(uid)

    def get_device_info(self, uid: str) -> Optional[Dict[str, Any]]:
        if not self._session:
            return None
        try:
            resp = self._session.get(
                f"{self.base_url}/device/{uid}/info",
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Failed to get device info for {uid}: {e}")
        return None

    @property
    def source_type(self) -> str:
        return "service_http"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_devices(self) -> List[Dict[str, Any]]:
        """Fetch device list from /devices endpoint."""
        if not self._session:
            return []
        try:
            resp = self._session.get(
                f"{self.base_url}/devices", timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Failed to fetch devices: {e}")
        return []

    def _fetch_telemetry(self, uid: str) -> Optional[Dict[str, Any]]:
        """Fetch telemetry from /device/{uid}/telemetry."""
        if not self._session:
            return None
        try:
            resp = self._session.get(
                f"{self.base_url}/device/{uid}/telemetry",
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                sensors_raw = data.get("sensors", [])
                normalised = _normalise_cs_telemetry(sensors_raw)
                normalised["timestamp"] = data.get(
                    "timestamp", datetime.now(UTC).isoformat())
                return normalised
        except Exception as e:
            logger.debug(f"Failed to fetch telemetry for {uid}: {e}")
        return None

    def _worker_loop(self) -> None:
        """Background thread: refresh device list and poll telemetry."""
        while not self._stop_event.is_set():
            try:
                # Refresh device list
                devices = self._fetch_devices()
                if devices:
                    with self._lock:
                        self._devices.clear()
                        for d in devices:
                            uid = d.get("uid", "")
                            if uid:
                                self._devices[uid] = d

                # Poll telemetry for each device
                with self._lock:
                    uids = list(self._devices.keys())

                for uid in uids:
                    telemetry = self._fetch_telemetry(uid)
                    if telemetry:
                        with self._lock:
                            self._telemetry[uid] = telemetry

            except Exception as e:
                logger.warning(f"Error in ServiceHttpDataSource worker: {e}")

            self._stop_event.wait(self.poll_interval)


def create_datasource(
    source_type: str,
    **kwargs
) -> DataSource:
    """Factory function to create a DataSource instance.

    Args:
        source_type: Type of data source:
                     'direct'        - direct serial via pycore
                     'fastapi'       - Python benchlab FastAPI server
                                       (localhost)
                     'fastapi_custom'- FastAPI server at custom URL
                     'mqtt'          - MQTT broker
                     'named_pipe'    - C# BenchLab service named pipes
                                       (Windows only)
                     'service_http'  - C# BenchLab service HTTP API
        **kwargs: Arguments passed to the data source constructor

    Returns:
        DataSource instance

    Raises:
        ValueError: If source_type is not recognized
    """
    if source_type == 'direct':
        return DirectDataSource(**kwargs)
    elif source_type in ('fastapi', 'fastapi_custom'):
        # Both fastapi and fastapi_custom use FastAPIDataSource
        # fastapi_custom just passes a custom base_url
        return FastAPIDataSource(**kwargs)
    elif source_type in ('mqtt', 'mqtt_custom'):
        # Both mqtt and mqtt_custom use MQTTDataSource
        # mqtt_custom just passes custom broker/port
        return MQTTDataSource(**kwargs)
    elif source_type == 'named_pipe':
        return NamedPipeDataSource(**kwargs)
    elif source_type == 'service_http':
        return ServiceHttpDataSource(**kwargs)
    else:
        raise ValueError(f"Unknown data source type: {source_type}")
