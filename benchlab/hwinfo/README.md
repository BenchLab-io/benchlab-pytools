# BenchLab HWiNFO Exporter

**benchlab/hwinfo/hwinfo_export.py** (flag: `-hwinfo`) is a Python utility that bridges BenchLab telemetry data with **HWiNFO64**'s custom sensor interface. It exports live sensor data (temperature, voltage, current, power, fan speed, and more) directly into HWiNFO's registry-based "Custom" sensors section.

This allows seamless integration of BenchLab devices with the HWiNFO monitoring and logging ecosystem.

**Windows only** — the tool uses the `winreg` module and raises a `RuntimeError` on start if not run on Windows (`sys.platform` does not start with `win`).

---

## Features

- Automatically detects connected BenchLab devices via serial interface
- Exports all sensor data to `HKEY_CURRENT_USER\Software\HWiNFO64\Sensors\Custom`
- Supports all major sensor types:
  - Temperature
  - Voltage
  - Current
  - Power
  - Clock
  - Usage
  - Fan
  - Other (generic percentage or numeric values)
- Handles registry cleanup safely on exit
- Preserves non-BenchLab user-created HWiNFO custom entries
- Logs all operations with timestamps and status messages

---

## Registry Structure

Each BenchLab device gets its own subkey under:

HKEY_CURRENT_USER\Software\HWiNFO64\Sensors\Custom\BENCHLAB_<PORT>_<UID>

Within that key, individual sensors are grouped by type:

Power0, Power1, ...
Volt0, Volt1, ...
Temp0, Temp1, ...
Fan0, Fan1, ...
Other0, Other1, ...


Each sensor key contains:
- **Name** (string): sensor label
- **Value** (string or DWORD): sensor value
- **Unit** (string, optional): e.g. `%`, `°C`, `V`

---

## Safe Cleanup

The script only removes keys starting with `BENCHLAB_`.  
Any user-created custom sensors under `\Custom` remain untouched.

During runtime or on exit:
- Old BenchLab keys are removed
- New sensor keys are created
- Registry is cleaned up automatically when the script exits

---

## Requirements

- **Windows** (uses `winreg`) — the tool refuses to run on other platforms
- **HWiNFO64** installed (for reading custom sensors)
- **Python 3.8+**
- `benchlab-pycore` (device protocol) and `pyserial` — see Installation below

---

## Installation

Install the tool's dependencies (from `benchlab/hwinfo/requirements.txt`):

```bash
pip install -r benchlab/hwinfo/requirements.txt
```

Key dependency: `pyserial>=3.5` (plus `benchlab-pycore`, already required by the main package).

---

## Usage

Run the exporter from the BenchLab project directory via the main launcher:

```bash
# Default (direct/serial source, 1s update interval)
python -m benchlab -hwinfo

# Custom interval
python -m benchlab -hwinfo -i 2

# Choose a data source (direct | fastapi | fastapi_custom | mqtt | mqtt_custom | named_pipe | service_http)
python -m benchlab -hwinfo --source fastapi --api-url http://127.0.0.1:8000
python -m benchlab -hwinfo --source mqtt --mqtt-broker localhost --mqtt-port 1883
```

`-i`/`--interval` controls `update_interval` (seconds between export cycles, default 1). `--source`, `--api-url`, `--mqtt-broker`, and `--mqtt-port` are the standard `benchlab/main.py` flags — the exporter has no `-hwinfo`-specific CLI flags of its own; all sources are supported (no `supported_sources` restriction in `benchlab/tools.py`).

It will:
1. Enumerate connected BenchLab devices via the selected data source
2. Continuously update sensor data in the registry
3. Make readings available in HWiNFO's Custom Sensors section
4. Remove registry entries for any device that drops out of the fleet

Press **Ctrl+C** to stop. The script cleans up its registry keys automatically (`cleanup_registry()` is also registered via `atexit`).

---

## Logging

All activity is logged to the console, including:
- Detected devices
- Exported sensors and values
- Registry creation/deletion events
- Error or warning messages

Example log output:

2025-10-20 09:33:24 [INFO] Created HWiNFO key: ...\Temp0 | Name=Chip_Temp | Value=42.1
2025-10-20 09:33:24 [INFO] Device BENCHLAB_COM4_1234 export summary: Temp: 5, Volt: 3, Power: 2


---

## Notes

- Skips keys like `FanExtDuty` and internal `Fan*_Status` to avoid redundant data.
- Automatically rounds floating-point values for human readability.
- Designed to integrate smoothly with live BenchLab telemetry streams.

---