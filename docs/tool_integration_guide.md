# Tool Integration Guide

> How to add Direct / FastAPI / MQTT data source support to any new tool in benchlab-pytools.

**Version:** 2.0  
**Last Updated:** 2026-04-08

---

## Architecture At A Glance

```
┌─────────────────────────────────────────────────────────┐
│                    User chooses:                         │
│              "Which telemetry source?"                    │
│                                                           │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│   │  Direct  │    │ FastAPI  │    │   MQTT   │          │
│   │(serial)  │    │  (REST)  │    │(pub/sub) │          │
│   └────┬─────┘    └────┬─────┘    └────┬─────┘          │
│        │               │               │                 │
│        ▼               ▼               ▼                 │
│   ┌─────────────────────────────────────────────┐       │
│   │          DataSource (Unified API)            │       │
│   │                                              │       │
│   │  ds.list_devices()    → List[DeviceInfo]     │       │
│   │  ds.get_telemetry(uid) → Dict[str, value]    │       │
│   │  ds.get_device_info(uid) → Dict              │       │
│   │  ds.source_type       → "direct|fastapi|mqtt" │       │
│   └─────────────────────────────────────────────┘       │
│                          │                               │
│                          ▼                               │
│               ┌─────────────────────┐                   │
│               │    YOUR TOOL        │                   │
│               │  (TUI, Graph, etc.) │                   │
│               └─────────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

**Key Principle:** The telemetry source decision is made **first** in the launcher. Only one component owns the serial port. All tools consume data through the `DataSource` abstraction — never by opening serial ports directly.

---

## Part 1: Understanding the DataSource API

### 1.1 The Interface

All data sources implement the `DataSource` base class from `benchlab.core.datasource`:

```python
from benchlab.core.datasource import DataSource

class DataSource(ABC):
    """Unified interface for consuming benchlab telemetry."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection."""

    @abstractmethod
    def list_devices(self) -> List[dict]:
        """Return available devices as list of dicts with 'uid' and 'port'."""

    @abstractmethod
    def get_telemetry(self, uid: str) -> Optional[dict]:
        """Get latest sensor data for a device. Returns None if unavailable."""

    @abstractmethod
    def get_device_info(self, uid: str) -> Optional[dict]:
        """Get device metadata (UID, port, firmware)."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if datasource is connected."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Return one of: 'direct', 'fastapi', 'mqtt'."""
```

### 1.2 Creating a DataSource

```python
from benchlab.core.datasource import create_datasource

# Option A: Create directly
ds = create_datasource("direct", poll_interval=1.0)
ds = create_datasource("fastapi", base_url="http://127.0.0.1:8000")
ds = create_datasource("mqtt", broker="localhost", port=1883)

# Option B: Auto-select from environment variable
ds = create_datasource()  # Reads BENCHLAB_DATA_SOURCE env var
```

### 1.3 What Each Type Does Internally

| Type | How it gets data | How it discovers devices |
|------|-----------------|------------------------|
| **direct** | Opens serial port, calls `read_sensors(ser)` from pycore | Scans USB ports for Benchlab VID:PID |
| **fastapi** | HTTP GET `/device/{uid}/telemetry` | HTTP GET `/devices` |
| **mqtt** | Subscribes to `benchlab/{uid}/telemetry` topics | Subscribes to `benchlab/{uid}/info` topics (retained) |

---

## Part 2: Adding DataSource Support to a Tool

### 2.1 The Pattern (Copy/Paste Template)

Every tool that wants to support all three data sources should follow this pattern:

```python
"""
My Cool Tool for BenchLab

Supports telemetry from:
  - Direct serial (this process opens COM port)
  - FastAPI server (HTTP REST API)
  - MQTT broker (pub/sub)
"""

import os
import time
import threading
from typing import Optional

from benchlab.core.datasource import (
    DataSource, create_datasource,
    DirectDataSource, FastAPIDataSource, MQTTDataSource
)


class MyToolApp:
    """Main application class that consumes telemetry."""

    def __init__(self, datasource: Optional[DataSource] = None):
        """
        Args:
            datasource: Pre-configured DataSource. If None, auto-selects from
                        BENCHLAB_DATA_SOURCE env var (falls back to direct).
        """
        self.datasource: Optional[DataSource] = datasource
        self._running = False
        self._poll_interval = float(os.environ.get("POLL_INTERVAL", "1.0"))
        self._latest_data: dict[str, float] = {}

    def run(self):
        """Main entry point. Sets up datasource and enters the data loop."""
        # Step 1: Set up data source
        self._setup_datasource()

        # Step 2: Start background reader
        self._running = True
        reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        reader_thread.start()

        # Step 3: Enter tool's main loop (or block)
        try:
            self._main_loop()
        except KeyboardInterrupt:
            print("\n  Stopping...")
        finally:
            self._running = False
            reader_thread.join(timeout=2)
            if self.datasource:
                self.datasource.disconnect()

    # ── Data source setup ─────────────────────────────────────

    def _setup_datasource(self):
        """Create and connect the datasource."""
        if self.datasource is not None:
            return  # Already provided

        source_type = os.environ.get("BENCHLAB_DATA_SOURCE", "direct")

        if source_type == "fastapi":
            base_url = os.environ.get("BENCHLAB_API_URL", "http://127.0.0.1:8000")
            self.datasource = FastAPIDataSource(base_url=base_url)
        elif source_type == "mqtt":
            broker = os.environ.get("MQTT_BROKER", "localhost")
            port = int(os.environ.get("MQTT_PORT", "1883"))
            self.datasource = MQTTDataSource(broker=broker, port=port)
        else:
            self.datasource = DirectDataSource(poll_interval=self._poll_interval)

        if not self.datasource.connect():
            raise RuntimeError(f"Failed to connect via {self.datasource.source_type}")

        print(f"  Connected via {self.datasource.source_type}")

    def _get_telemetry(self, uid: str) -> Optional[dict]:
        """Get latest telemetry, regardless of data source type."""
        return self.datasource.get_telemetry(uid) if self.datasource else None

    # ── Background reader ─────────────────────────────────────

    def _read_loop(self):
        """Continuously poll telemetry data."""
        while self._running:
            if self.datasource and self.datasource.is_connected():
                devices = self.datasource.list_devices()
                for dev in devices:
                    uid = dev.get("uid")
                    if uid:
                        data = self._get_telemetry(uid)
                        if data:
                            self._latest_data[uid] = data
            time.sleep(self._poll_interval)

    # ── Tool logic ────────────────────────────────────────────

    def _main_loop(self):
        """Override with your tool's display/update logic."""
        raise NotImplementedError("Subclass must implement _main_loop()")
```

### 2.2 Registration in main.py

Add the tool to `CONSUMER_TOOLS` in `benchlab/main.py`:

```python
CONSUMER_TOOLS = {
    # ... existing tools ...

    "mytool": {
        "name": "My Cool Tool",
        "description": "What it does",
        "flag": "-mytool",          # CLI flag
        "module": "benchlab.mytool.mytool_main",
        "function": "run_my_tool",  # Entry function
    },
}
```

### 2.3 Launcher Integration

In the `launch_mode()` function, add a case for your CLI flag:

```python
elif args.mytool:
    try:
        from benchlab.mytool.mytool_main import run_my_tool
        run_my_tool()
    except ModuleNotFoundError:
        print("My tool module not available in this build.")
```

### 2.4 Entry Point Function

Create the module-level entry function that the launcher calls:

```python
# benchlab/mytool/mytool_main.py

def run_my_tool():
    """Entry point for CLI launcher and interactive mode."""
    from benchlab.mytool.mytool_app import MyToolApp

    app = MyToolApp()  # Auto-selects datasource from env var
    app.run()
```

---

## Part 3: Special Cases

### 3.1 Tool Has Its Own UI (Like TUI with curses)

For tools with their own UI framework (curses, tkinter, DearPyGui, etc.), you need a background thread that feeds data into the UI:

```python
class DataSourceWorker:
    """Runs in background thread, polls datasource and caches results."""

    def __init__(self, datasource: DataSource, interval: float = 1.0):
        self.datasource = datasource
        self.interval = interval
        self._running = False
        self._cache: dict[str, dict] = {}  # uid -> telemetry
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)

    def start(self):
        self._running = True
        self._thread.start()

    def stop(self):
        self._running = False
        self._thread.join(timeout=3)
        if self.datasource:
            self.datasource.disconnect()

    def get_telemetry(self, uid: str) -> Optional[dict]:
        """Get cached or live data, regardless of source type."""
        if self.datasource and self.datasource.is_connected():
            # For remote sources, query live; for direct, use cache
            if self.datasource.source_type != "direct":
                return self.datasource.get_telemetry(uid)
            return self._cache.get(uid)
        return self._cache.get(uid)

    def list_devices(self) -> list[dict]:
        """Get device list, updating cache."""
        if self.datasource:
            return self.datasource.list_devices()
        return []

    def _worker_loop(self):
        if self.datasource and not self.datasource.is_connected():
            self.datasource.connect()

        while self._running:
            devices = self.datasource.list_devices() if self.datasource else []
            for dev in devices:
                uid = dev.get("uid")
                if uid:
                    data = self.datasource.get_telemetry(uid)
                    if data:
                        self._cache[uid] = data
            time.sleep(self.interval)
```

### 3.2 Tool Needs Device Discovery (Like HWInfo Scanning)

If your tool needs to enumerate devices at startup:

```python
def discover_devices(datasource: Optional[DataSource] = None) -> list[dict]:
    """Get list of available devices from datasource."""
    if datasource is None:
        source_type = os.environ.get("BENCHLAB_DATA_SOURCE", "direct")
        datasource = create_datasource(source_type)
        datasource.connect()

    devices = datasource.list_devices()
    # datasource.disconnect() — caller's responsibility
    return devices
```

### 3.3 Tool Needs WebSocket Streaming (Like Real-Time Dashboard)

FastAPI provides WebSocket streaming at `/device/{uid}/stream`. Your tool can use it:

```python
import websockets
import json

async def subscribe_websocket(base_url: str, uid: str):
    """Subscribe to real-time WebSocket telemetry."""
    ws_url = base_url.replace("http", "ws") + f"/device/{uid}/stream"
    async with websockets.connect(ws_url) as ws:
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            yield data
```

### 3.4 CLI Tool (Like a One-Shot Exporter)

For non-interactive tools that just need a snapshot:

```python
def export_sensors(datasource: Optional[DataSource] = None):
    """One-shot export: get current sensor values."""
    if datasource is None:
        datasource = create_datasource()
        datasource.connect()

    try:
        devices = datasource.list_devices()
        for dev in devices:
            uid = dev["uid"]
            telemetry = datasource.get_telemetry(uid)
            if telemetry:
                print(f"=== {uid} ===")
                for sensor, value in telemetry.items():
                    print(f"  {sensor}: {value}")
    finally:
        datasource.disconnect()
```

---

## Part 4: Testing Your Integration

### 4.1 Manual Testing Checklist

Test your tool with all three data sources:

```bash
# Test 1: Direct mode (tool opens serial port itself)
python -m benchlab -mytool
# Or via interactive menu: Single Tool → My Cool Tool → Direct

# Test 2: FastAPI mode (server owns serial port, tool reads REST)
python -m benchlab -fastapi   # Start server in one terminal
python -m benchlab -mytool    # Run tool in another (auto-detects via env)
# Or: Single Tool → My Cool Tool → FastAPI

# Test 3: MQTT mode (publisher reads serial, tool subscribes)
python -m benchlab -mqtt      # Start publisher in one terminal
python -m benchlab -mytool    # Run tool in another
# Or: Single Tool → My Cool Tool → MQTT
```

### 4.2 What to Verify

| Test | What to Check |
|------|--------------|
| Direct | Tool opens serial port, reads UID, gets sensor data |
| FastAPI | Tool gets data via HTTP, serial port NOT opened by tool |
| MQTT | Tool receives retained info + telemetry topics |
| Hot unplugging | Tool handles device disconnect gracefully |
| Multiple tools | Two tools sharing FastAPI/MQTT don't conflict |

---

## Part 5: Environment Variables Reference

Your tool should read these env vars for configuration:

| Variable | Default | Purpose |
|----------|---------|---------|
| `BENCHLAB_DATA_SOURCE` | `direct` | `direct`, `fastapi`, or `mqtt` |
| `BENCHLAB_API_URL` | `http://127.0.0.1:8000` | FastAPI base URL |
| `API_PORT` | `8000` | FastAPI port |
| `MQTT_BROKER` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_TOPIC_PREFIX` | `benchlab` | MQTT topic prefix |
| `POLL_INTERVAL` | `1.0` | How often to poll (direct mode) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Part 6: Quick Reference — What to Copy

### Minimum Viable Integration (3 Steps)

**Step 1:** Constructor accepts optional `datasource`:

```python
class MyApp:
    def __init__(self, datasource=None):
        self.datasource = datasource
```

**Step 2:** Auto-select from env var if not provided:

```python
    def start(self):
        if self.datasource is None:
            from benchlab.core.datasource import create_datasource
            self.datasource = create_datasource()
        self.datasource.connect()
```

**Step 3:** Use the three core methods:

```python
    def run(self):
        devices = self.datasource.list_devices()
        for dev in devices:
            data = self.datasource.get_telemetry(dev["uid"])
            self._process(data)
```

### Full Integration (Add These Too)

- Graceful disconnect in cleanup: `self.datasource.disconnect()`
- Source-type checking: `if self.datasource.source_type != "direct": ...`
- Error handling for disconnect/reconnect scenarios
- DeviceRegistry integration (optional): `DeviceRegistry.get_instance().on_connect(callback)`

---

## Part 7: Common Patterns from Existing Tools

### TUI Pattern (curses with background DataSourceWorker)
See: `benchlab/tui/tui_main.py`
- Uses `DataSourceWorker` thread that polls and caches
- UI thread reads from cache, updates display
- Press `f` to rescan fleet

### CSV Logger Pattern (continuous logging)
See: `benchlab/csv_log/csv_logger_enhanced.py`
- Creates datasource per device
- Logs to CSV with buffering
- Uses lazy initialization

### HWInfo Pattern (periodic export)
See: `benchlab/hwinfo/hwinfo_export.py`
- `export_device_sensors(device_info, datasource=None)`
- Falls back to direct serial when no datasource
- Reads once per device, writes to registry

### Graph Pattern (matplotlib with live updates)
See: `benchlab/graph/app.py`
- `GraphApp(datasource=None)` stores datasource reference
- Detects datasource type in sensor loop
- Fetches from `datasource.get_telemetry(uid)` instead of `read_sensors(ser)`

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| "No devices found" | Serial port already owned by another process | Use FastAPI/MQTT mode instead of Direct |
| Tool hangs on startup | FastAPI server not started | Launcher should auto-start it via `check_and_setup_source()` |
| MQTT shows empty device list | Info topics not retained or publisher not running | Ensure `benchlab -mqtt` is running first |
| Tool crashes with import error | Module not in `CONSUMER_TOOLS` dict properly | Check module path and function name |
| Two tools fight over serial | One tool opened serial directly instead of using DataSource | Always use `create_datasource()`, never `open_serial_connection()` |