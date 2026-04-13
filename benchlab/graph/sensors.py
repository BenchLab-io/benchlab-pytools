# benchlab/graph/sensors.py


def get_available_sensors(sensor_data: dict = None) -> list:
    """Return sensor keys available for graphing.

    Parameters
    ----------
    sensor_data:
        A telemetry dict from a datasource snapshot. When provided, the keys
        are derived from live data. When None, returns an empty list — the UI
        should call this again once data arrives.
    """
    if not sensor_data:
        return []
    return [k for k in sensor_data if k.lower() != "timestamp"]


def get_sensor_value(sensor_struct, sensor_name: str):
    """Return the value for sensor_name from a telemetry dict.

    sensor_struct is always a plain dict in datasource mode.
    """
    if not sensor_struct or not sensor_name:
        return None
    return sensor_struct.get(sensor_name)