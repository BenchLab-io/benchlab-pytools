# benchlab/vu/sensors.py

from benchlab.core.sensor_translation import translate_sensor_struct
from benchlab.core.serial_io import read_sensors, get_benchlab_ports, open_serial_connection
from benchlab.core.structures import SensorStruct

def get_available_sensors():
    """Return a list of human-readable sensor names."""
    sample_struct = SensorStruct()
    translated = translate_sensor_struct(sample_struct)
    return list(translated.keys())

def read_sensor_values(ser):
    """Read sensor struct from device and translate to dict."""
    try:
        sensor_struct = read_sensors(ser)
        return translate_sensor_struct(sensor_struct)
    except Exception as e:
        print(f"[ERROR] Failed to read sensors: {e}")
        return {}

def connect_device():
    """Open first available Benchlab serial device."""
    ports = get_benchlab_ports()
    if not ports:
        raise RuntimeError("No Benchlab devices found on serial ports.")
    ser = open_serial_connection(ports[0])
    if ser is None:
        raise RuntimeError(f"Failed to open serial port {ports[0]}")
    return ser
