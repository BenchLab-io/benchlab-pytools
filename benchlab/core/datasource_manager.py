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
from typing import Any, Dict, Optional, Callable

from benchlab.core import create_datasource, DataSource

logger = logging.getLogger("benchlab.tui.datasource_manager")


class DataSourceManager:
    """
    Unified manager for DataSource instances that provides a consistent
    snapshot API for UI components and other tools.
    
    This replaces the DataSourceWorkerWrapper and provides clean separation
    between data source management and UI concerns.
    """
    
    def __init__(self, source_type: str = 'direct', stats_callback: Optional[Callable] = None, **datasource_kwargs):
        """Initialize DataSource Manager.
        
        Args:
            source_type: Type of datasource ('direct', 'fastapi', 'mqtt')
            stats_callback: Optional callback(device_uid, channel, value) for statistics
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
        self._devices: Dict[str, Dict[str, Any]] = {}  # uid -> device_info
        self._telemetry: Dict[str, Dict[str, Any]] = {}  # uid -> sensor_data
        self._selected_uid: Optional[str] = None
        self._connection_time: Optional[datetime] = None
        self._last_error: Optional[str] = None
        
        # Track previous values for stats updates
        self._prev_telemetry: Dict[str, Dict[str, Any]] = {}

    def connect(self, port: Optional[str] = None, uid: Optional[str] = None) -> bool:
        """Connect to the datasource.
        
        Args:
            port: For direct connections, the serial port to use
            uid: For network connections, specific device UID to focus on
            
        Returns:
            True if connection successful
        """
        self.disconnect()  # Clean disconnect first
        
        try:
            # Create datasource instance with filtered parameters
            kwargs = self._filter_datasource_kwargs(port)
            self._datasource = create_datasource(self.source_type, **kwargs)
            
            # Connect to datasource
            if not self._datasource.connect():
                self._last_error = f"Failed to connect to {self.source_type} datasource"
                return False
            
            # Get available devices
            devices = self._datasource.list_devices()
            if not devices:
                self._last_error = f"No devices available via {self.source_type}"
                return False
            
            # Select device - prefer specified uid, otherwise first available
            if uid and any(d.get('uid') == uid for d in devices):
                self._selected_uid = uid
            else:
                self._selected_uid = devices[0].get('uid')
            
            if not self._selected_uid:
                self._last_error = "No valid device UID found"
                return False
            
            # Update device info
            with self._lock:
                self._devices.clear()
                for device in devices:
                    device_uid = device.get('uid')
                    if device_uid:
                        self._devices[device_uid] = device
                
                self._connected = True
                self._connection_time = datetime.now()
                self._last_error = None
            
            # Start background worker
            self._stop_event.clear()
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()
            
            logger.info(f"Connected to {self.source_type} datasource, selected device: {self._selected_uid}")
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
        """Select a different device for monitoring.
        
        Args:
            uid: Device UID to select
            
        Returns:
            True if device exists and was selected
        """
        with self._lock:
            if uid in self._devices:
                self._selected_uid = uid
                logger.info(f"Selected device: {uid}")
                return True
            return False

    def list_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get list of available devices.
        
        Returns:
            Dictionary mapping UID to device info
        """
        if self._datasource and self._datasource.is_connected():
            try:
                devices = self._datasource.list_devices()
                device_dict = {}
                for device in devices:
                    uid = device.get('uid')
                    if uid:
                        device_dict[uid] = device
                
                # Update our cached device info
                with self._lock:
                    self._devices.update(device_dict)
                
                return device_dict
            except Exception as e:
                logger.warning(f"Error listing devices: {e}")
        
        with self._lock:
            return self._devices.copy()

    def snapshot(self) -> Dict[str, Any]:
        """Get current state snapshot for UI consumption.
        
        Returns:
            Dictionary with connection status, device info, and telemetry data
        """
        with self._lock:
            selected_device = self._devices.get(self._selected_uid) if self._selected_uid else None
            selected_telemetry = self._telemetry.get(self._selected_uid) if self._selected_uid else None
            
            return {
                'connected': self._connected,
                'source_type': self.source_type,
                'source_desc': self._get_source_description(),
                'port': selected_device.get('port', 'unknown') if selected_device else None,
                'uid': self._selected_uid,
                'device_info': selected_device,
                'sensor_data': selected_telemetry,
                'sensor_struct': None,  # Only available in direct mode with legacy SerialWorker
                'connection_time': self._connection_time,
                'last_error': self._last_error,
                'all_devices': self._devices.copy(),
                'all_telemetry': self._telemetry.copy(),
            }

    def is_connected(self) -> bool:
        """Check if connected to datasource."""
        return self._connected

    def get_selected_uid(self) -> Optional[str]:
        """Get currently selected device UID."""
        return self._selected_uid

    def _get_source_description(self) -> str:
        """Build human-readable source description."""
        if self.source_type == 'direct':
            selected_device = self._devices.get(self._selected_uid) if self._selected_uid else None
            port = selected_device.get('port', 'unknown') if selected_device else 'unknown'
            return port
        elif self.source_type == 'fastapi':
            base_url = self.datasource_kwargs.get('base_url', 'http://127.0.0.1:8000')
            return f"FastAPI at {base_url}"
        elif self.source_type == 'mqtt':
            broker = self.datasource_kwargs.get('broker', 'localhost')
            port = self.datasource_kwargs.get('port', 1883)
            return f"MQTT at {broker}:{port}"
        else:
            return f"{self.source_type} datasource"

    def _filter_datasource_kwargs(self, port: Optional[str] = None) -> Dict[str, Any]:
        """Filter datasource kwargs based on source type to avoid parameter mismatches.
        
        Args:
            port: Optional port for direct connections
            
        Returns:
            Filtered kwargs appropriate for the datasource type
        """
        if self.source_type == 'direct':
            # DirectDataSource accepts: port, poll_interval
            kwargs = {}
            if port:
                kwargs['port'] = port
            if 'poll_interval' in self.datasource_kwargs:
                kwargs['poll_interval'] = self.datasource_kwargs['poll_interval']
            return kwargs
        
        elif self.source_type == 'fastapi':
            # FastAPIDataSource accepts: base_url, timeout
            kwargs = {}
            if 'base_url' in self.datasource_kwargs:
                kwargs['base_url'] = self.datasource_kwargs['base_url']
            if 'timeout' in self.datasource_kwargs:
                kwargs['timeout'] = self.datasource_kwargs['timeout']
            return kwargs
        
        elif self.source_type == 'mqtt':
            # MQTTDataSource accepts: broker, port, topic_prefix, timeout
            kwargs = {}
            if 'broker' in self.datasource_kwargs:
                kwargs['broker'] = self.datasource_kwargs['broker']
            if 'port' in self.datasource_kwargs:
                kwargs['port'] = self.datasource_kwargs['port']
            if 'topic_prefix' in self.datasource_kwargs:
                kwargs['topic_prefix'] = self.datasource_kwargs['topic_prefix']
            if 'timeout' in self.datasource_kwargs:
                kwargs['timeout'] = self.datasource_kwargs['timeout']
            return kwargs
        
        else:
            # Unknown datasource type, return empty dict to be safe
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
                            # Update stats if callback provided
                            if self.stats_callback:
                                prev_data = self._prev_telemetry.get(uid, {})
                                for key, value in telemetry.items():
                                    if isinstance(value, (int, float)) and key != 'timestamp':
                                        if key not in prev_data or value != prev_data[key]:
                                            self.stats_callback(uid, key, value)
                                self._prev_telemetry[uid] = prev_data.copy()
                                self._prev_telemetry[uid].update(telemetry)
                            
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