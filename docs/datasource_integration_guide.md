# DataSource Integration Guide for Benchlab Tools

> **Complete integration guide** for adding flexible datasource support to Benchlab tools using the refactored TUI architecture as a reference implementation.

**Version:** 2.1 (Refactored Architecture)  
**Last Updated:** 2026-04-11  
**Reference Implementation:** `benchlab/tui` (refactored)

---

## Overview

This document explains how to implement the **DataSourceManager pattern** used in the refactored Benchlab TUI to create tools that can seamlessly consume telemetry from multiple source types:

- **Direct serial connection** – Direct communication with Benchlab device
- **FastAPI** – REST API server that proxies device data  
- **MQTT** – Lightweight pub/sub broker for streaming telemetry

The refactored architecture separates datasource management from UI concerns, making components reusable and maintainable.

---

## Architecture Comparison

### OLD Architecture (Legacy)
```
Tool
├─ DataSourceWorker (threading.Thread)
│  ├─ SerialWorker (direct mode)
│  └─ DataSourceWorker (FastAPI/MQTT mode)  
├─ UI Rendering Code
├─ Statistics Tracking 
└─ Configuration Constants
```
**Problems:** Monolithic files, mixed concerns, code duplication

### NEW Architecture (Refactored)
```
Tool Application
├─ DataSourceManager (unified datasource abstraction)
├─ ChannelStats (thread-safe statistics)
├─ Config (centralized configuration)  
└─ UI/Logic Core (pure business logic)
```
**Benefits:** Separation of concerns, reusable components, maintainable code

---

## Core Components

### 1. DataSourceManager

**Location:** `benchlab.core.datasource_manager` (reusable by any tool)

The `DataSourceManager` provides a unified interface to all datasource types with:
- Thread-safe telemetry access
- Background polling worker  
- Statistics collection callbacks
- Consistent snapshot API
- Parameter filtering for datasource compatibility

```python
from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.statistics import ChannelStats, create_stats_callback

# Create statistics tracking
stats = ChannelStats()
stats_callback = create_stats_callback(stats)

# Create manager for FastAPI
manager = DataSourceManager(
    source_type='fastapi',
    stats_callback=stats_callback,
    base_url='http://127.0.0.1:8000',
    timeout=5.0
)

# Connect and get data
if manager.connect():
    snapshot = manager.snapshot()
    devices = manager.list_devices()
```

### 2. ChannelStats

**Location:** `benchlab.core.statistics` (reusable statistics tracking)

Thread-safe per-device, per-channel statistics with min/max/average calculations:

```python
from benchlab.core.statistics import ChannelStats, StatsFormatter

stats = ChannelStats()

# Update statistics  
stats.update('device_uid', 'SYS_Power', 125.5)

# Get statistics
min_val, max_val, avg_val = stats.get('device_uid', 'SYS_Power')

# Format for display
formatted = StatsFormatter.format_stat_string(min_val, max_val, avg_val, 1, 'W')
# Returns: "  ↓125.5W     ↑125.5W     ~125.5W    "
```

### 3. Config Module

**Location:** `benchlab.tui.config` (centralized configuration)

All configuration constants, scales, thresholds in one place:

```python
from benchlab.tui.config import Config

# Bar scales for progress bars
max_power = Config.BarScales.POWER_MAX  # 1000.0W
temp_warn = Config.BarScales.CHIP_TEMP_WARN  # 70.0°C

# Color pairs for UI
ok_color = curses.color_pair(Config.COLOR_PAIRS['ok'])
error_color = curses.color_pair(Config.COLOR_PAIRS['error'])

# Channel definitions
rail_channels = Config.Channels.RAIL_CHANNELS
```

---

## Implementation Pattern

### Step 1: Tool Application Structure

Create a main application class that coordinates components:

```python
"""
My Tool - Clean implementation using DataSourceManager pattern
"""

import logging
from typing import Optional, Dict, Any

from benchlab.core.datasource_manager import DataSourceManager  
from benchlab.core.statistics import ChannelStats, create_stats_callback
from benchlab.tui.config import Config

logger = logging.getLogger(__name__)

class MyToolApplication:
    """Main application coordinating datasource, stats, and business logic."""
    
    def __init__(self, source_type: str = 'direct', **datasource_kwargs):
        """Initialize tool application.
        
        Args:
            source_type: 'direct', 'fastapi', or 'mqtt'
            **datasource_kwargs: Source-specific parameters
        """
        self.source_type = source_type
        
        # Create statistics tracking
        self.stats = ChannelStats()
        stats_callback = create_stats_callback(self.stats)
        
        # Create datasource manager
        self.datasource_manager = DataSourceManager(
            source_type=source_type,
            stats_callback=stats_callback,
            **datasource_kwargs
        )
        
        # Tool-specific components
        self.running = False

    def run(self):
        """Main application entry point."""
        try:
            # Auto-connect for network sources
            if self.source_type != 'direct':
                self._connect_datasource()
            
            # Main loop
            self.running = True
            while self.running:
                self._update_cycle()
                
        except KeyboardInterrupt:
            logger.info("Application interrupted")
        finally:
            self.cleanup()

    def _connect_datasource(self):
        """Connect to network datasource (FastAPI/MQTT)."""
        if self.datasource_manager.connect():
            uid = self.datasource_manager.get_selected_uid()
            logger.info(f"Connected via {self.source_type}, selected: {uid}")
        else:
            snapshot = self.datasource_manager.snapshot()
            error_msg = snapshot.get('last_error', f"Failed to connect to {self.source_type}")
            raise RuntimeError(f"Connection failed: {error_msg}")

    def _update_cycle(self):
        """Main update cycle - override in subclass."""
        # Get current data snapshot
        snapshot = self.datasource_manager.snapshot()
        
        # Process data (implement in subclass)
        self._process_snapshot(snapshot)

    def _process_snapshot(self, snapshot: Dict[str, Any]):
        """Process telemetry snapshot - implement in subclass."""
        raise NotImplementedError("Subclass must implement _process_snapshot")

    def cleanup(self):
        """Clean up resources."""
        self.running = False
        if self.datasource_manager:
            self.datasource_manager.disconnect()
```

### Step 2: Handle Direct Mode Device Selection

For tools that need device selection in direct mode:

```python
def connect_to_device(self, device: Dict[str, Any]):
    """Connect to a specific device from fleet scan."""
    port = device.get('port')
    uid = device.get('uid')
    
    if uid == "BUSY":
        raise RuntimeError("Device is busy (already connected elsewhere)")
    
    try:
        if self.source_type == 'direct':
            # Direct serial connection
            if self.datasource_manager.connect(port=port):
                logger.info(f"Connected to device on {port}")
            else:
                snapshot = self.datasource_manager.snapshot()
                error_msg = snapshot.get('last_error', 'Connection failed')
                raise RuntimeError(f"Connection failed: {error_msg}")
        else:
            # Network datasource - select different device
            if self.datasource_manager.select_device(uid):
                logger.info(f"Selected device {uid} via {self.source_type}")
            else:
                raise RuntimeError("Failed to select device")
                
    except Exception as e:
        logger.error(f"Connection error: {e}")
        raise

def scan_fleet(self) -> List[Dict[str, Any]]:
    """Scan for available devices."""
    if self.source_type != 'direct' and self.datasource_manager.is_connected():
        # Use datasource for network sources
        devices_dict = self.datasource_manager.list_devices()
        return self._convert_fleet_format(devices_dict)
    else:
        # Fall back to local scan for direct mode
        return self._scan_local_fleet()
```

### Step 3: Parameter Setup by Source Type

Configure parameters correctly for each datasource type:

```python
def get_datasource_kwargs(source_type: str, args) -> Dict[str, Any]:
    """Get appropriate parameters for datasource type."""
    
    # Common parameters
    kwargs = {
        'poll_interval': getattr(args, 'interval', 1.0),
    }
    
    # Source-specific parameters
    if source_type == 'fastapi':
        if hasattr(args, 'api_port'):
            kwargs['base_url'] = f"http://127.0.0.1:{args.api_port}"
        else:
            kwargs['base_url'] = "http://127.0.0.1:8000"
        kwargs['timeout'] = 5.0  # Request timeout
        
    elif source_type == 'mqtt':
        if hasattr(args, 'mqtt_broker'):
            kwargs['broker'] = args.mqtt_broker  
        else:
            kwargs['broker'] = 'localhost'
        if hasattr(args, 'mqtt_port'):
            kwargs['port'] = args.mqtt_port
        else:
            kwargs['port'] = 1883
        kwargs['timeout'] = 5.0  # Connection timeout
    
    return kwargs
```

---

## DataSourceManager API Reference

### Core Methods

```python
# Connection management
success = manager.connect(port=None, uid=None)  # Returns bool
manager.disconnect()
is_connected = manager.is_connected()

# Device management  
devices = manager.list_devices()  # Returns Dict[uid, device_info]
success = manager.select_device(uid)  # Switch active device

# Data access
snapshot = manager.snapshot()  # Get complete state snapshot
selected_uid = manager.get_selected_uid()  # Get active device UID
```

### Snapshot Format

The `snapshot()` method returns a consistent dictionary:

```python
{
    'connected': bool,              # Connection status
    'source_type': str,             # 'direct', 'fastapi', 'mqtt'  
    'source_desc': str,             # Human readable description
    'port': str,                    # Device port or broker info
    'uid': str,                     # Selected device UID
    'device_info': dict,            # Device metadata
    'sensor_data': dict,            # Latest telemetry data
    'sensor_struct': Any,           # Raw sensor struct (direct mode only)
    'connection_time': datetime,    # Connection timestamp
    'last_error': str,              # Last error message
    'all_devices': dict,            # All available devices
    'all_telemetry': dict,          # All telemetry data
}
```

---

## Statistics Integration

### Basic Usage

```python
# Create statistics tracker
stats = ChannelStats()

# Update with telemetry data (usually via callback)
stats.update('device_uid', 'SYS_Power', 125.5)
stats.update('device_uid', 'SYS_Power', 130.2)
stats.update('device_uid', 'Chip_Temp', 65.0)

# Get statistics for display
min_pwr, max_pwr, avg_pwr = stats.get('device_uid', 'SYS_Power')
# Returns: (125.5, 130.2, 127.85)

# Get all statistics for a device
all_stats = stats.get_all('device_uid')
# Returns: {'SYS_Power': (125.5, 130.2, 127.85), 'Chip_Temp': (65.0, 65.0, 65.0)}

# Reset statistics
stats.reset('device_uid')  # Reset one device
stats.reset()              # Reset all devices
```

### Formatting for Display

```python
from benchlab.core.statistics import StatsFormatter

# Format as progress bar statistics  
stat_str = StatsFormatter.format_stat_string(
    min_val=125.5, max_val=130.2, avg_val=127.85,
    decimals=1, unit='W', width=10
)
# Returns: "  ↓125.5W     ↑130.2W     ~127.9W    "

# Format as compact range
compact_str = StatsFormatter.format_compact_range(
    min_val=125.5, max_val=130.2, avg_val=127.85,
    decimals=1, unit='W'
)
# Returns: "125.5-130.2 ~127.9W"
```

### Automatic Statistics via Callback

The DataSourceManager can automatically update statistics:

```python
stats = ChannelStats()
stats_callback = create_stats_callback(stats)

manager = DataSourceManager(
    source_type='fastapi',
    stats_callback=stats_callback,  # Automatic updates
    base_url='http://127.0.0.1:8000'
)
```

---

## Configuration Usage

### Accessing Scales and Thresholds

```python
from benchlab.tui.config import Config

# Bar scales for progress bars
power_max = Config.BarScales.POWER_MAX        # 1000.0W
temp_max = Config.BarScales.CHIP_TEMP_MAX     # 100.0°C
temp_warn = Config.BarScales.CHIP_TEMP_WARN   # 70.0°C

# Voltage bands for status determination
rail_band = Config.VoltageBands.RAIL          # (10.0, 14.0)
vdd_band = Config.VoltageBands.VDD            # (3.0, 3.6)

# Channel definitions
power_channels = Config.Channels.POWER_SUMMARY
rail_channels = Config.Channels.RAIL_CHANNELS
vin_channels = Config.Channels.VIN_CHANNELS

# UI layout constants
bar_width = Config.Layout.BAR_WIDTH           # 20
fleet_cols = Config.Layout.FLEET_COLS
```

### Status Determination Functions

```python
# Get voltage status and color
status, color = Config.get_voltage_status(voltage=12.5, band=Config.VoltageBands.RAIL)
# Returns: ('OK  ', 2) where 2 is the color pair index

# Get thermal status and color  
status, color = Config.get_thermal_status(temperature=75.0)
# Returns: ('WARNING', 4)

# Get temperature display color
color = Config.get_temperature_color(temperature=75.0, warn_threshold=70.0)
# Returns: 3 (error color pair)
```

### Color Initialization

```python
# Initialize curses colors (call once at startup)
curses.start_color()
curses.use_default_colors()
Config.init_colors()

# Use color pairs
ok_color = curses.color_pair(Config.COLOR_PAIRS['ok'])
error_color = curses.color_pair(Config.COLOR_PAIRS['error'])
```

---

## Example Implementations

### Console Tool

Simple console tool that displays telemetry:

```python
"""
Console Telemetry Monitor
"""

import time
from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.statistics import ChannelStats, create_stats_callback

class ConsoleTelemetryTool:
    """Simple console telemetry display."""
    
    def __init__(self, source_type='direct', **kwargs):
        self.stats = ChannelStats()
        self.manager = DataSourceManager(
            source_type=source_type,
            stats_callback=create_stats_callback(self.stats),
            **kwargs
        )
    
    def run(self):
        if not self.manager.connect():
            print("Failed to connect to datasource")
            return
        
        print(f"Connected via {self.manager.source_type}")
        
        try:
            while True:
                self._display_telemetry()
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.manager.disconnect()
    
    def _display_telemetry(self):
        snapshot = self.manager.snapshot()
        
        if snapshot['connected'] and snapshot['sensor_data']:
            print("\n" + "="*50)
            print(f"Device: {snapshot['uid']} ({snapshot['source_type']})")
            
            for sensor, value in snapshot['sensor_data'].items():
                if isinstance(value, (int, float)):
                    min_val, max_val, avg_val = self.stats.get(snapshot['uid'], sensor)
                    if min_val is not None:
                        print(f"{sensor:20} {value:8.1f} (min:{min_val:.1f} max:{max_val:.1f} avg:{avg_val:.1f})")
                    else:
                        print(f"{sensor:20} {value:8.1f}")
```

### GUI Tool with tkinter

GUI tool using tkinter:

```python
"""
GUI Telemetry Monitor with tkinter
"""

import tkinter as tk
import threading
from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.statistics import ChannelStats, create_stats_callback

class TelemetryGUI:
    """GUI telemetry display with statistics."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Benchlab Telemetry Monitor")
        
        # Create UI
        self.status_label = tk.Label(self.root, text="Connecting...")
        self.status_label.pack()
        
        self.data_frame = tk.Frame(self.root)
        self.data_frame.pack(padx=10, pady=10)
        
        # Create datasource components
        self.stats = ChannelStats()
        self.manager = DataSourceManager(
            source_type='fastapi',  # Use FastAPI by default
            stats_callback=create_stats_callback(self.stats),
            base_url='http://127.0.0.1:8000'
        )
        
        # Start background thread
        self.running = False
        self.worker_thread = None
    
    def run(self):
        # Start background data collection
        self.running = True
        self.worker_thread = threading.Thread(target=self._data_worker, daemon=True)
        self.worker_thread.start()
        
        # Start GUI
        self.root.mainloop()
        
        # Cleanup
        self.running = False
        self.manager.disconnect()
    
    def _data_worker(self):
        """Background thread for data collection."""
        if not self.manager.connect():
            self.root.after(0, lambda: self.status_label.config(text="Connection failed"))
            return
        
        self.root.after(0, lambda: self.status_label.config(text="Connected"))
        
        while self.running:
            snapshot = self.manager.snapshot()
            if snapshot['connected']:
                self.root.after(0, lambda s=snapshot: self._update_display(s))
            time.sleep(1.0)
    
    def _update_display(self, snapshot):
        """Update GUI with new data (runs in main thread)."""
        # Clear previous data
        for widget in self.data_frame.winfo_children():
            widget.destroy()
        
        if snapshot['sensor_data']:
            for sensor, value in snapshot['sensor_data'].items():
                if isinstance(value, (int, float)):
                    min_val, max_val, avg_val = self.stats.get(snapshot['uid'], sensor)
                    if min_val is not None:
                        text = f"{sensor}: {value:.1f} (avg: {avg_val:.1f})"
                    else:
                        text = f"{sensor}: {value:.1f}"
                    
                    label = tk.Label(self.data_frame, text=text)
                    label.pack(anchor='w')
```

---

## Migration from Legacy Pattern

### Old Pattern (Before Refactoring)

```python
# Old way - multiple classes, mixed concerns
class DataSourceWorkerWrapper:
    def __init__(self):
        self._worker = None
        self._datasource = None
        # ... lots of worker management code
    
    def connect_direct(self, port):
        self._worker = SerialWorker(port, ...)
    
    def connect_datasource(self):
        self._datasource = create_datasource(...)
        self._worker = DataSourceWorker(self._datasource, ...)

# Statistics mixed into main file
device_stats = defaultdict(lambda: defaultdict(_make_stat))

# Configuration scattered throughout file
POWER_MAX = 1000.0
TEMP_WARN = 70.0
```

### New Pattern (After Refactoring)

```python
# New way - clean separation
from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.statistics import ChannelStats, create_stats_callback  
from benchlab.tui.config import Config

# Single manager handles everything
stats = ChannelStats()
manager = DataSourceManager(
    source_type='fastapi',
    stats_callback=create_stats_callback(stats),
    base_url='http://127.0.0.1:8000'
)

# Configuration centralized
power_max = Config.BarScales.POWER_MAX
temp_warn = Config.BarScales.CHIP_TEMP_WARN
```

---

## Best Practices

### 1. Parameter Handling

Always filter parameters by datasource type to avoid compatibility issues:

```python
# Good - DataSourceManager handles parameter filtering internally
manager = DataSourceManager('fastapi', poll_interval=1.0, base_url='http://...', timeout=5.0)

# The manager filters: FastAPI gets base_url + timeout, poll_interval used internally
```

### 2. Error Handling

Use consistent error handling patterns:

```python
try:
    if not manager.connect():
        snapshot = manager.snapshot()
        error_msg = snapshot.get('last_error', 'Unknown connection error')
        logger.error(f"Connection failed: {error_msg}")
        return False
except Exception as e:
    logger.error(f"Connection exception: {e}")
    return False
```

### 3. Resource Cleanup

Always clean up resources properly:

```python
def cleanup(self):
    """Clean up resources."""
    self.running = False
    
    # Stop background workers
    if self.worker_thread:
        self.worker_thread.join(timeout=2.0)
    
    # Disconnect from datasource
    if self.datasource_manager:
        self.datasource_manager.disconnect()
```

### 4. Thread Safety

Use the provided thread-safe components:

```python
# ChannelStats is thread-safe
stats.update(device_uid, channel, value)  # Safe from any thread

# DataSourceManager is thread-safe  
snapshot = manager.snapshot()  # Safe from any thread
```

---

## Troubleshooting

### Common Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| `FastAPIDataSource.__init__() got an unexpected keyword argument 'poll_interval'` | Parameter mismatch | Use DataSourceManager - it filters parameters correctly |
| `<` not supported between instances of 'int' and 'NoneType' | None values in telemetry | Use Config.get_temperature_color() - it handles None values |
| Tool hangs on startup | FastAPI server not running | Use launcher menu system - it auto-starts servers |
| Statistics not updating | No stats callback | Use `create_stats_callback(stats)` when creating DataSourceManager |
| Colors not working | Color pairs not initialized | Call `Config.init_colors()` after `curses.start_color()` |

### Debug Information

Enable debug logging to see datasource operations:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# DataSourceManager will log connection attempts, device discovery, etc.
```

---

## Summary

The refactored DataSourceManager pattern provides:

✅ **Separation of Concerns** - Datasource, statistics, config, and UI logic separated  
✅ **Reusable Components** - DataSourceManager can be used by any tool  
✅ **Thread Safety** - All components are thread-safe  
✅ **Parameter Filtering** - Automatic parameter compatibility handling  
✅ **Consistent API** - Same interface regardless of source type  
✅ **Error Resilience** - Proper error handling and recovery  
✅ **Statistics Integration** - Built-in min/max/avg tracking  
✅ **Configuration Management** - Centralized constants and thresholds  

**Recommended Approach:** Use the DataSourceManager pattern for all new tools and gradually migrate existing tools to this architecture.

---

*End of DataSource Integration Guide*