# BENCHLAB Core - Shared Internals

`benchlab/core/` is the internal library used by every BENCHLAB PyTools tool (TUI, FastAPI server, MQTT publisher, Link, HWiNFO export, VU dials, WigiDash, config tool). It provides the data-source abstraction that lets multiple tools consume telemetry without fighting over a serial port, plus supporting infrastructure: device lifecycle tracking, subprocess management, retry helpers, and statistics.

This is a developer-facing internals doc. For CLI usage of individual tools, see their own READMEs (`benchlab/restapi/readme.md`, `benchlab/mqtt/README.md`, `benchlab/config/README.md`, `benchlab/link/README.md`).

## Architecture Overview

### The Problem

Only one process can own a serial port at a time. If every tool connected directly via `benchlab_pycore`, only one tool could ever run at once.

### The Solution

A **DataSource abstraction layer** (`datasource.py`) with multiple implementations, all exposing the same interface. Tools pick a source type via `--source` and don't need to know how the data actually gets to them.

## Components

### 1. DataSource classes (`datasource.py`)

`DataSource` is an abstract base class (`connect`, `disconnect`, `is_connected`, `list_devices`, `get_telemetry(uid)`, `get_device_info(uid)`, `source_type`). Concrete implementations:

| Class | `source_type` | Description |
|---|---|---|
| `DirectDataSource` | `direct` | Connects directly to the serial port via `benchlab_pycore`. Runs its own background polling thread. Exclusive port access — only one tool. |
| `FastAPIDataSource` | `fastapi` / `fastapi_custom` | HTTP client for the `benchlab.restapi` server. Polls `/health`, `/devices`, `/device/{uid}/telemetry`, `/device/{uid}/info` via `requests`. |
| `MQTTDataSource` | `mqtt` / `mqtt_custom` | Subscribes to `<topic_prefix>/+/telemetry` and `<topic_prefix>/+/info` on an MQTT broker via `paho-mqtt`. Resolves `MQTT_PROTOCOL` (v3.1 / v3.1.1 / v5) via the module-level `resolve_mqtt_protocol()` helper. |
| `NamedPipeDataSource` | `named_pipe` | Windows-only. Talks to the C# BENCHLAB Windows service over named pipes (`BenchlabDiscovery` for device listing, per-device `BenchlabSensorPipe_XX_YYY` pipes for telemetry). Normalizes the C# service's `ShortName`-keyed sensor payloads into the same flat key names Python tools expect, via the module-level `_normalise_cs_telemetry()` helper. |
| `ServiceHttpDataSource` | `service_http` | REST client for the C# BENCHLAB service's HTTP API (default `http://localhost:8585`). Thin client only — does not start/manage the service. Same telemetry normalization as `NamedPipeDataSource`. |

Each implementation validates its constructor kwargs through a Pydantic model in `config.py` (`SerialConfig`, `FastAPIConfig`, `MQTTConfig`, `NamedPipeConfig`, `ServiceHttpConfig`).

The `create_datasource(source_type, **kwargs)` factory function builds the right class:

```python
from benchlab.core import create_datasource

datasource = create_datasource('fastapi', base_url='http://127.0.0.1:8000')
datasource = create_datasource('direct', port='COM3')
datasource = create_datasource('mqtt', broker='localhost', port=1883)

datasource.connect()
devices = datasource.list_devices()
telemetry = datasource.get_telemetry(uid)
device_info = datasource.get_device_info(uid)
datasource.disconnect()
```

### 2. DataSourceManager (`datasource_manager.py`)

Wraps any `DataSource` with a consistent, higher-level API used by tools (TUI, Link, etc.): connection management, a background polling thread, device selection, and a single `snapshot()` call for UI/consumer code.

```python
from benchlab.core.datasource_manager import DataSourceManager

mgr = DataSourceManager(source_type='fastapi', base_url='http://127.0.0.1:8000')
mgr.connect()
mgr.select_device(uid)
snap = mgr.snapshot()
# snap: connected, source_type, source_desc, port, uid, device_info,
#       sensor_data, connection_time, last_error, all_devices, all_telemetry
```

Also accepts an optional `stats_callback(uid, channel, value)` invoked on every changed numeric telemetry value, typically wired to a `ChannelStats` instance (see below).

### 3. DeviceRegistry (`device_registry.py`)

Thread-safe singleton (`DeviceRegistry.get_instance()`) that is the single source of truth for "which devices exist right now." Exactly one component per process should own registration — e.g. the FastAPI server registers/unregisters devices as its reader threads start and stop. Other tools only observe via `get_devices()`, `get_device(uid)`, `has_device(uid)`, or subscribe to `on_connect(callback)` / `on_disconnect(callback)` events. `update_telemetry(uid)` stamps a device's `last_telemetry` time.

### 4. ProcessManager (`process_manager.py`)

Thread-safe singleton (`ProcessManager.get_instance()`) for starting/stopping infrastructure subprocesses (e.g. a spawned FastAPI server or MQTT publisher process) with health checks:

```python
pm = ProcessManager.get_instance()
pm.start_service(
    name="fastapi",
    cmd=["python", "-m", "benchlab", "-fastapi"],
    health_check=lambda: ping_http("http://127.0.0.1:8000/health"),
    timeout=20,
)
pm.stop_service("fastapi")
pm.shutdown_all()
```

Redirects each service's stdout/stderr to `logs/svc_<name>_stdout.log` / `_stderr.log`. Stopping tries graceful termination (SIGTERM / `taskkill`) first, then force-kills after a timeout.

### 5. InfrastructureManager (`infrastructure.py`)

Higher-level, non-singleton orchestrator used by the multi-tool launcher path: starts a FastAPI server as a `uvicorn` subprocess (`start_fastapi`) or an MQTT publisher as a background thread (`start_mqtt_publisher`), detecting if a compatible service is already listening on the target port before starting a new one. `start_all(tools)` / `stop_all()` take a list of tool configs and start only what's needed. Usable as a context manager.

### 6. Discovery (`discovery.py`)

`discover_devices()` — the canonical way to enumerate connected BENCHLAB devices over direct serial. Uses `benchlab_pycore.core.get_benchlab_ports()` to find candidate ports, opens each, reads UID/firmware/product ID, and returns `{"uid", "port", "fw", "variant"}` dicts (`variant` is `"ORIGINAL"` or `"BL2"`). Wrapped in the `retry` decorator (3 attempts, exponential backoff).

### 7. Retry (`retry.py`)

`RetryPolicy` (dataclass: `max_retries`, `backoff_factor`, `base_delay`, `allowed_exceptions`) plus a `retry(policy)` decorator that retries a wrapped callable with exponential backoff, logging each attempt. Used throughout `datasource.py` and `discovery.py` for connection attempts.

### 8. Shared serial (`shared_serial.py`)

`open_serial_connection(port)` — the one place that actually calls `serial.Serial(port, 115200, timeout=1)`; returns `None` instead of raising on failure. All direct-serial code paths (`DirectDataSource`, `discovery.py`, `telemetry_api.py`) use this instead of talking to `pyserial` directly, so the "None on failure" contract is consistent everywhere.

Also provides `SharedSerialManager`, a singleton connection pool keyed by port with reference counting (`acquire_connection` / `release_connection`), so multiple in-process consumers can share one open port instead of each opening their own handle. `SharedConnection` is the context-manager wrapper returned by `acquire_connection`.

### 9. Statistics (`statistics.py`)

`ChannelStats` — thread-safe per-device, per-channel running min/max/average tracker (`update`, `get`, `get_all`, `reset`, `get_devices`, `get_channels`, `has_data`). `StatsFormatter` provides display helpers (`format_stat_string`, `format_compact_range`). `create_stats_callback(stats)` builds a `(uid, channel, value) -> None` callback suitable for `DataSourceManager(stats_callback=...)`.

### 10. Config models (`config.py`)

Pydantic models validating each DataSource's constructor kwargs: `SerialConfig`, `FastAPIConfig` (normalizes `base_url` to include a scheme and strips trailing slash), `MQTTConfig`, `NamedPipeConfig`, `ServiceHttpConfig`.

## Package exports (`benchlab/core/__init__.py`)

```python
from benchlab.core import (
    DataSource, DirectDataSource, FastAPIDataSource, MQTTDataSource, create_datasource,
    DataSourceManager,
    ChannelStats, StatsFormatter, create_stats_callback,
    DeviceRegistry, DeviceInfo,
    ProcessManager, ManagedProcess,
    BENCHLAB_ORIGINAL_PRODUCT_ID, BENCHLAB_BL2_PRODUCT_ID,
)
```

`NamedPipeDataSource` and `ServiceHttpDataSource` live in `datasource.py` but aren't re-exported from `__init__.py`; import them directly from `benchlab.core.datasource` if needed. `BENCHLAB_ORIGINAL_PRODUCT_ID` / `BENCHLAB_BL2_PRODUCT_ID` are re-exported from `benchlab_pycore` (with a fallback to hardcoded `0x10`/`0x11` if pycore isn't installed) so tools can detect device variant without importing pycore directly.

## How tools use this

- **`benchlab/main.py`** resolves `--source` into environment variables via `_setup_source_from_args()` and calls `check_and_setup_source()` (in `benchlab/sources.py`) to start/verify the chosen source before launching a tool.
- **`benchlab/restapi/telemetry_api.py`** is a *producer*: it owns the serial port directly, registers devices in `DeviceRegistry`, and serves `DirectDataSource`-equivalent data to `FastAPIDataSource` clients.
- **`benchlab/mqtt/mqtt_publisher.py`** is also a producer: reads sensors directly and publishes to an MQTT broker for `MQTTDataSource` clients.
- **`benchlab/link/link_main.py`** is a consumer: uses `DataSourceManager` against any local source (direct/fastapi/mqtt) and republishes telemetry to a remote/cloud MQTT broker.
- **`benchlab/tui/`**, **VU dials**, **WigiDash**, **HWiNFO export**, **CSV logger** are consumers via `DataSourceManager` or `create_datasource` directly.
- The **config tool** (`benchlab/config/`) does not use the DataSource abstraction — it has its own direct/named-pipe client layer (`config_client.py`) for read/write configuration operations, since DataSource is read-only telemetry.

## Data Source Comparison

| Source | Use case | Pros | Cons |
|---|---|---|---|
| `direct` | Single tool, lowest latency | No overhead, no extra process | Exclusive port access, only one tool |
| `fastapi` / `fastapi_custom` | Multiple tools, remote access | HTTP-based, firewall-friendly, multi-client | Small latency overhead, requires a running server |
| `mqtt` / `mqtt_custom` | Distributed/IoT integration | Pub/sub, works with existing MQTT infra | Requires a broker, more moving parts |
| `named_pipe` | Windows, alongside the C# BENCHLAB service | Multiple tools, no direct serial management | Windows only, requires the service + pywin32 |
| `service_http` | Windows, alongside the C# BENCHLAB service | HTTP-based, multi-client | Windows-oriented, requires the service running |

## See Also

- [BENCHLAB PyTools Documentation](../../README.md) - Main PyTools documentation
- [FastAPI Telemetry Server README](../restapi/readme.md)
- [MQTT Publisher README](../mqtt/README.md)
- [Config Tool README](../config/README.md)
- [Link README](../link/README.md)
