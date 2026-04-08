"""
DataSource Worker for TUI

Provides a worker thread that polls data from any DataSource implementation
and provides a consistent snapshot API for the TUI render loop.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("benchlab.tui.datasource_worker")


class DataSourceWorker(threading.Thread):
    """
    Background worker that polls telemetry data from a DataSource.
    Provides a thread-safe snapshot API for the TUI render loop.
    """

    def __init__(
        self,
        datasource,
        uid: str,
        interval: float = 1.0,
        stats_callback=None,
    ):
        """Initialize data source worker.
        
        Args:
            datasource: DataSource instance to use
            uid: Device UID to monitor
            interval: Poll interval in seconds
            stats_callback: Optional callback(stats, device, channel, value) for stats
        """
        super().__init__(daemon=True)
        self.datasource = datasource
        self.uid = uid
        self.interval = interval
        self.stats_callback = stats_callback

        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Shared state
        self.connected = False
        self.sensor_data: Optional[Dict[str, Any]] = None
        self.device_info: Optional[Dict[str, Any]] = None
        # Note: sensor_struct is None for non-direct sources
        self.sensor_struct = None
        self.connection_time: Optional[datetime] = None
        self.last_error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        """Return a consistent copy of the latest telemetry."""
        with self._lock:
            return {
                'connected': self.connected,
                'port': getattr(self.datasource, 'port', getattr(self.datasource, 'base_url', 'unknown')),
                'sensor_data': self.sensor_data,
                'device_info': self.device_info,
                'sensor_struct': self.sensor_struct,
                'uid': self.uid,
                'connection_time': self.connection_time,
                'last_error': self.last_error,
            }

    def stop(self):
        """Signal the worker to stop."""
        self._stop_event.set()

    def run(self):
        """Main worker loop."""
        logger.info(f"Starting DataSourceWorker for {self.uid} ({self.datasource.source_type})")
        
        # Track previous values for stats
        prev_data = {}

        while not self._stop_event.is_set():
            try:
                # Check connection
                if not self.datasource.is_connected():
                    if not self.datasource.connect():
                        with self._lock:
                            self.connected = False
                            self.last_error = "Failed to connect to data source"
                        time.sleep(2.0)
                        continue

                # Get device info (once)
                if self.device_info is None:
                    self.device_info = self.datasource.get_device_info(self.uid)

                # Get telemetry
                data = self.datasource.get_telemetry(self.uid)
                if data:
                    # Update stats if callback provided
                    if self.stats_callback:
                        for key, value in data.items():
                            if isinstance(value, (int, float)) and key != 'timestamp':
                                if key not in prev_data or value != prev_data[key]:
                                    self.stats_callback(self.uid, key, value)
                                    prev_data[key] = value

                    with self._lock:
                        self.connected = True
                        self.sensor_data = data
                        self.last_error = None
                        if self.connection_time is None:
                            self.connection_time = datetime.now()
                else:
                    with self._lock:
                        self.last_error = "No telemetry data received"

            except Exception as e:
                logger.warning(f"Error in DataSourceWorker: {e}")
                with self._lock:
                    self.connected = False
                    self.last_error = str(e)
                time.sleep(1.0)

            self._stop_event.wait(self.interval)

        logger.info(f"DataSourceWorker stopped for {self.uid}")