# benchlab/graph/sensors.py

from benchlab.core.sensor_translation import translate_sensor_struct
from benchlab.core.serial_io import read_sensors
from benchlab.core.structures import (
    SensorStruct,
    PowerSensor,
    FanSensor,
    SENSOR_POWER_NUM,
    FAN_NUM,
)

def get_available_sensors():
    """Return all telemetry fields that can be selected in the graph."""
    # Use a sample struct for generating keys
    from benchlab.core.structures import SensorStruct, PowerSensor, FanSensor, SENSOR_VIN_NUM

    sensors = []

    # Use translate_sensor_struct to get human-readable keys
    sample_struct = SensorStruct()  # empty struct just to extract keys
    translated = translate_sensor_struct(sample_struct)

    return list(translated.keys())

def get_sensor_value(sensor_struct, sensor_name):
    """
    Return the value of a sensor by name.
    Returns translated/human-readable value.
    """
    if not sensor_struct:
        return None

    translated = translate_sensor_struct(sensor_struct)
    return translated.get(sensor_name)