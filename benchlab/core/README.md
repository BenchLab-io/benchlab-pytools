# BENCHLAB Core - Data Source Abstraction Layer

This module provides a unified architecture for BENCHLAB tools to consume telemetry data from multiple sources, enabling multiple tools to run simultaneously without serial port conflicts.

## Architecture Overview

### The Problem

The original architecture had each tool directly connecting to the serial port via `benchlab_pycore`. Since only one process can claim a serial port at a time, this prevented running multiple tools simultaneously.

### The Solution

A **Data Source Abstraction Layer** that allows tools to consume data from:

1. **Direct** - Direct serial connection via pycore (for single tool)
2. **FastAPI** - HTTP REST API server (recommended for multiple tools)
3. **MQTT** - MQTT broker subscription

## Components

### 1. DataSource Classes (`datasource.py`)

Abstract base class and implementations:

```python
from benchlab.core import create_datasource

# Factory function - creates appropriate DataSource
datasource = create_datasource('fastapi', base_url='http://127.0.0.1:8000')
datasource = create_datasource('direct', port='COM3')
datasource = create_datasource('mqtt', broker='localhost', port=1883)

# Common API
datasource.connect()
devices = datasource.list_devices()
telemetry = datasource.get_telemetry(uid)
device_info = datasource.get_device_info(uid)
datasource.disconnect()
```

### 2. Infrastructure Manager (`infrastructure.py`)

Manages starting/stopping FastAPI server and MQTT publisher:

```python
from benchlab.core import InfrastructureManager

infra = InfrastructureManager(fastapi_port=8000)

# Start infrastructure based on tool requirements
tools = [{'source': 'fastapi'}, {'source': 'mqtt'}]
infra.start_all(tools)

# ... run tools ...

infra.stop_all()
```

### 3. Launcher Utilities (`launcher.py`)

Interactive multi-tool selection and launching:

```python
from benchlab.core.launcher import run_interactive_launcher

# Run interactive menu
run_interactive_launcher()
```

## Usage

### Single Tool Mode (Backward Compatible)

Existing single-tool commands still work:

```bash
python benchlab.py -tui
python benchlab.py -hwinfo
python benchlab.py -fastapi
```

### Multi-Tool Mode - Interactive

```bash
python benchlab.py -multi
```

This launches an interactive menu where you can:
1. Select multiple tools to run
2. Choose data source (FastAPI recommended)
3. Launcher automatically starts infrastructure
4. Tools run simultaneously

### Multi-Tool Mode - Command Line

```bash
# Launch specific tools with automatic data source selection
python benchlab.py -tools tui hwinfo graph

# Specify data source explicitly
python benchlab.py -tools tui hwinfo -source fastapi

# Custom FastAPI port
python benchlab.py -tools tui hwinfo -source fastapi -fastapi-port 9000
```

## Data Source Details

### Direct Source
- **Use case**: Single tool that needs low-latency access
- **Pros**: Lowest latency, no overhead
- **Cons**: Exclusive serial port access, only one tool
- **Implementation**: `DirectDataSource` uses pycore directly

### FastAPI Source
- **Use case**: Multiple tools, web dashboards, remote access
- **Pros**: HTTP-based (firewall-friendly), supports multiple clients, REST API
- **Cons**: Small latency overhead (~10-50ms)
- **Implementation**: `FastAPIDataSource` makes HTTP requests to FastAPI server
- **Server**: Automatically started by `InfrastructureManager` on port 8000 (default)

### MQTT Source
- **Use case**: Distributed systems, IoT integration
- **Pros**: Pub/sub model, works with existing MQTT infrastructure
- **Cons**: Requires MQTT broker, more complex setup
- **Implementation**: `MQTTDataSource` subscribes to telemetry topics
- **Publisher**: Automatically started by `InfrastructureManager`

## Tool Refactoring Status

Tools need to be refactored to use the DataSource abstraction:

- [x] **Core infrastructure** - DataSource classes, InfrastructureManager
- [x] **Launcher** - Multi-tool selection and orchestration
- [ ] **TUI** - In progress (DataSourceWorker created)
- [ ] **HWiNFO** - Needs refactoring
- [ ] **CSV Logger** - Needs refactoring
- [ ] **Graph** - Needs refactoring
- [ ] **VU Dials** - Needs refactoring
- [ ] **WigiDash** - Needs refactoring

### Refactoring Pattern

Each tool should:

1. Accept a `datasource` parameter (or create one based on config)
2. Replace direct serial calls with DataSource API
3. Handle cases where `sensor_struct` is None (only available in direct mode)

Example:

```python
# Before
from benchlab_pycore.core import read_sensors, open_serial_connection
ser = open_serial_connection(port)
sensors = read_sensors(ser)

# After
from benchlab.core import create_datasource
datasource = create_datasource('fastapi')
datasource.connect()
telemetry = datasource.get_telemetry(uid)
```

## Configuration

### Environment Variables

FastAPI server:
- `LOG_LEVEL` - Logging level (default: INFO)
- `POLL_INTERVAL` - Sensor poll interval (default: 1.0)
- `HISTORY_LENGTH` - Number of historical records (default: 10)
- `API_HOST` - Server host (default: 0.0.0.0)
- `API_PORT` - Server port (default: 8000)

MQTT publisher:
- `MQTT_BROKER` - Broker hostname (default: localhost)
- `MQTT_PORT` - Broker port (default: 1883)
- `MQTT_USERNAME` - Optional username
- `MQTT_PASSWORD` - Optional password
- `MQTT_QOS` - QoS level (default: 0)
- `MQTT_POLL_RATE` - Publish interval (default: 1.0)

### Command-Line Arguments

Multi-tool mode:
- `-multi` - Interactive multi-tool selector
- `-tools <tool1> <tool2> ...` - Launch specific tools
- `-source <direct|fastapi|mqtt>` - Data source type
- `-fastapi-port <port>` - Custom FastAPI port (default: 8000)

## Migration Guide

### For Tool Developers

1. **Import the core module**:
   ```python
   from benchlab.core import create_datasource, InfrastructureManager
   ```

2. **Replace serial connection**:
   ```python
   # Old way
   ser = open_serial_connection(port)
   sensors = read_sensors(ser)
   
   # New way
   datasource = create_datasource(source_type, **kwargs)
   datasource.connect()
   telemetry = datasource.get_telemetry(uid)
   ```

3. **Handle missing sensor_struct**:
   ```python
   # sensor_struct is only available in direct mode
   if datasource.source_type == 'direct':
       # Use sensor_struct for detailed info
       pass
   else:
       # Use telemetry dict only
       pass
   ```

4. **Update argument parsing**:
   ```python
   parser.add_argument('-source', choices=['direct', 'fastapi', 'mqtt'],
                       default='direct', help='Data source type')
   ```

### For Users

**Existing workflows continue to work** - no changes needed for single-tool usage.

To use multiple tools:
1. Run `python benchlab.py -multi`
2. Select tools from the menu
3. Choose FastAPI as data source (recommended)
4. Tools will run simultaneously

## Benefits

1. **No serial port conflicts** - Multiple tools can run simultaneously
2. **Flexible deployment** - Tools can run on different machines (via network)
3. **Extensible** - Easy to add new data sources
4. **Backward compatible** - Existing single-tool commands still work
5. **Clean separation** - Tools don't need to know about data source details

## Future Enhancements

- [ ] Hot-reload configuration changes
- [ ] WebSocket support for real-time streaming
- [ ] Data source failover (auto-switch if one fails)
- [ ] Remote data source (tools on different machines)
- [ ] Data source health monitoring
- [ ] Automatic tool restart on failure