# benchlab_telemetry.py

from collections import defaultdict, deque
import time
import threading

from benchlab.core import serial_io
from benchlab.core.sensor_translation import translate_sensor_struct

from benchlab.wigidash.benchlab_utils import get_logger

logger = get_logger("BenchlabTelemetry")

class TelemetryHistory:
    """Keep historical data for all telemetry points dynamically, with timestamps."""
    def __init__(self, max_samples=1000):
        self.max_samples = max_samples
        self.data = defaultdict(lambda: deque(maxlen=self.max_samples))
        self.lock = threading.Lock()

    def add_sample(self, sample: dict):
        """Add a full telemetry sample."""
        timestamped_sample = {"timestamp": time.time(), **sample}
        with self.lock:
            for key, value in timestamped_sample.items():
                if key not in self.data:
                    self.data[key] = deque(maxlen=self.max_samples)
                self.data[key].append(value)

    def get_history(self, key):
        with self.lock:
            return list(self.data.get(key, []))


def telemetry_step(app):
    """Execute one telemetry iteration for a BenchlabWigi instance."""
    try:
        if not app.ser:
            logger.warning("No serial connection available for telemetry")
            return
        
        # --- Read device info if not set ---
        if app.device_info is None:
            di = serial_io.read_device(app.ser)
            if di:
                app.device_info = di
                if 'UID' in di and app.uid is None:
                    app.uid = di['UID']
                logger.info(f"Device info read successfully: UID={app.uid}")
            else:
                logger.warning("Failed to read device info from serial port")
            return

        # --- Read sensors ---
        sensor_struct = serial_io.read_sensors(app.ser)
        if sensor_struct is None:
            logger.warning("Failed to read sensors from serial port")
            return

        data = translate_sensor_struct(sensor_struct)
        if data is None:
            logger.warning("Failed to translate sensor data")
            return

        # --- Cleanup & normalization ---
        data.setdefault("Fans", [])
        data.setdefault("Vin", [])
        for fan in data["Fans"]:
            fan.setdefault("RPM", 0)
            fan.setdefault("Duty", 0)
            fan.setdefault("Status", 0)
        for i, vin in enumerate(data["Vin"]):
            if vin is None:
                data["Vin"][i] = 0.0

        # --- Store telemetry ---
        app.sensor_data = data
        app.history.add_sample(data)
        logger.debug(f"Telemetry step recorded with {len(data)} keys")

    except Exception as e:
        logger.exception(f"Telemetry step error: {e}")
