# benchlab/hwinfo_export.py

import winreg
import time
import logging
import atexit
from benchlab.core.serial_io import get_fleet_info, open_serial_connection, read_sensors
from benchlab.core.sensor_translation import translate_sensor_struct
from benchlab.core.structures import FAN_NUM

logger = logging.getLogger("hwinfo_export")
logger.setLevel(logging.INFO)
if not logger.handlers:
    import sys
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

HWINFO_CUSTOM_ROOT = winreg.HKEY_CURRENT_USER
HWINFO_CUSTOM_PATH = r"Software\HWiNFO64\Sensors\Custom"

IGNORE_KEYS = [f"Fan{i+1}_Status" for i in range(FAN_NUM)]
exported_devices = set()

# --- Map keys to HWiNFO types & units ---
def get_sensor_type_and_unit(key):
    key_lower = key.lower()
    if "temp" in key_lower or key_lower in ("chip_temp", "ambient_temp"):
        return "Temp", None
    elif "volt" in key_lower or key_lower.startswith("vin") or key_lower in ("vdd", "vref"):
        return "Volt", None
    elif "power" in key_lower:
        return "Power", None
    elif "current" in key_lower:
        return "Current", None
    elif "usage" in key_lower:
        return "Usage", "%"
    elif "fan" in key_lower and "rpm" in key_lower:
        return "Fan", None
    elif "clock" in key_lower:
        return "Clock", None
    elif "duty" in key_lower:
        return "Other", "%"
    elif "fanextduty" in key_lower:
        return "Other", "%"
    else:
        return "Other", "%"

def write_hwinfo_sensor(device_name, sensor_type, idx, name, value, unit=None):
    key_path = f"{HWINFO_CUSTOM_PATH}\\{device_name}\\{sensor_type}{idx}"
    try:
        with winreg.CreateKey(HWINFO_CUSTOM_ROOT, key_path) as key:
            # Always write Name first (required)
            winreg.SetValueEx(key, "Name", 0, winreg.REG_SZ, name)

            # Write Value
            if isinstance(value, float):
                winreg.SetValueEx(key, "Value", 0, winreg.REG_SZ, f"{value:.3f}")
            else:
                winreg.SetValueEx(key, "Value", 0, winreg.REG_DWORD, value)

            # Force-overwrite Unit safely
            try:
                winreg.DeleteValue(key, "Unit")
            except FileNotFoundError:
                pass
            if unit:
                winreg.SetValueEx(key, "Unit", 0, winreg.REG_SZ, unit)

        log_value = f"{value:.3f}" if isinstance(value, float) else str(value)
        if unit:
            logger.info("Created HWiNFO key: %s | Name=%s | Value=%s | Unit=%s",
                        key_path, name, log_value, unit)
        else:
            logger.info("Created HWiNFO key: %s | Name=%s | Value=%s",
                        key_path, name, log_value)
    except Exception as e:
        logger.warning("Failed to write %s%d for %s: %s", sensor_type, idx, device_name, e)

def export_device_sensors(device_info):
    uid = device_info["uid"]
    port = device_info["port"]
    device_name = f"BENCHLAB_{port}_{uid}"
    exported_devices.add(device_name)

    ser = open_serial_connection(port)
    if not ser:
        logger.error("Cannot open serial port for device %s", uid)
        return

    sensor_struct = read_sensors(ser)
    if not sensor_struct:
        logger.error("Failed to read sensors for device %s", uid)
        ser.close()
        return

    data = translate_sensor_struct(sensor_struct)

    grouped_sensors = {
        "Temp": [],
        "Volt": [],
        "Current": [],
        "Power": [],
        "Clock": [],
        "Usage": [],
        "Fan": [],
        "Other": []
    }

    for key, value in data.items():
        # Skip ignored keys and FanExtDuty completely
        if key in IGNORE_KEYS or key.lower() == "fanextduty":
            continue

        sensor_type, unit = get_sensor_type_and_unit(key)

        # Apply rounding to floats
        if isinstance(value, float):
            if sensor_type == "Volt":
                value = round(value, 3)
            elif sensor_type == "Temp":
                value = round(value, 1)
            elif sensor_type == "Power":
                value = round(value, 2)
            elif sensor_type == "Current":
                value = round(value, 3)
            elif sensor_type == "Other" and unit == "%":
                value = round(value, 1)
            elif sensor_type == "Other":
                value = round(value, 2)

        grouped_sensors[sensor_type].append((key, value, unit))

    # Export sensors in grouped order
    seq_counters = {k: 0 for k in grouped_sensors.keys()}
    for group in ["Power", "Volt", "Current", "Temp", "Usage", "Clock", "Fan", "Other"]:
        for key, value, unit in grouped_sensors[group]:
            idx = seq_counters[group]
            seq_counters[group] += 1
            write_hwinfo_sensor(device_name, group, idx, key, value, unit)

    ser.close()

    summary = ", ".join(f"{k}: {len(v)}" for k, v in grouped_sensors.items())
    logger.info("Device %s export summary: %s", device_name, summary)

def delete_registry_tree(root, path):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            # Delete subkeys recursively
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, 0)
                    delete_registry_tree(root, f"{path}\\{subkey_name}")
                except OSError:
                    break
            # Delete all values in this key
            try:
                while True:
                    value_name = winreg.EnumValue(key, 0)[0]
                    winreg.DeleteValue(key, value_name)
            except OSError:
                pass
        winreg.DeleteKey(root, path)
        logger.info("Removed registry key: %s", path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Failed to remove key %s: %s", path, e)

def cleanup_registry():
    for device_name in exported_devices:
        delete_registry_tree(HWINFO_CUSTOM_ROOT, f"{HWINFO_CUSTOM_PATH}\\{device_name}")

atexit.register(cleanup_registry)

def export_all_devices(update_interval=1):
    # Remove only old BenchLab entries, not user-created sensors
    try:
        with winreg.OpenKey(HWINFO_CUSTOM_ROOT, HWINFO_CUSTOM_PATH, 0, winreg.KEY_ALL_ACCESS) as root_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root_key, i)
                    if subkey_name.startswith("BENCHLAB_"):
                        delete_registry_tree(HWINFO_CUSTOM_ROOT, f"{HWINFO_CUSTOM_PATH}\\{subkey_name}")
                    else:
                        i += 1
                except OSError:
                    break
    except FileNotFoundError:
        pass

    try:
        while True:
            fleet = get_fleet_info()
            if not fleet:
                logger.warning("No BenchLab devices found")
            for device in fleet:
                export_device_sensors(device)
            time.sleep(update_interval)
    except KeyboardInterrupt:
        logger.info("Stopping HWiNFO export...")
        logger.info("Cleaning up registry keys...")
        cleanup_registry()
        logger.info("Done.")

if __name__ == "__main__":
    logger.info("Starting BenchLab HWiNFO exporter")
    export_all_devices(update_interval=1)