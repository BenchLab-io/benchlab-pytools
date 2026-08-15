# BENCHLAB VU Dials

Drives physical analog-style USB "VU dial" gauges (via the bundled [VU-Server](VU-Server/)) using live BENCHLAB telemetry, and provides a terminal UI for mapping dials to sensors.

## Overview

This tool has two pieces:

- **VU Updater** (`-vu`) — polls BENCHLAB telemetry via the shared data-source layer, resolves each configured dial's mapped sensor value, and pushes it to the VU-Server API so the physical dial needle moves. Also renders and uploads a sensor logo image to each dial's screen.
- **VU Config** (`-vuconfig`) — a curses TUI (`vu_tui.py`) for discovering connected VU dials and BENCHLAB devices, and mapping each dial to a specific device + sensor.

Both talk to a local **VU-Server** process (bundled in [`VU-Server/`](VU-Server/), a fork of [ThePi910FC's VU1 server](VU-Server/README.md)), which the tool starts and manages automatically if it isn't already running.

## Installation

```bash
pip install -r benchlab/vu/requirements.txt
```

Key dependencies: `requests`, `PyYAML`, `Pillow` (logo rendering), `pyserial`, `tornado` (VU-Server), plus `blessed` for the legacy curses-adjacent terminal helpers.

## Usage

### Configure dial-to-sensor mappings

```bash
python -m benchlab -vuconfig
```

Lists detected VU dials and BENCHLAB devices/sensors, and lets you assign a sensor to each dial. Mappings are written to `benchlab/vu/vu_dial.config`.

### Run the updater

```bash
python -m benchlab -vu
```

Starts (or connects to) VU-Server, then continuously polls telemetry and updates each mapped dial. Supports the standard data-source flags (`--source`, `--api-url`, `--mqtt-broker`, etc. — see the [top-level README](../../README.md#data-sources)) and `-i/--interval` for poll frequency.

The updater watches `vu_dial.config` for changes and hot-reloads mappings without restarting.

## Configuration

- **`vu_server.config`** — VU-Server connection settings:
  ```json
  {
    "vu_server_url": "http://localhost:5340",
    "api_key": "...",
    "logo_file": "assets/bl_logo_144x200.png"
  }
  ```
- **`vu_dial.config`** — list of dial-to-sensor mappings (`dial_uid`, `dial_name`, `benchlab_uid`, `benchlab_port`, `sensor`), managed by `-vuconfig` or by editing directly.
- **`VU-Server/config.yaml`** — the underlying VU-Server's own device/serial configuration.

## Troubleshooting

- **VU-Server won't start** — check `vu_updater.log` in this directory; the updater forwards VU-Server's stdout/stderr there.
- **No dials detected** — VU dials enumerate as USB serial devices; verify they're connected and recognized by the OS before running `-vuconfig`.
- **Dial not updating** — confirm the mapped sensor name still exists in the current telemetry snapshot (sensor keys can change if a device's config changes).

## See Also

- [BENCHLAB PyTools Documentation](../../README.md)
- [VU-Server README](VU-Server/README.md) — the bundled dial server itself
