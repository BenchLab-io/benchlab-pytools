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

# Import core functionality
try:
    from benchlab_pycore.core import read_sensors, read_uid, read_device, get_benchlab_ports, translate_sensor_struct
except ImportError:
    print("Error: benchlab_pycore not available. Please install dependencies.")
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
        self.device_connections: Dict[str, serial.Serial] = {}
        self.writers: Dict[str, Any] = {}
        self.files: Dict[str, Any] = {}
        self.logging_active = False
        self.buffer_lock = threading.Lock()
        self.data_buffers: Dict[str, List[Dict]] = {}
        
        # Setup logging
        self.setup_logging()
        
        # Ensure output directory exists
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        
    def setup_logging(self):
        """Setup logging configuration"""
        if self.config.silent_mode:
            logging.getLogger().setLevel(logging.WARNING)
        else:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )
    
    def discover_devices(self) -> List[DeviceConfig]:
        """Discover all BENCHLAB devices with enhanced error handling"""
        discovered_devices = []
        ports = get_benchlab_ports()
        
        if not ports:
            logging.warning("No BENCHLAB ports found")
            return discovered_devices
            
        for port_info in ports:
            port = port_info.get("port")
            if not port:
                continue
                
            device_config = self._probe_device(port)
            if device_config:
                discovered_devices.append(device_config)
                
        return discovered_devices
    
    def _probe_device(self, port: str) -> Optional[DeviceConfig]:
        """Probe a single port for BENCHLAB device"""
        for attempt in range(self.config.retry_attempts):
            try:
                ser = serial.Serial(port, baudrate=115200, timeout=1)
                uid = read_uid(ser)
                device_info = read_device(ser)
                fw = device_info.get("FwVersion", "?") if device_info else "?"
                ser.close()
                
                if uid and uid != "?":
                    device_config = DeviceConfig(
                        port=port,
                        uid=uid,
                        firmware=fw,
                        last_seen=datetime.now()
                    )
                    logging.info(f"Found device: {uid} on {port} (FW: {fw})")
                    return device_config
                else:
                    logging.warning(f"No valid UID on {port}")
                    return None
                    
            except serial.SerialException as e:
                logging.warning(f"Serial error on {port} (attempt {attempt + 1}): {e}")
                if attempt < self.config.retry_attempts - 1:
                    time.sleep(self.config.retry_delay * (2 ** attempt))  # Exponential backoff
            except Exception as e:
                logging.error(f"Unexpected error probing {port}: {e}")
                break
                
        return None
    
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
    
    def open_connections(self, devices: List[DeviceConfig]) -> Dict[str, serial.Serial]:
        """Open serial connections with retry logic"""
        connections = {}
        
        for device in devices:
            for attempt in range(self.config.retry_attempts):
                try:
                    ser = serial.Serial(device.port, baudrate=115200, timeout=0.5)
                    connections[device.uid] = ser
                    logging.info(f"Connected to {device.uid} on {device.port}")
                    break
                except serial.SerialException as e:
                    logging.warning(f"Failed to open {device.port} (attempt {attempt + 1}): {e}")
                    if attempt < self.config.retry_attempts - 1:
                        time.sleep(self.config.retry_delay * (2 ** attempt))
                except Exception as e:
                    logging.error(f"Unexpected error opening {device.port}: {e}")
                    break
                    
        return connections
    
    def initialize_writers(self, connections: Dict[str, serial.Serial]):
        """Initialize CSV writers with buffering"""
        for uid, ser in connections.items():
            try:
                # Get initial sensor data for headers
                data = translate_sensor_struct(read_sensors(ser))
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
    
    def log_device_data(self, uid: str, ser: serial.Serial):
        """Log data for a single device with error handling"""
        try:
            data = translate_sensor_struct(read_sensors(ser))
            timestamp = datetime.now().isoformat()
            row = {"Timestamp": timestamp, **data}
            
            # Add to buffer
            with self.buffer_lock:
                self.data_buffers[uid].append(row)
                
            # Write if buffer is full
            if len(self.data_buffers[uid]) >= self.config.buffer_size:
                self.write_buffered_data(uid)
                
            # Console summary
            if not self.config.silent_mode:
                sys_power = data.get("SYS_Power", 0)
                cpu_power = data.get("CPU_Power", 0)
                gpu_power = data.get("GPU_Power", 0)
                print(f"[{uid}] SYS:{sys_power:.0f}W CPU:{cpu_power:.0f}W GPU:{gpu_power:.0f}W",
                      end="\r", flush=True)
                      
        except serial.SerialException as e:
            logging.warning(f"Serial error reading {uid}: {e}")
            return False  # Signal connection issue
        except Exception as e:
            logging.error(f"Error logging data for {uid}: {e}")
            
        return True
    
    def monitor_connections(self, connections: Dict[str, serial.Serial]):
        """Monitor and reconnect dropped connections"""
        while self.logging_active:
            for uid, ser in list(connections.items()):
                if not ser.is_open:
                    logging.warning(f"Connection lost for {uid}, attempting reconnection")
                    device_config = self.devices.get(uid)
                    if device_config:
                        reconnected_ser = self._reconnect_device(device_config)
                        if reconnected_ser:
                            connections[uid] = reconnected_ser
                            logging.info(f"Reconnected to {uid}")
                        else:
                            logging.error(f"Failed to reconnect to {uid}")
            time.sleep(5)  # Check every 5 seconds
    
    def _reconnect_device(self, device_config: DeviceConfig) -> Optional[serial.Serial]:
        """Attempt to reconnect to a device"""
        for attempt in range(self.config.retry_attempts):
            try:
                ser = serial.Serial(device_config.port, baudrate=115200, timeout=0.5)
                # Verify it's still the same device
                uid = read_uid(ser)
                if uid == device_config.uid:
                    return ser
                else:
                    ser.close()
                    logging.warning(f"Device changed on {device_config.port}")
                    return None
            except Exception as e:
                logging.warning(f"Reconnection attempt {attempt + 1} failed for {uid}: {e}")
                if attempt < self.config.retry_attempts - 1:
                    time.sleep(self.config.retry_delay * (2 ** attempt))
        return None
    
    def start_logging(self):
        """Start the enhanced logging process"""
        # Discover devices
        discovered_devices = self.discover_devices()
        if not discovered_devices:
            logging.error("No devices found")
            return
            
        # Select devices
        selected_devices = self.select_devices(discovered_devices)
        if not selected_devices:
            logging.error("No devices selected")
            return
            
        # Store device configs
        for device in selected_devices:
            self.devices[device.uid] = device
            
        # Open connections
        connections = self.open_connections(selected_devices)
        if not connections:
            logging.error("No connections established")
            return
            
        # Initialize writers
        self.initialize_writers(connections)
        
        # Start logging threads
        self.logging_active = True
        logging_thread = threading.Thread(target=self._logging_loop, args=(connections,))
        monitor_thread = threading.Thread(target=self.monitor_connections, args=(connections,))
        
        logging_thread.daemon = True
        monitor_thread.daemon = True
        
        logging_thread.start()
        monitor_thread.start()
        
        try:
            print(f"\nLogging started. Press Ctrl+C to stop.")
            while self.logging_active:
                time.sleep(0.5)
                # Periodically flush buffers
                for uid in connections.keys():
                    if len(self.data_buffers.get(uid, [])) > 0:
                        self.write_buffered_data(uid)
                        
        except KeyboardInterrupt:
            logging.info("Stopping logging...")
            self.stop_logging(connections)
            
    def _logging_loop(self, connections: Dict[str, serial.Serial]):
        """Main logging loop with enhanced error handling"""
        while self.logging_active:
            for uid, ser in connections.items():
                if not self.logging_active:
                    break
                    
                success = self.log_device_data(uid, ser)
                if not success:
                    # Connection lost, remove from active connections
                    if uid in connections:
                        del connections[uid]
                        
            time.sleep(self.config.interval)
    
    def stop_logging(self, connections: Dict[str, serial.Serial]):
        """Stop logging and cleanup resources"""
        self.logging_active = False
        
        # Flush remaining buffers
        for uid in connections.keys():
            self.write_buffered_data(uid)
            
        # Close files
        for f in self.files.values():
            try:
                f.close()
            except Exception as e:
                logging.error(f"Error closing file: {e}")
                
        # Close serial connections
        for ser in connections.values():
            try:
                ser.close()
            except Exception as e:
                logging.error(f"Error closing serial connection: {e}")
                
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