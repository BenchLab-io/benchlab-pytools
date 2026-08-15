# Enhanced BENCHLAB CSV Fleet Logger

## Overview

The Enhanced CSV Fleet Logger provides robust, lightweight telemetry logging for BENCHLAB devices with improved error handling, performance optimization, and cross-platform compatibility.

### Key Improvements

- **Configuration-driven**: Support for config files and environment variables
- **Enhanced error handling**: Exponential backoff retry logic and auto-reconnection
- **Performance optimized**: Buffered writes and async file I/O
- **Cross-platform**: Optimized for both Windows and Linux environments
- **Multiple formats**: Support for CSV and JSON output formats
- **Silent operation**: Headless mode for automated deployments
- **Connection monitoring**: Automatic reconnection for dropped devices

---

## Features

| Feature | Description |
|---------|-------------|
| **Configurable operation** | File-based and environment variable configuration |
| **Smart device discovery** | Enhanced error handling during device detection |
| **Buffered writes** | Configurable buffer size for optimal disk I/O |
| **Auto-reconnection** | Automatic reconnection for dropped serial connections |
| **Multiple output formats** | CSV and JSON format support |
| **Silent mode** | Headless operation for automated scripts |
| **Cross-platform** | Optimized for Windows, Linux, and embedded systems |
| **Memory efficient** | Circular buffers and automatic cleanup |
| **Detailed logging** | Configurable log levels and structured output |

---

## Installation

Install the tool's dependencies (from `benchlab/csv_log/requirements.txt`):

```bash
pip install -r benchlab/csv_log/requirements.txt
```

Key dependencies: `pyserial`, `benchlab-pycore`.

---

## Configuration

### Configuration File

Create a `csv_logger.config` file in your working directory. Only the settings below are actually read by `LoggerConfig`/`load_config()`; other keys are ignored:

```ini
[logger]
interval = 1.0                    # Logging interval in seconds
output_dir = logs                 # Output directory
buffer_size = 100                 # Rows buffered before a batched write
format = csv                      # Output format: csv or json (currently informational; batcher writes CSV)
silent_mode = false                # Silent operation (also auto-selects devices)
auto_select = false                # Auto-select all discovered devices, skip the prompt
```

### Environment Variables

Override select settings with environment variables (read in `load_config()`):

```bash
export CSV_LOG_INTERVAL=0.5
export CSV_LOG_OUTPUT_DIR=/var/log/benchlab
export CSV_LOG_BUFFER_SIZE=200
export CSV_LOG_SILENT=true
export CSV_LOG_AUTO_SELECT=true
```

`BENCHLAB_AUTO_SELECT=true` is also honored by `run_enhanced_csv_logger()` (used when launched via `python -m benchlab -logfleet`) to force auto-selection.

---

## Usage

### Command Line Interface

Via the main BENCHLAB launcher (recommended):

```bash
# Basic usage with default settings (direct/serial source, 1s interval)
python -m benchlab -logfleet

# Custom interval
python -m benchlab -logfleet -i 0.5

# Choose a data source (direct | fastapi | fastapi_custom | mqtt | mqtt_custom | named_pipe | service_http)
python -m benchlab -logfleet --source fastapi --api-url http://127.0.0.1:8000
python -m benchlab -logfleet --source mqtt --mqtt-broker localhost --mqtt-port 1883
```

`-i`/`--interval`, `--source`, `--api-url`, `--api-port`, `--mqtt-broker`, and `--mqtt-port` are the standard `benchlab/main.py` CLI flags; the tool itself has no `-logfleet`-specific flags beyond what `main.py` exposes.

Running the module standalone (bypasses the main launcher, uses its own small argparse CLI limited to `direct`/`fastapi`/`mqtt` sources):

```bash
python -m benchlab.csv_log.csv_logger_enhanced -i 0.5 -c my_config.config
python -m benchlab.csv_log.csv_logger_enhanced --silent --auto-select
```

### Programmatic Usage

```python
from benchlab.csv_log.csv_logger_enhanced import EnhancedCSVLogger, LoggerConfig
import types

# Create configuration
config = LoggerConfig(
    interval=0.5,
    output_dir="custom_logs",
    buffer_size=50,
    silent_mode=True,
    auto_select=True,
)

# args mirrors the standard benchlab CLI namespace (source/api_url/mqtt_broker/mqtt_port)
args = types.SimpleNamespace(source="direct", interval=0.5, api_url="http://127.0.0.1:8000",
                              mqtt_broker="localhost", mqtt_port=1883)

# Create and run logger (use as a context manager so stop_logging() always runs)
with EnhancedCSVLogger(config, args=args) as logger:
    logger.start_logging()
```

---

## Output Format

Rows are written as CSV via the batching logger (`benchlab/csv_log/message_batcher.py`):

```
Timestamp,uid,SYS_Power,CPU_Power,GPU_Power,Temp1,Temp2,...
2025-10-06T10:15:01.123456,BL-1234,120,50,30,65,70,...
2025-10-06T10:15:02.123456,BL-1234,118,49,31,65,71,...
```

The `format` config key is accepted by `LoggerConfig` but the batcher currently always writes CSV; JSON output is not implemented.

---

## Behavior Notes

- **Buffered writes**: rows are batched (default `buffer_size = 100`) and flushed via `BatchingLogger`, with a background flush every 5 seconds and an explicit flush each polling cycle.
- **Retry on connect**: initial connection to the data source uses `SmartRetryManager` (3 attempts, exponential backoff) — see `benchlab/csv_log/smart_retry.py`.
- **Silent/auto-select**: `silent_mode` or `auto_select` (or `BENCHLAB_AUTO_SELECT=true`) skip the interactive device-selection prompt and log at `WARNING` level instead of `INFO`.
- **Graceful shutdown**: Ctrl+C stops the polling loop, flushes the batcher, and disconnects the data source (`EnhancedCSVLogger.stop_logging()`).

---

## Troubleshooting

### Device Not Detected
```bash
python -c "from benchlab_pycore.core import get_benchlab_ports; print(get_benchlab_ports())"
```

### Permission Errors (Linux)
```bash
# Add user to dialout group for serial access
sudo usermod -a -G dialout $USER
# Log out and back in, or restart
```

### No devices selected / logging exits immediately
Ensure the configured `--source` (direct/fastapi/mqtt/etc.) is actually running and reachable — the logger connects via `DataSourceManager`, and a failed connection or empty device discovery causes it to log an error and stop.

---

## API Reference

### EnhancedCSVLogger Class

```python
class EnhancedCSVLogger:
    def __init__(self, config: LoggerConfig, args=None)
    def start_logging(self)
    def stop_logging(self)
    def discover_devices(self) -> List[DeviceConfig]
    def select_devices(self, devices: List[DeviceConfig]) -> List[DeviceConfig]
    def create_batcher(self)
    def log_device_data(self, uid: str) -> bool
```

### LoggerConfig Dataclass

```python
@dataclass
class LoggerConfig:
    interval: float = 1.0
    output_dir: str = "logs"
    buffer_size: int = 100
    format: str = "csv"          # accepted, but only CSV output is implemented
    silent_mode: bool = False
    auto_select: bool = False
```

---

## Support

For issues, check the Troubleshooting section above or report bugs via GitHub issues.