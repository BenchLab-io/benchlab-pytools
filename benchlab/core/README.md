# BENCHLAB Core

## Overview

The **core module** contains the fundamental building blocks for BENCHLAB telemetry.  
It defines the **data structures, enums, constants**, and **serial communication helpers** that all other modules rely on.

This module provides:

- Standardized **sensor and device structures**
- UART command definitions
- Serial communication helpers for detecting and reading BENCHLAB devices
- Sensor translation logic for consistent numeric and human-readable values

---

## Features

| Feature | Description |
|---------|-------------|
| Constants | Standard vendor/product IDs, firmware versions, sensor counts, and fan counts. |
| Device Structures | `VendorDataStruct`, `PowerSensor`, `FanSensor`, and `SensorStruct` using `ctypes`. |
| UART Commands | `BENCHLAB_CMD` enum with all device commands (read sensors, UID, fan profiles, etc.). |
| Serial I/O | Connect to devices, list COM ports, read sensor and vendor data, read UID. |
| Fleet Management | Scan for connected devices, retrieve vendor info, and maintain fleet cache. |
| Sensor Translation | Convert raw sensor structs to flat, human-readable dictionaries. |
| Utility Helpers | Temperature formatting and other small helpers for consistent data. |

---

## Installation

The core module is a dependency of all BENCHLAB submodules.  

Install standard Python packages for serial communication:

```
pip install pyserial
```

The core module **does not have its own UI**, but is required for TUI, Graph, VU, CSV logging, and MQTT modes.

---

## Usage

### Accessing Device Structures

```python
from benchlab.core.structures import SensorStruct, VendorDataStruct, PowerSensor, FanSensor
from benchlab.core.structures import BENCHLAB_CMD, BENCHLAB_VENDOR_ID
```

These structures can be used to **read and interpret raw serial data** from BENCHLAB devices.

---

### Connecting to Devices

```python
from benchlab.core import serial_io

# List available COM ports
ports = serial_io.get_benchlab_ports()

# Open a connection
ser = serial_io.open_serial_connection(ports[0])

# Read device info
device_info = serial_io.read_device(ser)

# Read sensors
sensor_struct = serial_io.read_sensors(ser)

# Read UID
uid = serial_io.read_uid(ser)
```

---

### Sensor Translation

Convert raw sensor structs to a **flat dictionary** for logging, graphs, or MQTT publishing:

```python
from benchlab.core.sensor_translation import translate_sensor_struct

data = translate_sensor_struct(sensor_struct)
print(data['CPU_Power'], data['Chip_Temp'], data['VIN_0'])
```

**Output Example:**

```text
CPU_Power: 95.3 W
Chip_Temp: 45.2 °C
VIN_0: 12.01 V
```

---

### Enums and Commands

`BENCHLAB_CMD` contains all UART commands:

```python
from benchlab.core.structures import BENCHLAB_CMD

ser.write(BENCHLAB_CMD.UART_CMD_READ_SENSORS.toByte())
```

Commands include:

- `UART_CMD_READ_SENSORS` – read all sensor data
- `UART_CMD_READ_UID` – get device UID
- `UART_CMD_READ_VENDOR_DATA` – get vendor/product info
- `UART_CMD_WRITE_FAN_PROFILE` – update fan profile
- `UART_CMD_WRITE_RGB` – update RGB configuration
- …and others

---

## Developer Notes

- `SensorStruct` is designed for **direct memory mapping** via `ctypes`.
- All power, voltage, temperature, and fan data are raw integers and need **translation** for human-readable values.
- Serial I/O is **thread-safe** for multiple devices, but read operations should handle `None` returns if the device is disconnected.
- Logging uses the `LOG_LEVEL` environment variable (`DEBUG`, `INFO`, `WARNING`, `ERROR`).

---

## References

- [BENCHLAB TUI](../tui/README.md)
- [BENCHLAB Graph](../graph/README.md)
- [BENCHLAB CSV Logger](../csv_log/README.md)
- [BENCHLAB MQTT Publisher](../mqtt/README.md)
- [BENCHLAB VU Dials](../vu/README.md)

---

## Notes

- Core modules are **required by all other submodules**.
- The sensor translation function ensures that all telemetry modes (TUI, Graph, VU, CSV, MQTT) can **consume the same normalized dataset**.
- Designed for **reliability and compatibility** across different BENCHLAB hardware revisions.
