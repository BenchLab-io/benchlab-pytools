"""
Enhanced CSV Fleet Logger for BENCHLAB
Lightweight, robust, and cross-platform compatible
"""

import serial
import serial.tools.list_ports
import csv
import threading
import time
import json
import os
import sys
import configparser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
import logging

# Import core functionality and DataSource
try:
    from benchlab_pycore.core import translate_sensor_struct
    from benchlab.core.datasource import DataSource, DirectDataSource, FastAPIDataSource, MQTTDataSource
    from benchlab.core.infrastructure import InfrastructureManager
except ImportError:
    print("Error: benchlab_pycore or benchlab.core not available. Please install dependencies.")
    sys.exit(1)

@dataclass
class DeviceConfig:
    """Configuration for a single device"""
    port: str
    uid: str
    firmware: str
    enabled: bool = True
    last_seen: Optional[datetime] = None

@dataclass
class LoggerConfig:
    """Configuration for the CSV logger"""
    interval: float = 1.0
    output_dir: str = "logs"
    buffer_size: int = 100
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    retry_attempts: int = 3
    retry_delay: float = 1.0
    format: str = "csv"  # csv, json
    timestamp_format: str = "%Y-%m-%d %H:%M:%S.%f"
    silent_mode: bool = False
    auto_select: bool = False
    selected_devices: List[str] = None

class EnhancedCSVLogger:
    """Enhanced CSV logger with improved error handling and performance"""
    
    def __init__(self, config: LoggerConfig):
        self.config = config
        self.devices: Dict[str, DeviceConfig] = {}
        self.data_sources: Dict[str, DataSource] = {}
        self.writers: Dict[str, Any] = {}
        self.files: Dict[str, Any] = {}
        self.logging_active = False
        self.buffer_lock = threading.Lock()
        self.data_buffers: Dict[str, List[Dict]] = {}
        
        # Setup logging
        self.setup_logging()
        
        # Ensure output directory exists
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        
        # Initialize infrastructure for shared services
        self.infrastructure = InfrastructureManager()
        self.selected_data_sources = {}
        self._data_source_types = {}  # Track which types have been created
        # Lazy initialization of data sources (PERF-1.1)
        
    def _get_or_create_data_source(self, source_type: str) -> Optional[DataSource]:
        """Lazy-initialize a data source on demand (PERF-1.1).
        
        Args:
            source_type: Type of data source ('direct', 'fastapi', 'mqtt')
            
        Returns:
            DataSource instance, or None if creation failed
        """
        if source_type in self.data_sources:
            return self.data_sources[source_type]
        
        if source_type in self._data_source_types:
            return None  # Already failed, don't retry
        
        source_classes = {
            'direct': DirectDataSource,
            'fastapi': FastAPIDataSource,
            'mqtt': MQTTDataSource,
        }
        
        cls = source_classes.get(source_type)
        if cls is None:
            logging.error("Unknown data source type: %s", source_type)
            self._data_source_types[source_type] = False
            return None
        
        try:
            ds = cls()
            self.data_sources[source_type] = ds
            self._data_source_types[source_type] = True
            logging.info("Lazy-initialized data source: %s", source_type)
            return ds
        except Exception as e:
            logging.error("Failed to initialize data source %s: %s", source_type, e)
            self._data_source_types[source_type] = False
            return None
    
    def _initialize_data_sources(self):
        """Initialize all available data source types (deprecated - use lazy init)."""
        # Only keep this for backward compatibility, but use lazy init instead
        logging.debug("_initialize_data_sources() deprecated, using lazy init")
    
    def setup_logging(self):
        """Setup logging configuration"""
        if self.config.silent_mode:
            logging.getLogger().setLevel(logging.WARNING)
        else:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )

    def discover_data_sources(self) -> List[DataSource]:
        """Discover all available data sources"""
        discovered_sources = []
        
        for ds in self.data_sources.values():
            discovered_sources.append(ds)
        
        return discovered_sources
    
    def discover_devices(self) -> List[DeviceConfig]:
        """Discover all BENCHLAB devices using data sources"""
        discovered_devices = []
        
        for ds in self.discover_data_sources():
            try:
                devices = ds.list_devices()
                for device in devices:
                    device_config = DeviceConfig(
                        port=device.get("port", "unknown"),
                        uid=device.get("uid", "unknown"),
                        firmware=device.get("firmware", "?"),
                        last_seen=datetime.now()
                    )
                    discovered_devices.append(device_config)
            except Exception as e:
                logging.warning(f"Failed to get devices from data source {ds}: {e}")
        
        return discovered_devices
    
    def _probe_data_source(self, data_source: DataSource) -> Optional[DeviceConfig]:
        """Probe a single data source for BENCHLAB device"""
        try:
            devices = data_source.get_devices()
            if devices:
                device = devices[0]  # Take the first device
                device_config = DeviceConfig(
                    port=device.get("port", "unknown"),
                    uid=device.get("uid", "unknown"),
                    firmware=device.get("firmware", "?"),
                    last_seen=datetime.now()
                )
                logging.info(f"Found device: {device_config.uid} via {data_source}")
                return device_config
        except Exception as e:
            logging.warning(f"Failed to probe data source {data_source}: {e}")
        
        return None

    def select_data_sources(self, data_sources: List[DataSource]) -> List[DataSource]:
        """Select data sources to use"""
        if not data_sources:
            logging.error("No data sources available")
            return []
            
        if self.config.auto_select or self.config.silent_mode:
            logging.info(f"Auto-selecting all {len(data_sources)} data sources")
            return data_sources
            
        # Interactive selection
        print("\n--- Available Data Sources ---")
        for i, ds in enumerate(data_sources, 1):
            print(f"{i}: Type: {type(ds).__name__} Config: {ds}")
            
        selection = input(
            "\nEnter data source numbers to use (comma-separated, e.g., 1,2), 'all', or press Enter for all: "
        ).strip().lower()
        
        if selection == "all" or not selection:
            return data_sources
        else:
            try:
                indices = [int(s.strip()) - 1 for s in selection.split(",")]
                selected = [data_sources[i] for i in indices if 0 <= i < len(data_sources)]
                if selected:
                    return selected
            except (ValueError, IndexError):
                pass
                
        logging.error("Invalid selection")
        return []
    
    def select_devices(self, devices: List[DeviceConfig]) -> List[DeviceConfig]:
        """Select devices to log with enhanced selection logic"""
        if not devices:
            logging.error("No devices available for logging")
            return []
            
        if self.config.auto_select or self.config.silent_mode:
            logging.info(f"Auto-selecting all {len(devices)} devices")
            return devices
            
        # Interactive selection
        print("\n--- Available Devices ---")
        for i, dev in enumerate(devices, 1):
            print(f"{i}: Port: {dev.port:<12} UID: {dev.uid} FW: {dev.firmware}")
            
        selection = input(
            "\nEnter device numbers to log (comma-separated, e.g., 1,2), 'all', or press Enter for all: "
        ).strip().lower()
        
        if selection == "all" or not selection:
            return devices
        else:
            try:
                indices = [int(s.strip()) - 1 for s in selection.split(",")]
                selected = [devices[i] for i in indices if 0 <= i < len(devices)]
                if selected:
                    return selected
            except (ValueError, IndexError):
                pass
                
        logging.error("Invalid selection")
        return []
    
    def open_data_source_connections(self, data_sources: List[DataSource]) -> Dict[str, DataSource]:
        """Open connections to data sources"""
        connections = {}
        
        for ds in data_sources:
            try:
                ds.connect()
                # Use a unique identifier for the data source
                identifier = f"{ds.source_type}_{id(ds)}"
                connections[identifier] = ds
                logging.info(f"Connected to data source: {ds.source_type}")
            except Exception as e:
                logging.warning(f"Failed to connect to data source {ds.source_type}: {e}")
                continue
        
        return connections
    
    def open_device_connections(self, devices: List[DeviceConfig]) -> Dict[str, DataSource]:
        """Open connections to devices using data sources"""
        connections = {}
        
        for device in devices:
            # Find a data source that can handle this device
            for ds in self.selected_data_sources.values():
                try:
                    # Handle DirectDataSource differently - it connects to all devices automatically
                    if ds.source_type == "direct":
                        # DirectDataSource already handles all devices when connected
                        connections[device.uid] = ds
                        logging.info(f"Connected to device {device.uid} via DirectDataSource")
                        break
                    else:
                        # For FastAPI and MQTT data sources, connect to specific device
                        ds.connect_device(device.uid)
                        connections[device.uid] = ds
                        logging.info(f"Connected to device {device.uid} via data source {ds}")
                        break
                except Exception as e:
                    logging.debug(f"Data source {ds} cannot handle device {device.uid}: {e}")
                    continue
        
        return connections
    
    def initialize_writers(self, connections: Dict[str, DataSource]):
        """Initialize CSV writers with buffering"""
        for uid, ds in connections.items():
            try:
                # Get initial sensor data for headers
                data = self.get_sensor_data(ds, uid)
                timestamp = datetime.now().isoformat()
                row = {"Timestamp": timestamp, **data}
                
                # Create filename
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"log_{ts}_{uid}.{self.config.format}"
                filepath = Path(self.config.output_dir) / filename
                
                # Initialize buffer
                self.data_buffers[uid] = []
                
                if self.config.format == "csv":
                    f = open(filepath, "w", newline="", encoding="utf-8")
                    writer = csv.DictWriter(f, fieldnames=["Timestamp"] + list(data.keys()))
                    writer.writeheader()
                    self.files[uid] = f
                    self.writers[uid] = writer
                elif self.config.format == "json":
                    f = open(filepath, "w", encoding="utf-8")
                    self.files[uid] = f
                    self.writers[uid] = f  # For JSON, writer is the file itself
                    
                logging.info(f"Started logging {uid} -> {filepath}")
                
            except Exception as e:
                logging.error(f"Failed to initialize writer for {uid}: {e}")
    
    def write_buffered_data(self, uid: str):
        """Write buffered data to file"""
        if uid not in self.data_buffers or not self.data_buffers[uid]:
            return
            
        with self.buffer_lock:
            data_to_write = self.data_buffers[uid].copy()
            self.data_buffers[uid].clear()
            
        try:
            if self.config.format == "csv":
                writer = self.writers[uid]
                for row in data_to_write:
                    writer.writerow(row)
                self.files[uid].flush()
            elif self.config.format == "json":
                file = self.writers[uid]
                for row in data_to_write:
                    file.write(json.dumps(row) + "\n")
                file.flush()
                
        except Exception as e:
            logging.error(f"Failed to write buffered data for {uid}: {e}")
    
    def get_sensor_data(self, data_source: DataSource, uid: str) -> Dict:
        """Get sensor data from a data source"""
        try:
            data = data_source.get_telemetry(uid)
            if not data:
                # Try to refresh data
                data_source.refresh()
                data = data_source.get_telemetry(uid)
            return data if data else {}
        except Exception as e:
            logging.debug(f"Failed to get sensor data for {uid} from {data_source}: {e}")
            return {}

    def log_device_data(self, uid: str):
        """Log data for a single device with error handling"""
        try:
            # Get the data source for this device
            # The device connections are stored in selected_data_sources after open_device_connections
            data_source = self.selected_data_sources.get(uid)
            if not data_source:
                logging.warning(f"No data source found for device {uid}")
                return False
            
            data = self.get_sensor_data(data_source, uid)
            if not data:
                logging.warning(f"No data available for device {uid}")
                return False
            
            timestamp = datetime.now().isoformat()
            row = {"Timestamp": timestamp, **data}
            
            # Add to buffer
            with self.buffer_lock:
                self.data_buffers[uid].append(row)
                
            # Write if buffer is full
            if len(self.data_buffers[uid]) >= self.config.buffer_size:
                self.write_buffered_data(uid)
                
            # Console summary (PERF-4.1 fix: replaced print+\r with logging)
            if not self.config.silent_mode:
                sys_power = data.get("SYS_Power", 0)
                cpu_power = data.get("CPU_Power", 0)
                gpu_power = data.get("GPU_Power", 0)
                logging.debug(f"[{uid}] SYS:{sys_power:.0f}W CPU:{cpu_power:.0f}W GPU:{gpu_power:.0f}W")
                      
            return True
                      
        except Exception as e:
            logging.error(f"Error logging data for {uid}: {e}")
            return False
    
    def monitor_connections(self):
        """Monitor and reconnect dropped connections"""
        while self.logging_active:
            for uid, data_source in list(self.selected_data_sources.items()):
                try:
                    if not data_source.is_connected():
                        logging.warning(f"Connection lost for {uid}, attempting reconnection")
                        data_source.connect()
                        logging.info(f"Reconnected to {uid}")
                except Exception as e:
                    logging.debug(f"Reconnection attempt for {uid} failed: {e}")
            time.sleep(5)  # Check every 5 seconds
    
    def _reconnect_data_source(self, data_source: DataSource) -> bool:
        """Attempt to reconnect to a data source"""
        try:
            data_source.connect()
            return True
        except Exception as e:
            logging.warning(f"Reconnection attempt failed for {data_source}: {e}")
            return False
    
    def start_logging(self):
        """Start the enhanced logging process"""
        # Discover data sources
        discovered_data_sources = self.discover_data_sources()
        if not discovered_data_sources:
            logging.error("No data sources found")
            return
            
        # Select data sources
        selected_data_sources = self.select_data_sources(discovered_data_sources)
        if not selected_data_sources:
            logging.error("No data sources selected")
            return
            
        # Store selected data sources with both identifier and device mappings
        self.selected_data_sources = {}  # Reset to store device mappings
        self.data_source_identifiers = {}  # Store original identifier mappings
        
        for ds in selected_data_sources:
            identifier = f"{ds.source_type}_{id(ds)}"
            self.data_source_identifiers[identifier] = ds
            # Also store in selected_data_sources for device connection
            self.selected_data_sources[identifier] = ds
        
        # Discover devices from selected data sources
        discovered_devices = []
        for ds in selected_data_sources:
            try:
                devices = ds.list_devices()
                for device in devices:
                    device_config = DeviceConfig(
                        port=device.get("port", "unknown"),
                        uid=device.get("uid", "unknown"),
                        firmware=device.get("firmware", "?"),
                        last_seen=datetime.now()
                    )
                    discovered_devices.append(device_config)
            except Exception as e:
                logging.warning(f"Failed to get devices from data source {ds}: {e}")
        
        if not discovered_devices:
            logging.error("No devices found from selected data sources")
            return
        
        # Select devices
        selected_devices = self.select_devices(discovered_devices)
        if not selected_devices:
            logging.error("No devices selected")
            return
        
        # Store device configs
        for device in selected_devices:
            self.devices[device.uid] = device
        
        # Open connections to data sources
        data_source_connections = self.open_data_source_connections(selected_data_sources)
        if not data_source_connections:
            logging.error("No data source connections established")
            return
        
        # Open connections to devices via data sources
        device_connections = self.open_device_connections(selected_devices)
        if not device_connections:
            logging.error("No device connections established")
            return
        
        # Store device-to-data-source mapping for logging
        # Use separate variable to avoid losing data source identifier mappings
        self.device_connections = device_connections
        # Also update selected_data_sources with device mappings for log_device_data
        for uid, ds in device_connections.items():
            self.selected_data_sources[uid] = ds
        
        # Initialize writers
        self.initialize_writers(device_connections)
        
        # Start logging threads
        self.logging_active = True
        logging_thread = threading.Thread(target=self._logging_loop)
        monitor_thread = threading.Thread(target=self.monitor_connections)
        
        logging_thread.daemon = True
        monitor_thread.daemon = True
        
        logging_thread.start()
        monitor_thread.start()
        
        try:
            print(f"\nLogging started. Press Ctrl+C to stop.")
            while self.logging_active:
                time.sleep(0.5)
                # Periodically flush buffers
                for uid in self.data_buffers.keys():
                    if len(self.data_buffers.get(uid, [])) > 0:
                        self.write_buffered_data(uid)
                        
        except KeyboardInterrupt:
            logging.info("Stopping logging...")
            self.stop_logging()
            
    def _logging_loop(self):
        """Main logging loop with enhanced error handling"""
        while self.logging_active:
            for uid in self.devices.keys():
                if not self.logging_active:
                    break
                    
                success = self.log_device_data(uid)
                if not success:
                    # Connection lost, remove from active connections
                    if uid in self.selected_data_sources:
                        del self.selected_data_sources[uid]
                        
            time.sleep(self.config.interval)
    
    def stop_logging(self):
        """Stop logging and cleanup resources"""
        self.logging_active = False
        
        # Wait for logging threads to finish (with timeout)
        current_threads = [t for t in threading.enumerate() 
                          if t.name in ("LoggingLoop", "ConnectionMonitor")]
        for thread in current_threads:
            thread.join(timeout=5)
            if thread.is_alive():
                logging.warning(f"Thread {thread.name} did not stop gracefully")
        
        # Flush remaining buffers
        for uid in list(self.data_buffers.keys()):
            self.write_buffered_data(uid)
            
        # Close files
        for uid, f in list(self.files.items()):
            try:
                if not f.closed:
                    f.flush()
                    f.close()
            except Exception as e:
                logging.error(f"Error closing file for {uid}: {e}")
                
        # Disconnect data sources
        for uid, ds in list(self.device_connections.items()):
            try:
                ds.disconnect()
            except Exception as e:
                logging.error(f"Error disconnecting data source for {uid}: {e}")
        
        # Also disconnect data source identifiers
        for identifier, ds in list(self.data_source_identifiers.items()):
            try:
                if ds.is_connected():
                    ds.disconnect()
            except Exception as e:
                logging.error(f"Error disconnecting data source {identifier}: {e}")
                
        logging.info("Logging stopped")

def load_config(config_file: str = "csv_logger.config") -> LoggerConfig:
    """Load configuration from file or environment variables"""
    config = LoggerConfig()
    
    # Check for config file
    if os.path.exists(config_file):
        parser = configparser.ConfigParser()
        parser.read(config_file)
        
        if "logger" in parser:
            section = parser["logger"]
            config.interval = float(section.get("interval", config.interval))
            config.output_dir = section.get("output_dir", config.output_dir)
            config.buffer_size = int(section.get("buffer_size", config.buffer_size))
            config.max_file_size = int(section.get("max_file_size", config.max_file_size))
            config.retry_attempts = int(section.get("retry_attempts", config.retry_attempts))
            config.retry_delay = float(section.get("retry_delay", config.retry_delay))
            config.format = section.get("format", config.format)
            config.timestamp_format = section.get("timestamp_format", config.timestamp_format)
            config.silent_mode = section.getboolean("silent_mode", config.silent_mode)
            config.auto_select = section.getboolean("auto_select", config.auto_select)
    
    # Override with environment variables
    config.interval = float(os.getenv("CSV_LOG_INTERVAL", config.interval))
    config.output_dir = os.getenv("CSV_LOG_OUTPUT_DIR", config.output_dir)
    config.buffer_size = int(os.getenv("CSV_LOG_BUFFER_SIZE", config.buffer_size))
    config.silent_mode = os.getenv("CSV_LOG_SILENT", str(config.silent_mode)).lower() == "true"
    config.auto_select = os.getenv("CSV_LOG_AUTO_SELECT", str(config.auto_select)).lower() == "true"
    
    return config

def run_enhanced_csv_logger(interval: float = 1.0, config_file: str = "csv_logger.config"):
    """Run the enhanced CSV logger"""
    print("Running Enhanced BENCHLAB CSV fleet logger...\n")
    
    # Load configuration
    config = load_config(config_file)
    config.interval = interval  # Override with function parameter
    
    # Create and run logger
    logger = EnhancedCSVLogger(config)
    logger.start_logging()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Enhanced BENCHLAB CSV Fleet Logger")
    parser.add_argument("-i", "--interval", type=float, default=1.0, help="Logging interval in seconds")
    parser.add_argument("-c", "--config", default="csv_logger.config", help="Configuration file path")
    parser.add_argument("--silent", action="store_true", help="Run in silent mode")
    parser.add_argument("--auto-select", action="store_true", help="Auto-select all devices")
    
    args = parser.parse_args()
    
    # Override config with command line args
    config = load_config(args.config)
    config.interval = args.interval
    config.silent_mode = args.silent
    config.auto_select = args.auto_select
    
    print("Running Enhanced BENCHLAB CSV fleet logger...\n")
    
    logger = EnhancedCSVLogger(config)
    logger.start_logging()