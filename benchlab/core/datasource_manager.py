"""
DataSource Manager for TUI and other tools

Provides a unified interface to benchlab.core.DataSource implementations
with thread-safe telemetry access, statistics collection, and consistent
snapshot API that can be consumed by UI components.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from benchlab.core import create_datasource, DataSource
from benchlab.core.discovery import discover_devices as _discover_devices

logger = logging.getLogger("benchlab.tui.datasource_manager")

# All recognised source type identifiers
ALL_SOURCE_TYPES = (
    "direct",
    "fastapi",
    "fastapi_custom",
    "mqtt",
    "mqtt_custom",
    "named_pipe",
    "service_http")


class DataSourceManager:
    """
    Unified manager for DataSource instances that provides a consistent
    snapshot API for UI components and other tools.
    """

    def __init__(
            self,
            source_type: str = 'direct',
            stats_callback: Optional[Callable] = None,
            **datasource_kwargs):
        """Initialize DataSource Manager.

        Args:
            source_type: Type of datasource:
                         'direct'       — direct serial via pycore
                         'fastapi'      — Python benchlab FastAPI server
                         'mqtt'         — MQTT broker
                         'named_pipe'   — C# BenchLab service named pipes
                                          (Windows)
                         'service_http' — C# BenchLab service HTTP API
            stats_callback: Optional callback(device_uid, channel, value)
                for statistics
            **datasource_kwargs: Arguments passed to datasource constructor
        """
        self.source_type = source_type
        self.stats_callback = stats_callback
        self.datasource_kwargs = datasource_kwargs

        self._datasource: Optional[DataSource] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Shared state
        self._connected = False
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._telemetry: Dict[str, Dict[str, Any]] = {}
        self._selected_uid: Optional[str] = None
        self._connection_time: Optional[datetime] = None
        self._last_error: Optional[str] = None

        self._prev_telemetry: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------

    def discover_devices(self) -> List[Dict[str, Any]]:
        """Expose the core discovery helper.

        Returns a list of dictionaries with ``uid``, ``port`` and ``fw`` keys
        for each connected BenchLab device found via direct serial scan.
        For named_pipe / service_http sources use list_devices() instead.
        """
        try:
            return _discover_devices()
        except Exception as e:
            logger.error(f"Device discovery failed: {e}")
            return []

    def connect(
            self,
            port: Optional[str] = None,
            uid: Optional[str] = None) -> bool:
        """Connect to the datasource.

        Args:
            port: For direct connections, the serial port to use.
            uid: Specific device UID to select after connecting.

        Returns:
            True if connection successful
        """
        self.disconnect()

        try:
            kwargs = self._filter_datasource_kwargs(port)
            self._datasource = create_datasource(self.source_type, **kwargs)

            if not self._datasource.connect():
                self._last_error = f"Failed to connect to {
                    self.source_type} datasource"
                return False

            devices = self._datasource.list_devices()
            if not devices:
                self._last_error = f"No devices available via {
                    self.source_type}"
                return False

            if uid and any(d.get('uid') == uid for d in devices):
                self._selected_uid = uid
            else:
                self._selected_uid = devices[0].get('uid')

            if not self._selected_uid:
                self._last_error = "No valid device UID found"
                return False

            with self._lock:
                self._devices.clear()
                for device in devices:
                    device_uid = device.get('uid')
                    if device_uid:
                        device_entry = device.copy()
                        try:
                            full_info = self._datasource.get_device_info(
                                device_uid)
                            if full_info:
                                device_entry.update(full_info)
                        except Exception:
                            pass
                        self._devices[device_uid] = device_entry

                self._connected = True
                self._connection_time = datetime.now()
                self._last_error = None

            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop, daemon=True)
            self._worker_thread.start()

            logger.info(
                f"Connected to {
                    self.source_type} datasource, selected device: {
                    self._selected_uid}")
            return True

        except Exception as e:
            self._last_error = f"Connection failed: {str(e)}"
            logger.error(f"DataSource connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from datasource and stop background worker."""
        self._stop_event.set()

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None

        if self._datasource:
            try:
                self._datasource.disconnect()
            except Exception as e:
                logger.warning(f"Error during datasource disconnect: {e}")
            self._datasource = None

        with self._lock:
            self._connected = False
            self._selected_uid = None
            self._connection_time = None
            self._devices.clear()
            self._telemetry.clear()
            self._prev_telemetry.clear()

        logger.info("Disconnected from datasource")

    def select_device(self, uid: str) -> bool:
        """Select a different device for monitoring."""
        with self._lock:
            if uid in self._devices:
                self._selected_uid = uid
                logger.info(f"Selected device: {uid}")
                return True
            return False

    def list_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get list of available devices."""
        if self._datasource and self._datasource.is_connected():
            try:
                devices = self._datasource.list_devices()
                device_dict = {}
                for device in devices:
                    uid = device.get('uid')
                    if uid:
                        device_dict[uid] = device

                with self._lock:
                    self._devices.update(device_dict)

                return device_dict
            except Exception as e:
                logger.warning(f"Error listing devices: {e}")

        with self._lock:
            return self._devices.copy()

    def snapshot(self) -> Dict[str, Any]:
        """Get current state snapshot for UI consumption."""
        with self._lock:
            selected_device = self._devices.get(
                self._selected_uid) if self._selected_uid else None
            selected_telemetry = self._telemetry.get(
                self._selected_uid) if self._selected_uid else None

            return {
                'connected': self._connected,
                'source_type': self.source_type,
                'source_desc': self._get_source_description(),
                'port': selected_device.get(
                    'port',
                    'unknown') if selected_device else None,
                'uid': self._selected_uid,
                'device_info': selected_device,
                'sensor_data': selected_telemetry,
                'connection_time': self._connection_time,
                'last_error': self._last_error,
                'all_devices': self._devices.copy(),
                'all_telemetry': self._telemetry.copy(),
            }

    def is_connected(self) -> bool:
        return self._connected

    def get_selected_uid(self) -> Optional[str]:
        return self._selected_uid

    def _get_source_description(self) -> str:
        """Build human-readable source description."""
        if self.source_type == 'direct':
            selected_device = self._devices.get(
                self._selected_uid) if self._selected_uid else None
            port = selected_device.get(
                'port', 'unknown') if selected_device else 'unknown'
            return port
        elif self.source_type == 'fastapi':
            base_url = self.datasource_kwargs.get(
                'base_url', 'http://127.0.0.1:8000')
            return f"FastAPI at {base_url}"
        elif self.source_type == 'fastapi_custom':
            base_url = self.datasource_kwargs.get(
                'base_url', 'http://127.0.0.1:8000')
            return f"FastAPI (custom) at {base_url}"
        elif self.source_type == 'mqtt':
            broker = self.datasource_kwargs.get('broker', 'localhost')
            port = self.datasource_kwargs.get('port', 1883)
            return f"MQTT at {broker}:{port}"
        elif self.source_type == 'mqtt_custom':
            broker = self.datasource_kwargs.get('broker', 'localhost')
            port = self.datasource_kwargs.get('port', 1883)
            return f"MQTT (custom) at {broker}:{port}"
        elif self.source_type == 'named_pipe':
            return "BenchLab Windows service (named pipe)"
        elif self.source_type == 'service_http':
            base_url = self.datasource_kwargs.get(
                'base_url', 'http://localhost:8585')
            return f"BenchLab service HTTP API at {base_url}"
        else:
            return f"{self.source_type} datasource"

    def _filter_datasource_kwargs(
            self, port: Optional[str] = None) -> Dict[str, Any]:
        """Filter datasource kwargs based on source type."""
        if self.source_type == 'direct':
            kwargs = {}
            if port:
                kwargs['port'] = port
            if 'poll_interval' in self.datasource_kwargs:
                kwargs['poll_interval'] = (
                    self.datasource_kwargs['poll_interval'])
            return kwargs

        elif self.source_type == 'fastapi':
            kwargs = {}
            if 'base_url' in self.datasource_kwargs:
                kwargs['base_url'] = self.datasource_kwargs['base_url']
            if 'timeout' in self.datasource_kwargs:
                kwargs['timeout'] = self.datasource_kwargs['timeout']
            return kwargs

        elif self.source_type == 'fastapi_custom':
            # Same as fastapi, but uses custom base_url
            kwargs = {}
            if 'base_url' in self.datasource_kwargs:
                kwargs['base_url'] = self.datasource_kwargs['base_url']
            if 'timeout' in self.datasource_kwargs:
                kwargs['timeout'] = self.datasource_kwargs['timeout']
            return kwargs

        elif self.source_type == 'mqtt':
            kwargs = {}
            for key in ('broker', 'port', 'topic_prefix', 'timeout'):
                if key in self.datasource_kwargs:
                    kwargs[key] = self.datasource_kwargs[key]
            return kwargs

        elif self.source_type == 'mqtt_custom':
            # Same as mqtt, uses custom broker/port
            kwargs = {}
            for key in ('broker', 'port', 'topic_prefix', 'timeout'):
                if key in self.datasource_kwargs:
                    kwargs[key] = self.datasource_kwargs[key]
            return kwargs

        elif self.source_type == 'named_pipe':
            # NamedPipeDataSource accepts: timeout, poll_interval
            kwargs = {}
            for key in ('timeout', 'poll_interval'):
                if key in self.datasource_kwargs:
                    kwargs[key] = self.datasource_kwargs[key]
            return kwargs

        elif self.source_type == 'service_http':
            # ServiceHttpDataSource accepts: base_url, timeout, poll_interval
            kwargs = {}
            for key in ('base_url', 'timeout', 'poll_interval'):
                if key in self.datasource_kwargs:
                    kwargs[key] = self.datasource_kwargs[key]
            return kwargs

        else:
            logger.warning(f"Unknown datasource type: {self.source_type}")
            return {}

    def _worker_loop(self):
        """Background worker that polls telemetry data."""
        poll_interval = self.datasource_kwargs.get('poll_interval', 1.0)

        logger.info(f"Starting datasource worker for {self.source_type}")

        while not self._stop_event.is_set():
            try:
                if not self._datasource or not self._datasource.is_connected():
                    with self._lock:
                        self._connected = False
                        self._last_error = "Datasource not connected"
                    time.sleep(2.0)
                    continue

                # Update device list periodically
                try:
                    devices = self._datasource.list_devices()
                    with self._lock:
                        self._devices.clear()
                        for device in devices:
                            uid = device.get('uid')
                            if uid:
                                self._devices[uid] = device
                except Exception as e:
                    logger.debug(f"Error updating device list: {e}")

                # Get telemetry for all known devices
                telemetry_updated = False
                with self._lock:
                    device_uids = list(self._devices.keys())

                for uid in device_uids:
                    try:
                        telemetry = self._datasource.get_telemetry(uid)
                        if telemetry:
                            if self.stats_callback:
                                prev_data = self._prev_telemetry.get(uid, {})
                                for key, value in telemetry.items():
                                    if (isinstance(value, (int, float))
                                            and key != 'timestamp'):
                                        if (key not in prev_data
                                                or value
                                                != prev_data.get(key)):
                                            self.stats_callback(
                                                uid, key, value)
                                self._prev_telemetry[uid] = {**prev_data, **{
                                    k: v for k, v in telemetry.items()
                                    if isinstance(v, (int, float))
                                }}

                            with self._lock:
                                self._telemetry[uid] = telemetry
                                telemetry_updated = True
                    except Exception as e:
                        logger.debug(f"Error getting telemetry for {uid}: {e}")

                if telemetry_updated:
                    with self._lock:
                        self._connected = True
                        self._last_error = None

            except Exception as e:
                logger.warning(f"Error in datasource worker: {e}")
                with self._lock:
                    self._connected = False
                    self._last_error = str(e)

            self._stop_event.wait(poll_interval)

        logger.info("Datasource worker stopped")
