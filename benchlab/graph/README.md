# BENCHLAB Graph

## Overview

The BENCHLAB Graph module provides a **graphical interface** for real-time telemetry visualization.  
It allows monitoring of connected BENCHLAB devices with selectable sensors and dynamic plotting of measurements.

Built using [Dear PyGui](https://dearpygui.readthedocs.io/), the module supports:

- Real-time device detection and selection
- Dynamic sensor graphing
- Live min/max/average value display
- Multi-device support

---

## Features

| Feature | Description |
|---------|-------------|
| Device Detection | Automatically scans for connected BENCHLAB devices and lists them in a combo box. |
| Sensor Selection | Choose which sensor to display from the device. |
| Real-Time Graph | Plots selected sensor data dynamically with adjustable axes. |
| Min/Max/Avg Display | Shows live statistics for the selected sensor on the graph. |
| Thread-Safe Updates | Sensor reading and graph updates run in separate threads with synchronization. |

---

## Installation

Install the tool's dependencies (from `benchlab/graph/requirements.txt`):

```
pip install -r benchlab/graph/requirements.txt
```

**Dependencies:**

- `dearpygui>=2.1.1`
- `pyserial>=3.5`
- BENCHLAB core modules (`benchlab.core`)

---

## Usage

### Launch Graph Module

From the main BENCHLAB launcher:

```bash
# Default (direct/serial source, 1s sensor poll interval)
python -m benchlab -graph

# Custom interval
python -m benchlab -graph -i 0.5

# Choose a data source (direct | fastapi | fastapi_custom | mqtt | mqtt_custom | named_pipe | service_http)
python -m benchlab -graph --source fastapi --api-url http://127.0.0.1:8000
python -m benchlab -graph --source mqtt --mqtt-broker localhost --mqtt-port 1883
```

`-i`/`--interval` sets `GraphApp.sensor_read_interval` (default 1.0s). `--source`, `--api-url`, `--mqtt-broker`, and `--mqtt-port` are the standard `benchlab/main.py` flags; the graph module has no `-graph`-specific CLI flags of its own.

### Main GUI

- **Device Selection**: Select a device from the combo box.
- **Sensor Selection**: Open the sensor selection window to pick which metric to graph.
- **Graph Window**: Displays a live line chart of the selected sensor.
- **Statistics**: Minimum, maximum, and average values are shown and updated in real time.
- **Auto-Axis**: Y-axis limits adjust dynamically based on sensor data.

---

## Developer Notes

### Class: `GraphApp`

Handles the main functionality of the graph module:

- **Device Management**  
  - `detect_devices()` – scans for devices and updates UI  
  - `device_changed()` – updates state when device selection changes  
  - `start_sensor_thread()` / `stop_sensor_thread()` – manage sensor polling in a background thread  

- **Sensor Logic**  
  - `get_sensor_value()` – fetches a specific sensor value from the device  

- **UI Windows**  
  - `show_sensor_selection()` – opens the sensor selection window  
  - `open_graph_window()` – launches the graph plotting window  

- **Graph Update Loop**  
  - `update_graph_loop()` – continuously updates the graph with new sensor readings  
  - Dynamically adjusts axes and updates min/max/avg statistics  

- **Main Run Loop**  
  - `run()` – creates the Dear PyGui context, viewport, and GUI update loop

---

## References

- [BENCHLAB core modules](https://github.com/<your-org>/benchlab/tree/main/benchlab/core)  
- Related modules:
  - [TUI](../tui/README.md)
  - [CSV Logger](../csv_log/README.md)
  - [MQTT Publisher](../mqtt/README.md)
  - [VU](../vu/README.md)

---

## Notes

- The module is **thread-safe**: sensor reads and GUI updates run in separate threads.
- Designed for **real-time telemetry monitoring**, suitable for fleet management and lab setups.
- Y-axis auto-scaling ensures graphs remain readable even with rapidly changing sensor values.
