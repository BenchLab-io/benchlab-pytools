# BENCHLAB PyTools

## Overview

BENCHLAB PyTools is the main entry point for interacting with BENCHLAB telemetry devices using Python.  
It provides a modular launcher to start different modes of operation, including:

- Interactive terminal TUI
- CSV logging for offline analysis
- MQTT publishing for remote dashboards
- GUI graphing
- VU analog-style dials and configuration

The launcher can operate in interactive mode or via command-line flags.

---

## Available Modes

| Mode | Flag | Description | Notes |
|------|------|------------|-------|
| TUI | -tui | Interactive terminal UI | Displays a live TUI for monitoring multiple devices. |
| CSV Logging | -logfleet | Logs device data to CSV | Supports single-device and fleet logging. |
| MQTT Publisher | -mqtt | Publishes telemetry to MQTT broker | Can connect to localhost or a remote broker. |
| Graph | -graph | GUI graphing interface | Visualizes telemetry trends in real time. |
| VU Dials | -vu | Analog-style VU dials | Visual monitoring of device metrics. |
| VU Config | -vuconfig | VU configuration interface | Customize VU dials and settings interactively. |

---

## Installation

BENCHLAB PyTools relies on Python 3.8+ and optional modules for each mode.  

Install core dependencies:

```
pip install -r requirements_core.txt
```

Optional mode-specific dependencies are in:

- `requirements_tui.txt`
- `requirements_csv_log.txt`
- `requirements_mqtt.txt`
- `requirements_graph.txt`
- `requirements_vu.txt`

The launcher will attempt to install missing requirements for selected modes automatically.

---

## Usage

### Interactive Launcher

Start without arguments:

```
python benchlab.py
```

- Presents a menu to select modes and features.
- Optionally installs requirements for selected modes.
- Allows selecting which mode to start immediately.

### Command-Line Flags

Run directly using flags:

```
python benchlab.py -tui
python benchlab.py -logfleet
python benchlab.py -mqtt
python benchlab.py -graph
python benchlab.py -vu
python benchlab.py -vuconfig
```

### Info Mode

```
python benchlab.py -info
```

Displays detailed mode descriptions and exits.

---

## Launcher Behavior

1. **Mode Selection**
   - Interactive menu lists all modes with descriptions.
   - User can select one or multiple modes.
   - If `all` is selected, launcher installs dependencies for all available modules.

2. **Dependency Installation**
   - Checks for `requirements_<tag>.txt` per mode.
   - Silently skips missing requirement files.
   - Installs via pip if present.

3. **Mode Execution**
   - Rewrites `sys.argv` to dispatch to the selected mode.
   - Default behavior launches TUI if no flags are provided.
   - Each mode is isolated; missing modules produce informative messages.

---

## Developer Notes

### Modular Design

- `MODES` dictionary defines available modes, flags, requirements, and descriptions.
- `launch_mode()` handles CLI dispatching to mode-specific runners.
- Default TUI mode uses `curses.wrapper` for terminal display.

### Adding New Modes

1. Add a new entry to the `MODES` dictionary with:
   - `flag`, `reqs`, `desc`, `info`.
2. Implement a corresponding runner in `benchlab.<module>` and import it in `launch_mode()`.
3. Optionally provide a `requirements_<tag>.txt` file.

### Interactive Menu

- Clears the screen and shows mode selection.
- Supports comma-separated input (e.g., `1,3,5`) or `all`.
- Supports an info view with detailed mode descriptions.
- Automatically installs requirements for selected modes if available.

---

## References

- [BENCHLAB core modules](https://github.com/<your-org>/benchlab/tree/main/benchlab/core)  
- Individual module READMEs:
  - [TUI](../tui/README.md)
  - [CSV Logger](../csv_log/README.md)
  - [MQTT Publisher](../mqtt/README.md)
  - [Graph](../graph/README.md)
  - [VU](../vu/README.md)
