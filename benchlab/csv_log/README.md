# BENCHLAB CSV Fleet Logger

## Overview

The CSV Fleet Logger module provides real-time logging of telemetry data from all connected BENCHLAB devices into individual CSV files.  

It automatically:

- Detects all connected devices via serial ports.
- Reads sensor data from each device.
- Translates sensor data into structured CSV rows.
- Writes each device to its own CSV file with timestamped entries.
- Supports multiple devices simultaneously using threading.
- Provides a console summary of key power metrics while logging.

---

## Features

| Feature | Description |
|---------|-------------|
| Automatic device discovery | Uses `discover_fleet_devices()` to list all connected devices with UID and firmware. |
| Multi-device logging | Logs multiple devices in parallel threads. |
| CSV output | Each device has its own CSV file, headers generated from sensor fields. |
| Timestamped entries | Each row is timestamped with ISO 8601 format. |
| Console summary | Displays SYS, CPU, GPU power in real-time. |
| Graceful shutdown | Stops logging cleanly on Ctrl+C or user abort. |

---

## Installation

Install the required dependencies for the CSV logger:

```
pip install -r requirements_csv.txt
```

Dependencies include:

```
pyserial
benchlab core modules
```

---

## Usage

### Run CSV Logger

```
python benchlab.py -logfleet
```

Behavior:

- Detects all connected BENCHLAB devices.
- Displays a numbered list of available devices.
- Prompts the user to select devices to log (`all` or comma-separated numbers).
- Opens serial connections for selected devices.
- Starts logging data to timestamped CSV files.
- Provides a console summary while logging.
- Continues logging until interrupted with Ctrl+C.

---

### Device Selection Example

```
--- Available Devices ---
1: Port: COM3        UID: 123456 FW: 1.2.3
2: Port: COM4        UID: 789012 FW: 1.2.4

Enter device numbers to log (comma-separated, e.g., 1,2), or 'all' for all devices: 1,2
```

---

### Sample CSV Output

```
Timestamp,SYS_Power,CPU_Power,GPU_Power,Temp1,Temp2,...
2025-10-06T10:15:01.123456,120,50,30,65,70,...
2025-10-06T10:15:02.123456,118,49,31,65,71,...
```

---

## Developer Notes

### Threading

- Each device runs in its own logging thread (`sensor_logger_fleet`).
- Threads flush CSV files after each row.
- `logging_active` flag controls stopping threads.

### Graceful Shutdown

- Pressing Ctrl+C stops logging:
  - `logging_active` is set to `False`.
  - Threads exit their loops.
  - CSV files are closed.
  - Serial connections are closed.

### Error Handling

- Failed device detection: warnings printed for each port.
- Failed serial read/write: errors logged to console, retries on next interval.
- Missing UID or firmware: warnings shown, device may still be logged if serial connection works.

### Extending the Module

1. **Add additional sensor fields:** Update `translate_sensor_struct()` in `benchlab.core.sensor_translation`.  
2. **Custom CSV filenames:** Modify `sensor_logger_fleet` to customize output paths or naming conventions.  
3. **Fleet summaries:** Extend console summary logic for more metrics per device.

---

## References

- [BENCHLAB core modules](https://github.com/<your-org>/benchlab/tree/main/benchlab/core)  
- [Python CSV module](https://docs.python.org/3/library/csv.html)  
- [PySerial](https://pypi.org/project/pyserial/)
