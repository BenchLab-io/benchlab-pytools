# benchlab/hwinfo_export.py

import os
import sys
import time
import logging
import atexit
from benchlab_pycore.core import (
    translate_sensor_struct,
    FAN_NUM,
    read_device,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
)
from benchlab.core.datasource import create_datasource, DataSource

# Conditional import for Windows-only winreg module (BUG-7.2)
if sys.platform.startswith('win'):
    import winreg
else:
    winreg = None

logger = logging.getLogger("hwinfo_export")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Only define registry constants on Windows (BUG-7.2)
if winreg is not None:
    HWINFO_CUSTOM_ROOT = winreg.HKEY_CURRENT_USER
    HWINFO_CUSTOM_PATH = r"Software\HWiNFO64\Sensors\Custom"

IGNORE_KEYS = [f"Fan{i + 1}_Status" for i in range(FAN_NUM)]
exported_devices = set()

# --- Map keys to HWiNFO types & units ---


def get_sensor_type_and_unit(key):
    key_lower = key.lower()
    if "temp" in key_lower or key_lower in ("chip_temp", "ambient_temp"):
        return "Temp", None
    elif ("volt" in key_lower or key_lower.startswith("vin")
          or key_lower in ("vdd", "vref")):
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
                winreg.SetValueEx(
                    key, "Value", 0, winreg.REG_SZ, f"{value:.3f}")
            else:
                winreg.SetValueEx(key, "Value", 0, winreg.REG_SZ, str(value))

            # Force-overwrite Unit safely
            try:
                winreg.DeleteValue(key, "Unit")
            except FileNotFoundError:
                pass
            if unit:
                winreg.SetValueEx(key, "Unit", 0, winreg.REG_SZ, unit)

        log_value = f"{value:.3f}" if isinstance(value, float) else str(value)
        if unit:
            logger.info(
                "Created HWiNFO key: %s | Name=%s | Value=%s | Unit=%s",
                key_path,
                name,
                log_value,
                unit)
        else:
            logger.info("Created HWiNFO key: %s | Name=%s | Value=%s",
                        key_path, name, log_value)
    except Exception as e:
        logger.warning("Failed to write %s%d for %s: %s",
                       sensor_type, idx, device_name, e)


def _process_sensor_data(data: dict) -> dict:
    """Translate raw sensor dict into HWiNFO grouped sensors with rounding."""
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
        if key in IGNORE_KEYS or key.lower() in ("fanextduty", "timestamp"):
            continue

        sensor_type, unit = get_sensor_type_and_unit(key)

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

    return grouped_sensors


def _export_grouped(device_name: str, grouped_sensors: dict):
    """Write grouped sensors to HWiNFO registry."""
    seq_counters = {k: 0 for k in grouped_sensors.keys()}
    for group in [
        "Power",
        "Volt",
        "Current",
        "Temp",
        "Usage",
        "Clock",
        "Fan",
            "Other"]:
        for key, value, unit in grouped_sensors[group]:
            idx = seq_counters[group]
            seq_counters[group] += 1
            write_hwinfo_sensor(device_name, group, idx, key, value, unit)

    summary = ", ".join(
        f"{k}: {len(v)}" for k,
        v in grouped_sensors.items() if v)
    logger.debug("Device %s export summary: %s", device_name, summary)


def export_device_sensors(device_info, datasource=None):
    """Export sensors from a device to HWiNFO registry.

    Args:
        device_info: Device info dict with uid and port
        datasource: Optional DataSource to use (falls back to
            DirectDataSource env config if None)

    Returns:
        True if export succeeded, False otherwise.
    """
    uid = device_info["uid"]
    port = device_info["port"]
    device_name = f"BENCHLAB_{port}_{uid}"
    exported_devices.add(device_name)

    # Get telemetry from datasource
    if datasource is not None:
        # Use provided data source
        data = datasource.get_telemetry(uid)
        if not data:
            logger.debug(
                "No telemetry for %s via %s yet",
                uid,
                datasource.source_type)
    else:
        # Fallback: direct probe via pycore (legacy behavior)
        try:
            from benchlab_pycore.core import read_sensors
            from benchlab.core.shared_serial import open_serial_connection
            ser = open_serial_connection(port)
            if not ser:
                logger.error("Cannot open serial port for device %s", uid)
                return False
            # Get product_id for correct sensor interpretation (BL2 vs
            # ORIGINAL)
            product_id = BENCHLAB_ORIGINAL_PRODUCT_ID
            try:
                device_info = read_device(ser)
                if device_info:
                    product_id = device_info.get(
                        'ProductId', BENCHLAB_ORIGINAL_PRODUCT_ID)
            except Exception:
                pass
            sensor_struct = read_sensors(ser, product_id=product_id)
            ser.close()
            if not sensor_struct:
                logger.error("Failed to read sensors for device %s", uid)
                return False
            data = translate_sensor_struct(sensor_struct)
        except Exception as e:
            logger.error(
                "Error exporting sensors for device %s (fallback): %s", uid, e)
            return False

    if not data:
        logger.error("No telemetry data for device %s", uid)
        return False

    try:
        grouped_sensors = _process_sensor_data(data)
        _export_grouped(device_name, grouped_sensors)
        return True
    except Exception as e:
        logger.error("Error exporting sensors for device %s: %s", uid, e)
        return False


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
        delete_registry_tree(
            HWINFO_CUSTOM_ROOT,
            f"{HWINFO_CUSTOM_PATH}\\{device_name}")


atexit.register(cleanup_registry)


def _select_datasource() -> DataSource:
    """Select a data source based on environment config or default to
    direct."""
    source_type = os.environ.get("BENCHLAB_DATA_SOURCE", "direct")
    kwargs = {}

    if source_type in ("fastapi", "fastapi_custom"):
        kwargs["base_url"] = os.environ.get(
            "BENCHLAB_API_URL", "http://127.0.0.1:8000")
    elif source_type == "mqtt":
        kwargs["broker"] = os.environ.get("MQTT_BROKER", "localhost")
        kwargs["port"] = int(os.environ.get("MQTT_PORT", "1883"))
        kwargs["topic_prefix"] = os.environ.get(
            "MQTT_TOPIC_PREFIX", "benchlab")

    ds = create_datasource(source_type, **kwargs)
    if ds.connect():
        logger.info("Using data source: %s", source_type)
        return ds
    else:
        logger.warning(
            "Failed to connect via %s, falling back to direct",
            source_type)
        ds = create_datasource("direct")
        if ds.connect():
            return ds
        raise RuntimeError("All data sources failed")


def export_all_devices(update_interval=1, datasource=None):
    """Export all devices continuously using the configured data source.

    Args:
        update_interval: Seconds between export cycles
        datasource: Optional DataSource to use. If None, auto-selects via
            env config.
    """
    # Check Windows availability (BUG-7.2)
    if winreg is None:
        logger.error(
            "HWiNFO export is only supported on Windows. "
            "Please run on a Windows system.")
        raise RuntimeError(
            "HWiNFO export requires Windows. "
            f"Current platform: {sys.platform}")

    # Remove only old BenchLab entries, not user-created sensors
    try:
        with winreg.OpenKey(
                HWINFO_CUSTOM_ROOT, HWINFO_CUSTOM_PATH, 0,
                winreg.KEY_ALL_ACCESS) as root_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root_key, i)
                    if subkey_name.startswith("BENCHLAB_"):
                        delete_registry_tree(
                            HWINFO_CUSTOM_ROOT,
                            f"{HWINFO_CUSTOM_PATH}\\{subkey_name}")
                    else:
                        i += 1
                except OSError:
                    break
    except FileNotFoundError:
        pass

    # Set up data source
    if datasource is None:
        datasource = _select_datasource()

    try:
        while True:
            # Get device list from data source
            fleet = datasource.list_devices()

            # Remove registry entries for devices no longer present in the
            # fleet
            current_device_names = {
                f"BENCHLAB_{device.get('port', 'unknown')}"
                f"_{device.get('uid', 'unknown')}"
                for device in fleet
            }
            for stale_name in exported_devices - current_device_names:
                delete_registry_tree(
                    HWINFO_CUSTOM_ROOT,
                    f"{HWINFO_CUSTOM_PATH}\\{stale_name}")
                exported_devices.discard(stale_name)

            if not fleet:
                logger.warning(
                    "No BenchLab devices found via %s",
                    datasource.source_type)
                time.sleep(update_interval)
                continue

            # Export each device
            for device in fleet:
                port = device.get("port", "unknown")
                uid = device.get("uid", "unknown")
                device_name = f"BENCHLAB_{port}_{uid}"

                success = export_device_sensors(device, datasource)
                if success:
                    logger.debug("Exported %s", device_name)
                else:
                    logger.warning("Failed to export %s", device_name)

            time.sleep(update_interval)
    except KeyboardInterrupt:
        logger.info("Stopping HWiNFO export...")
        datasource.disconnect()
        logger.info("Cleaning up registry keys...")
        cleanup_registry()
        logger.info("Done.")


if __name__ == "__main__":
    logger.info("Starting BenchLab HWiNFO exporter")
    export_all_devices(update_interval=1)
