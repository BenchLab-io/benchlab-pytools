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
- benchlab core modules


---

## Folder Structure

wigidash/
├─ README.md
├─ assets/ # Fonts, logos, and other UI resources
├─ init.py
├─ benchlab_fleet.py # Fleet selection UI
├─ benchlab_graph.py # Graph rendering and metric selection
├─ benchlab_overview.py # Overview page for system telemetry
├─ benchlab_telemetry.py # Data logging and historical telemetry handling
├─ benchlab_utils.py # Utilities for image display, logging, and device management
├─ wigidash_device.py # Device abstraction layer
├─ wigidash_usb.py # USB communication layer
├─ wigidash_widget.py # Dashboard widget configuration
├─ wigidash_launcher.py # Launcher for the dashboard
└─ requirements.txt


---

## Usage

### Launch the Dashboard

```
python wigidash_launcher.py
```

Behavior:

- Detects all connected BENCHLAB devices.
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