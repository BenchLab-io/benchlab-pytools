# BenchLab HWiNFO Exporter

**benchlab/hwinfo_export.py** is a Python utility that bridges BenchLab telemetry data with **HWiNFO64**’s custom sensor interface. It exports live sensor data (temperature, voltage, current, power, fan speed, and more) directly into HWiNFO’s registry-based “Custom” sensors section.

This allows seamless integration of BenchLab devices with the HWiNFO monitoring and logging ecosystem.

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

- **Windows** (uses `winreg`)
- **HWiNFO64** installed (for reading custom sensors)
- **Python 3.8+**
- BenchLab Core library available (providing `serial_io`, `sensor_translation`, etc.)

---

## Usage

Run the exporter from the BenchLab project directory:

python -m benchlab.hwinfo_export


It will:
1. Enumerate connected BenchLab devices
2. Continuously update sensor data in the registry
3. Make readings available in HWiNFO’s Custom Sensors section

Press **Ctrl+C** to stop. The script cleans up its registry keys automatically.

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