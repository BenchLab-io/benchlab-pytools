"""
Enhanced CSV Fleet Logger for BENCHLAB
Lightweight, robust, and cross-platform compatible
"""

import os
import configparser
import threading
import time
import types
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import logging

# Core imports
from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.statistics import ChannelStats, create_stats_callback
from benchlab.csv_log.message_batcher import BatchingLogger, create_csv_batcher
from benchlab.csv_log.smart_retry import SmartRetryManager, RetryConfig

# ----------------------------------------------------------------------
# Configuration and Data Classes
# ----------------------------------------------------------------------


@dataclass
class LoggerConfig:
    """Configuration for the CSV logger."""
    interval: float = 1.0
    output_dir: str = "logs"
    buffer_size: int = 100
    format: str = "csv"          # csv | json
    silent_mode: bool = False
    auto_select: bool = False


class DeviceConfig:
    """Configuration for a single device."""

    def __init__(
        self,
        port: str,
        uid: str,
        firmware: str = "?",
        enabled: bool = True,
        last_seen: Optional[datetime] = None,
    ):
        self.port = port
        self.uid = uid
        self.firmware = firmware
        self.enabled = enabled
        self.last_seen = last_seen


# ----------------------------------------------------------------------
# EnhancedCSVLogger
# ----------------------------------------------------------------------

class EnhancedCSVLogger:
    """Enhanced CSV logger with DataSourceManager and batching integration."""

    def __init__(self, config: LoggerConfig, args=None):
        """
        Parameters
        ----------
        config:
            LoggerConfig instance.
        args:
            Standard benchlab args namespace.  Fields used:
                source      – "direct" | "fastapi" | "mqtt"
                interval    – poll interval in seconds
                api_url     – FastAPI base URL
                mqtt_broker – MQTT broker host
                mqtt_port   – MQTT broker port
            May be None for direct mode.
        """
        self.config = config
        self.args = args
        self.selected_uids: List[str] = []
        self.stats = ChannelStats()
        self.batcher: Optional[BatchingLogger] = None
        self.logging_active = False
        self._stop_event = threading.Event()
        self.logging_thread: Optional[threading.Thread] = None
        self.manager: Optional[DataSourceManager] = None
        self._setup_logging()
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop_logging()

    # ------------------------------------------------------------------
    # Internal setup
    # ------------------------------------------------------------------

    def _setup_logging(self):
        level = logging.WARNING if self.config.silent_mode else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s")

    # ------------------------------------------------------------------
    # Device discovery and selection
    # ------------------------------------------------------------------

    def discover_devices(self) -> List[DeviceConfig]:
        """Discover devices via the DataSourceManager."""
        if not self.manager:
            return []
        devices_info = self.manager.list_devices()
        return [
            DeviceConfig(
                port=info.get("port", "unknown"),
                uid=uid,
                firmware=info.get("firmware", "?"),
            )
            for uid, info in devices_info.items()
        ]

    def select_devices(
            self,
            devices: List[DeviceConfig]) -> List[DeviceConfig]:
        """Select devices to log.

        Auto-selects all devices when ``config.auto_select`` or
        ``config.silent_mode`` is set — never blocks on stdin in those cases.
        """
        if not devices:
            logging.error("No devices available for logging")
            return []

        if self.config.auto_select or self.config.silent_mode:
            logging.info(f"Auto-selecting all {len(devices)} device(s)")
            return devices

        print("\n--- Available Devices ---")
        for i, dev in enumerate(devices, 1):
            print(
                f"  {i}: Port: {
                    dev.port:<12} UID: {
                    dev.uid}  FW: {
                    dev.firmware}")

        selection = input(
            "\nEnter device numbers (comma-separated), 'all', "
            "or Enter for all: "
        ).strip().lower()

        if not selection or selection == "all":
            return devices

        try:
            indices = [int(s.strip()) - 1 for s in selection.split(",")]
            return [devices[i] for i in indices if 0 <= i < len(devices)]
        except (ValueError, IndexError):
            logging.error("Invalid selection")
            return []

    # ------------------------------------------------------------------
    # Batcher
    # ------------------------------------------------------------------

    def create_batcher(self):
        """Create a batching logger for buffered CSV writes."""
        self.batcher = create_csv_batcher(
            output_dir=self.config.output_dir,
            batch_size=self.config.buffer_size,
            flush_interval=5.0,
        )
        return self.batcher

    # ------------------------------------------------------------------
    # Device data logging
    # ------------------------------------------------------------------

    def log_device_data(self, uid: str) -> bool:
        """Collect telemetry for a device and buffer it via batcher."""
        if not self.manager:
            logging.warning(f"No manager for device {uid}")
            return False
        try:
            self.manager.select_device(uid)
            snapshot = self.manager.snapshot()
            data = snapshot.get("sensor_data")
            if not data:
                logging.warning(f"No telemetry for device {uid}")
                return False

            # Strip the device-supplied 'timestamp' key (case-insensitive) to
            # avoid a duplicate column alongside our own 'Timestamp'.
            data = {k: v for k, v in data.items() if k.lower() != "timestamp"}
            row = {"Timestamp": datetime.now().isoformat(), "uid": uid, **data}

            if self.batcher:
                self.batcher.add_message(row)
            else:
                logging.debug(f"Data for {uid}: {row}")

            if not self.config.silent_mode:
                logging.debug(
                    f"[{uid}] SYS:{data.get('SYS_Power', 0):.0f}W  "
                    f"CPU:{data.get('CPU_Power', 0):.0f}W  "
                    f"GPU:{data.get('GPU_Power', 0):.0f}W"
                )
            return True
        except Exception as e:
            logging.error(f"Error logging data for {uid}: {e}")
            return False

    # ------------------------------------------------------------------
    # Connection with retry
    # ------------------------------------------------------------------

    def _connect_with_retry(self, manager: DataSourceManager) -> bool:
        retry_cfg = RetryConfig(max_retries=3, base_delay=1.0)
        try:
            SmartRetryManager(retry_cfg).execute(lambda: manager.connect())
            return True
        except Exception as e:
            logging.error(f"Connection failed after retries: {e}")
            return False

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def start_logging(self):
        """Connect to datasource, discover devices, and begin logging."""
        args = self.args
        source_type = (args.source if args else None) or "direct"

        ds_kwargs: Dict[str, Any] = {}
        if source_type in ("fastapi", "fastapi_custom") and args:
            ds_kwargs["base_url"] = args.api_url
        elif source_type == "mqtt" and args:
            ds_kwargs["broker"] = args.mqtt_broker
            ds_kwargs["port"] = args.mqtt_port

        self.manager = DataSourceManager(
            source_type=source_type,
            stats_callback=create_stats_callback(self.stats),
            **ds_kwargs,
        )

        if not self._connect_with_retry(self.manager):
            logging.error("Failed to connect to datasource")
            return

        discovered = self.discover_devices()
        selected = self.select_devices(discovered)
        if not selected:
            logging.error("No devices selected")
            return
        self.selected_uids = [d.uid for d in selected]

        self.create_batcher()

        log_path = Path(self.config.output_dir).resolve()
        print(f"\nLogging to: {log_path}")
        if not self.config.silent_mode:
            print("Press Ctrl+C to stop.\n")

        self.logging_active = True
        self._stop_event.clear()

        try:
            while not self._stop_event.is_set():
                # Sleep in small chunks so Ctrl+C is responsive on Windows
                deadline = time.monotonic() + self.config.interval
                while time.monotonic() < deadline:
                    if self._stop_event.is_set():
                        break
                    time.sleep(min(0.1, deadline - time.monotonic()))

                if self._stop_event.is_set():
                    break

                if self.batcher:
                    self.batcher.flush()
                for uid in self.selected_uids:
                    self.log_device_data(uid)

        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.stop_logging()

    def stop_logging(self):
        """Stop logging, flush buffers, and disconnect."""
        if not self.logging_active:
            return   # already stopped
        self.logging_active = False
        self._stop_event.set()
        if self.batcher:
            self.batcher.shutdown()
        if self.manager:
            self.manager.disconnect()
        logging.info("Logging stopped")


# ----------------------------------------------------------------------
# Configuration loader
# ----------------------------------------------------------------------

def load_config(config_file: str = "csv_logger.config") -> LoggerConfig:
    """Load configuration from file and/or environment variables."""
    config = LoggerConfig()

    if os.path.exists(config_file):
        parser = configparser.ConfigParser()
        parser.read(config_file)
        if "logger" in parser:
            s = parser["logger"]
            config.interval = float(s.get("interval", config.interval))
            config.output_dir = s.get("output_dir", config.output_dir)
            config.buffer_size = int(s.get("buffer_size", config.buffer_size))
            config.format = s.get("format", config.format)
            config.silent_mode = s.getboolean(
                "silent_mode", config.silent_mode)
            config.auto_select = s.getboolean(
                "auto_select", config.auto_select)

    config.interval = float(os.getenv("CSV_LOG_INTERVAL", config.interval))
    config.output_dir = os.getenv("CSV_LOG_OUTPUT_DIR", config.output_dir)
    config.buffer_size = int(
        os.getenv(
            "CSV_LOG_BUFFER_SIZE",
            config.buffer_size))
    config.silent_mode = os.getenv(
        "CSV_LOG_SILENT", str(
            config.silent_mode)).lower() == "true"
    config.auto_select = os.getenv(
        "CSV_LOG_AUTO_SELECT", str(
            config.auto_select)).lower() == "true"

    return config


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def run_enhanced_csv_logger(args=None):
    """Run the enhanced CSV logger.

    Parameters
    ----------
    args:
        Standard benchlab args namespace.  If None, runs in direct mode
        with default settings (backwards-compatible).
    """
    print("Running Enhanced BENCHLAB CSV fleet logger...\n")

    # Build a default args if called without one (e.g. from old code paths)
    if args is None:
        args = types.SimpleNamespace(
            source="direct",
            interval=1.0,
            api_url="http://127.0.0.1:8000",
            mqtt_broker="localhost",
            mqtt_port=1883,
        )

    config = load_config()
    config.interval = args.interval

    if os.getenv("BENCHLAB_AUTO_SELECT", "").lower() == "true":
        config.auto_select = True

    with EnhancedCSVLogger(config, args=args) as logger:
        logger.start_logging()


# ----------------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import argparse as _argparse

    _parser = _argparse.ArgumentParser(
        description="Enhanced BENCHLAB CSV Fleet Logger")
    _parser.add_argument("-i", "--interval", type=float, default=1.0,
                         help="Logging interval in seconds")
    _parser.add_argument("-c", "--config", default="csv_logger.config",
                         help="Configuration file path")
    _parser.add_argument("--source", default="direct",
                         choices=["direct", "fastapi", "mqtt"],
                         help="Data source")
    _parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    _parser.add_argument("--mqtt-broker", default="localhost")
    _parser.add_argument("--mqtt-port", type=int, default=1883)
    _parser.add_argument("--silent", action="store_true")
    _parser.add_argument("--auto-select", action="store_true")
    _cli_args = _parser.parse_args()

    _config = load_config(_cli_args.config)
    _config.interval = _cli_args.interval
    _config.silent_mode = _cli_args.silent
    _config.auto_select = _cli_args.auto_select

    _args = types.SimpleNamespace(
        source=_cli_args.source,
        interval=_cli_args.interval,
        api_url=_cli_args.api_url,
        mqtt_broker=_cli_args.mqtt_broker,
        mqtt_port=_cli_args.mqtt_port,
    )

    print("Running Enhanced BENCHLAB CSV fleet logger...\n")
    EnhancedCSVLogger(_config, args=_args).start_logging()
