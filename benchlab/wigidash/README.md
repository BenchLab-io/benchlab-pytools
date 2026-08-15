# Wigidash

## Overview

Wigidash is a Python-based telemetry dashboard for BENCHLAB devices, providing real-time and historical monitoring of sensors including voltage, current, power, and fans.  

It automatically:

- Reads telemetry from connected BENCHLAB devices.
- Displays numeric data and fan metrics in an interactive dashboard.
- Plots historical data with timestamps.
- Allows touch or click interaction for metric selection and toggling.
- Handles multi-page navigation with headers, footers, and control buttons.
- Automatically scales graphs with support for negative and positive data.
- Ensures safe page transitions and prevents ghost touches.

Designed for engineers and system testers to monitor BENCHLAB telemetry effectively.

---

## Features

| Feature | Description |
|---------|-------------|
| Real-time telemetry | Continuously updates sensor metrics on a live dashboard. |
| Historical graphing | Displays plots of previous data with timestamps or sample numbers. |
| Metric grouping | Automatically groups metrics by section: Power, Voltage, Current, Fans. |
| Fan metric handling | Supports Duty and RPM metrics with “All Duty” / “All RPM” toggles. |
| Touch-enabled UI | Allows metric selection and page navigation via touch or mouse. |
| Auto-scaled Y-axis | Graphs automatically scale with numeric data, including negative values. |
| Footer info panel | Shows device info, port, firmware version, and UID. |
| Page lifecycle management | Safely handles page start/stop and prevents input from other pages. |
| Graceful exit | Shutdown button or Ctrl+C closes the dashboard cleanly. |

---

## Installation

Install the required dependencies:

```
pip install -r requirements.txt
```

Dependencies include:

- Pillow
- Matplotlib
- NumPy
- pyserial
- pyusb
- libusb-package (Windows only — provides the libusb-1.0 backend pyusb needs)
- benchlab core modules

### Windows Setup

`pyusb` needs a `libusb-1.0` backend DLL, which isn't bundled with the `pyusb` pip package itself. `requirements.txt` includes `libusb-package`, which provides a prebuilt backend that `pyusb` auto-discovers — plain `pip install -r requirements.txt` in any standard Python environment (venv, system Python, etc.) is enough; no Anaconda/conda-forge install is required.

### Linux Setup

`pyusb` needs a working `libusb-1.0` backend and permission to access the WigiDash's USB device node as a non-root user.

1. Install libusb (relevant on minimal/ARM images, e.g. Raspberry Pi OS Lite, which may not ship it by default):

   ```
   sudo apt install libusb-1.0-0
   ```

2. Grant non-root USB access by creating `/etc/udev/rules.d/99-wigidash.rules`:

   ```
   SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", TAG+="uaccess"
   ```

   Then reload udev rules:

   ```
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```

   Without this rule, `pyusb` calls typically fail with an `Access denied (insufficient permissions)` USB error unless run as root. On startup, `scan_wigidash()` runs a best-effort check for this and logs an actionable warning (with the same rule text above) if it looks like access isn't set up — this is a diagnostic only, not a hard requirement check, since udev setups vary across distros.


---

## Folder Structure

wigidash/
├─ README.md
├─ assets/ # Fonts, logos, and other UI resources
├─ __init__.py
├─ benchlab_fleet.py # Fleet selection UI
├─ benchlab_graph.py # Graph rendering and metric selection
├─ benchlab_overview.py # Overview page for system telemetry
├─ benchlab_telemetry.py # Data logging and historical telemetry handling
├─ benchlab_ui.py # Shared UI toolkit: theme, buttons, header/footer drawing
├─ benchlab_utils.py # Utilities for image display, logging, and device management
├─ wigidash_device.py # Device abstraction layer
├─ wigidash_manager.py # Top-level orchestrator: device discovery, session/telemetry management
├─ wigidash_session.py # Per-device UI session and page lifecycle
├─ wigidash_usb.py # USB communication layer
├─ wigidash_widget.py # Dashboard widget configuration
└─ requirements.txt


---

## Usage

### Launch the Dashboard

```
python -m benchlab -wigidash
```

By default this connects via the `direct` (serial) data source. To use a different source, pass `--source` along with the matching connection flags:

```
python -m benchlab -wigidash --source fastapi --api-url http://127.0.0.1:8000
python -m benchlab -wigidash --source mqtt --mqtt-broker localhost --mqtt-port 1883
```

Supported `--source` values: `direct`, `fastapi`, `fastapi_custom` (via `DataSourceManager`, same as the other consumer tools).

Behavior:

- Detects all connected BENCHLAB devices via the selected data source.
- Displays the main overview page.
- Allows switching to the telemetry graph page for detailed metrics.
- Supports interactive metric selection and fan toggles.
- Updates the dashboard continuously until interrupted (`Ctrl+C`) or using the Shutdown button.

---

## Developer Notes

### Page Lifecycle

- `start()` initializes the page and starts data updates.
- `stop()` stops updates and returns to overview.
- Touch input is filtered to only affect the active page.
- Optional 0.5-second delay prevents ghost touches during page transitions.

### Graphing

- Uses Matplotlib to plot numeric metrics over time.
- Graph Y-axis is automatically scaled; zero is the default minimum unless negative values are present.
- X-axis shows timestamps or sample numbers depending on history data availability.
- Legends and units are automatically generated from available metrics.

### Touch Handling

- `check_touch()` validates touches against active buttons.
- Supports toggling individual metrics or grouped fan metrics.
- Footer buttons execute callbacks such as `Shutdown` or returning to the overview page.

### Logging

- Logs page lifecycle events, metric updates, and touch interactions.
- Warnings for missing assets (fonts, logos) are logged.

---

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request with clear descriptions of changes. Ensure your code follows existing style and passes all tests.

---

## License

This project is licensed under MIT License. See the LICENSE file for details.

---

## References

- [Pillow](https://pypi.org/project/Pillow/)  
- [Matplotlib](https://matplotlib.org/)  
- [NumPy](https://numpy.org/)  
- [pyserial](https://pypi.org/project/pyserial/)  
- [pyusb](https://pypi.org/project/pyusb/)