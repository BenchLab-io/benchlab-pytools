# BENCHLAB Telemetry TUI

## Overview

The BENCHLAB TUI provides a curses-based, interactive terminal interface for real-time telemetry monitoring of BENCHLAB devices.  

It allows developers and engineers to:

- View all connected devices in a fleet.
- Inspect device-level information and configuration.
- Monitor system power, voltages, temperatures, and fan status.
- Navigate easily with keyboard shortcuts.
- Switch between multiple telemetry tabs.
- Handle dynamic device connections and disconnections gracefully.

---

## Features

| Feature | Description |
|---------|-------------|
| Fleet overview | Lists all connected devices with port, firmware, and UID. |
| Device tab | Shows connection details, firmware, and device configuration. |
| Power monitoring | Displays SYS, CPU, GPU, and MB power in real-time. |
| Voltage monitoring | Shows Vdd, Vref, and per-channel VIN values. |
| Temperature monitoring | Chip, ambient, humidity, and multiple temperature sensors. |
| Fan monitoring | Displays duty, RPM, and status for all fans. |
| Keyboard navigation | Switch tabs, move through devices, select active device. |
| Auto-reconnect | Handles device disconnections and reconnections. |

---

## Installation

Ensure your environment has required dependencies:

```
pip install -r requirements_tui.txt
```

Dependencies include:

```
python3-curses (Linux/Unix)
benchlab core modules
```

---

## Usage

### Launch the TUI

```
python benchlab.py -tui
```

Behavior:

- Launches the curses interface.
- Detects all connected devices and caches them.
- Displays tabs for Fleet, Device, Power, Voltage, Temperature, and Fans.
- Refreshes every `interval` seconds (default 1s).

---

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| q or Q | Quit the TUI |
| KEY_RIGHT / l | Move to next tab |
| KEY_LEFT / h | Move to previous tab |
| KEY_UP / KEY_DOWN | Navigate fleet device list |
| Enter | Select device from fleet tab |
| KEY_RESIZE | Handle terminal resize gracefully |

---

### Tabs Overview

1. **Fleet**: Lists all devices, marks the active one.
2. **Device**: Shows serial port, vendor/product IDs, firmware, and configuration.
3. **Power**: Displays SYS, CPU, GPU, MB power metrics.
4. **Voltage**: Displays Vdd, Vref, and VIN channels.
5. **Temperature**: Chip, ambient, humidity, and multiple sensor readings.
6. **Fans**: Duty cycle, RPM, enable status, and external fan duty.

---

### Example Fleet Display

```
## BENCHLAB Fleet ##
-> COM3       0x1A2B3C  123456 [ACTIVE]
   COM4       0x1A2B3D  789012
```

---

### Example Power Tab

```
## System Telemetry ##
SYS Power : 120 W
CPU Power : 50 W
GPU Power : 30 W
MB Power  : 20 W
```

---

## Developer Notes

### Fleet Cache

- Devices detected at startup via `serial_io.get_fleet_info()`.
- Sorted by serial port for consistent display.
- `active_device_index` tracks currently highlighted device.

### Serial Connection

- `open_serial_connection()` handles per-device serial setup.
- Disconnect/reconnect handled automatically when switching devices.

### TUI Refresh

- Uses `curses.nodelay` with a 500ms timeout.
- Main loop refreshes the screen every 0.1s.
- Handles dynamic window resize and small terminal dimensions gracefully.

### Sensor Translation

- Raw sensor structs converted via `translate_sensor_struct()`.
- Provides consistent keys for power, voltage, temperature, and fan metrics.

### Extending the Module

1. **Add new telemetry fields**: Update `translate_sensor_struct` to expose new metrics.  
2. **Custom tab layouts**: Modify `TAB_NAMES` and `if current_tab == X` sections.  
3. **Enhanced visuals**: Leverage `curses` color pairs and formatting for better UX.  

---

## References

- [BENCHLAB core modules](https://github.com/<your-org>/benchlab/tree/main/benchlab/core)  
- [Python curses documentation](https://docs.python.org/3/library/curses.html)  
