# benchlab/vu/sensors.py


def get_available_sensors(snapshot: dict = None) -> list:
    """Return sensor keys from a live datasource snapshot.

    Falls back to an empty list if no snapshot is available yet.
    """
    if not snapshot:
        return []
    return [k for k in snapshot if k.lower() != "timestamp"]


def get_sensor_value(snapshot: dict, sensor_name: str):
    """Return the value of sensor_name from a telemetry snapshot dict."""
    if not snapshot or not sensor_name:
        return None
    return snapshot.get(sensor_name)
