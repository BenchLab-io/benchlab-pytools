# BENCHLAB PyTools

BENCHLAB PyTools is the Python-based control suite for BENCHLAB telemetry devices. It provides a shared telemetry pipeline — device discovery, data sourcing, and process management — plus a set of consumer tools built on top of it:

- **TUI** — interactive terminal dashboard for live monitoring
- **CSV Logger** — fleet-wide telemetry logging for offline analysis
- **FastAPI Server** — REST API for telemetry, exposed for other tools/integrations
- **Graph** — DearPyGui-based real-time sensor graphing
- **HWiNFO Export** — exposes sensors as HWiNFO64 custom sensors
- **MQTT Publisher** — publishes telemetry to a local or remote MQTT broker
- **Link** — publishes telemetry to the BENCHLAB cloud (SaaS) MQTT broker
- **VU Dials** — analog-style VU meter dial display and configuration
- **WigiDash** — telemetry/graph display on a G.SKILL WigiDash panel
- **Config Tool** — import/export device configuration (fan curves, RGB, etc.) via JSON

All tools share a common data-source layer, so the same telemetry can be read directly from a device, through a FastAPI server, over MQTT, or via the Windows BENCHLAB service — locally or remotely — without changing the consumer tool.

---

## Installation

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

Each tool has its own `requirements.txt` (e.g. `benchlab/graph/requirements.txt`, `benchlab/vu/requirements.txt`). The launcher installs a tool's requirements automatically the first time it's run — you generally don't need to install them by hand.

---

## Usage

### Interactive Menu

Run with no arguments to enter the interactive launcher:

```bash
python benchlab.py
```

or equivalently:

```bash
python -m benchlab
```

The interactive menu (prompt_toolkit-based, with a plain-input fallback if `prompt_toolkit` isn't installed) lets you pick a data source and one or more tools, then launches them — installing any missing per-tool dependencies along the way.

### Command-Line Flags

Each tool can also be launched directly with a flag:

```bash
python -m benchlab -tui         # Interactive terminal dashboard
python -m benchlab -logfleet    # CSV logger (no TUI)
python -m benchlab -fastapi     # FastAPI telemetry server
python -m benchlab -graph       # DearPyGui graph
python -m benchlab -hwinfo      # HWiNFO custom sensor export
python -m benchlab -mqtt [broker]  # MQTT publisher (default broker: localhost)
python -m benchlab -link        # Publish telemetry to BENCHLAB cloud (Link)
python -m benchlab -vu          # VU analog dials
python -m benchlab -vuconfig    # VU dial configuration UI
python -m benchlab -wigidash    # WigiDash display
python -m benchlab -config ...  # Device configuration import/export
```

Running with no flags is equivalent to launching the interactive menu.

### Data Sources

Most tools accept `--source` to choose where telemetry comes from:

| Source | Description |
|---|---|
| `direct` (default) | Direct USB-serial connection via `benchlab-pycore` |
| `fastapi` | Local FastAPI server, started automatically if not already running |
| `fastapi_custom` | Remote FastAPI server — requires `--api-url` |
| `mqtt` | Local MQTT broker + publisher, started automatically if needed |
| `mqtt_custom` | Remote/existing MQTT broker — requires `--mqtt-broker`/`--mqtt-port` |
| `named_pipe` | Windows BENCHLAB service (`BL_Service`) via named pipes — Windows only |
| `service_http` | Windows BENCHLAB service HTTP API — requires `--service-url` (default `http://localhost:8585`) |

Common connection flags:

```
--source SOURCE          direct | fastapi | fastapi_custom | mqtt | mqtt_custom | named_pipe | service_http
--api-url URL             FastAPI base URL (default: http://127.0.0.1:8000)
--api-port PORT           FastAPI port (default: 8000)
--mqtt-broker HOST         MQTT broker host (default: localhost)
--mqtt-port PORT           MQTT broker port (default: 1883)
--service-url URL          BENCHLAB Windows service HTTP API URL (default: http://localhost:8585)
-i, --interval SECONDS     Refresh interval (default: 1.0)
```

When a source needs a background service (`fastapi`, `mqtt`), the launcher starts and health-checks it automatically, and tears it down on exit. Not every tool supports every source — the config tool, for example, only supports `direct` and `named_pipe`. See each tool's README for specifics.

### Launch Profiles

A named profile can bundle a data source and a set of tools to start together:

```bash
python -m benchlab --profile gskill_ctex26
```

Profiles are defined in `benchlab/tools.py` (`LAUNCH_PROFILES`). Each spawns its tools in separate terminal windows and manages them as a group.

---

## Architecture

- `benchlab/main.py` — CLI argument parsing and mode dispatch (`launch_mode()`)
- `benchlab/launcher.py` — in-process and multi-terminal tool launching, process lifecycle
- `benchlab/sources.py` — data-source detection, startup, and teardown for all supported sources
- `benchlab/tools.py` — the `CONSUMER_TOOLS` registry (tool metadata, module/function to invoke, dependencies) and `LAUNCH_PROFILES`
- `benchlab/menu.py` / `benchlab/menu_classic.py` — interactive terminal menu (prompt_toolkit, with a plain-input fallback)
- `benchlab/core/` — shared internals used by every tool: device discovery, the data-source abstraction, process management, retry logic. See [benchlab/core/README.md](benchlab/core/README.md).

Each consumer tool lives in its own subpackage under `benchlab/` with its own README, and (where needed) its own `requirements.txt`.

### Adding a New Tool

1. Add an entry to `CONSUMER_TOOLS` in `benchlab/tools.py` with `name`, `flag`, `module`, `function`, and `requirements`.
2. Implement the tool's entry function in its module, accepting an `args` namespace (see `benchlab/launcher.py::_build_args_namespace`).
3. Add a CLI flag for it in `benchlab/main.py::get_parser()` and dispatch it in `launch_mode()`.
4. Add a `requirements.txt` in the tool's directory if it has extra dependencies.
5. Write a `README.md` in the tool's directory following the style of the existing ones.

---

## Tool Documentation

- [Config Tool](benchlab/config/README.md) — device configuration import/export
- [Core](benchlab/core/README.md) — shared internals (data sources, process management, discovery)
- [CSV Logger](benchlab/csv_log/README.md)
- [FastAPI Server](benchlab/restapi/readme.md)
- [Graph](benchlab/graph/README.md)
- [HWiNFO Export](benchlab/hwinfo/README.md)
- [Link](benchlab/link/README.md) — publish telemetry to BENCHLAB cloud
- [MQTT Publisher](benchlab/mqtt/README.md)
- [TUI](benchlab/tui/README.md)
- [VU Dials](benchlab/vu/README.md)
- [WigiDash](benchlab/wigidash/README.md)

---

## License

Part of BENCHLAB PyTools. See main project license for details.
