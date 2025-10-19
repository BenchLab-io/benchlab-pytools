# Benchlab Multi-Device Telemetry API

A Python-based API server and telemetry platform for **Benchlab devices**, providing live device monitoring, historical data, and WebSocket streaming. Built with **FastAPI**, this tool reads sensor data over serial connections and exposes it in a standardized API for easy integration with dashboards, GUIs, or other software.

---

## Features

- Automatically detects connected Benchlab devices over USB serial.
- Periodic polling and caching of telemetry data.
- Endpoints to query:
  - Device info
  - Latest telemetry
  - Individual sensor readings
  - Historical telemetry
  - Available sensors
- WebSocket support for live telemetry streaming.
- Configurable via `.env` file:
  - Poll interval
  - History length
  - API host/port
  - Log level
- Optional TUI, MQTT, GUI, and VU analog dials (via command-line flags in the main launcher).

---

## Installation

1. Clone the repository:

```
git clone https://github.com/BenchLab-io/benchlab-pytools.git
cd benchlab-pytools
```

2. Create and activate a Python virtual environment:

```
python -m venv benchlab
# Linux/macOS
source benchlab/bin/activate
# Windows
benchlab\Scripts\activate
```

3. Install dependencies:

```
pip install -r fastapi/requirements.txt
```

4. Copy `.env.example` to `.env` and customize if needed:

```
LOG_LEVEL=INFO
POLL_INTERVAL=1.0
HISTORY_LENGTH=10
API_HOST=0.0.0.0
API_PORT=8000
```

---

## Running the API

From the `benchlab-pytools` folder:

```
python benchlab.py -fastapi
```

By default, the server runs on the host and port specified in `.env` (`0.0.0.0:8000`).

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/devices` | GET | List all connected Benchlab devices with ports |
| `/device/{uid}/info` | GET | Return basic device info (cached) |
| `/device/{uid}/telemetry` | GET | Return latest telemetry readings |
| `/device/{uid}/telemetry/{sensor}` | GET | Return a single sensor reading |
| `/device/{uid}/history` | GET | Return last N telemetry readings |
| `/device/{uid}/sensors` | GET | Return all available sensor keys |
| `/device/{uid}/stream` | WS | WebSocket endpoint for live telemetry |

> Example:
>
> ```
> curl http://localhost:8000/device/2C003D001457435735363620/telemetry
> ```

---

## FastAPI Folder Structure

```
fastapi/
├── __init__.py
├── telemetry_api.py      # Main FastAPI server
├── readme.md             # This README
├── requirements.txt      # FastAPI dependencies
├── benchlab.ico          # Optional favicon
└── __pycache__/          # Python cache folder
```

---

## Configuration via `.env`

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | INFO | Logging level for the API |
| `POLL_INTERVAL` | 1.0 | Seconds between device polls |
| `HISTORY_LENGTH` | 10 | Number of telemetry entries to keep in memory |
| `API_HOST` | 0.0.0.0 | Host address to serve API |
| `API_PORT` | 8000 | Port to serve API |

---

## Contributing

1. Fork the repository.
2. Create a new branch for your feature/bugfix.
3. Submit a pull request with a clear description of your changes.

---

## Notes

- Ensure you have permission to access the USB serial ports.
- The API caches device info and telemetry; querying endpoints **does not reopen serial ports**.
- WebSocket clients will receive live telemetry updates at the configured polling interval.

---

Made with ❤️ by the Benchlab Team
