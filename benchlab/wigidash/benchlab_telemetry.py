# benchlab_telemetry.py

from collections import defaultdict, deque

import time
import threading

from benchlab.wigidash.benchlab_utils import get_logger

logger = get_logger("BenchlabTelemetry")


class TelemetryHistory:
    """
    Keep historical data for all telemetry points dynamically, with timestamps.
    """

    def __init__(self, max_samples=1000):
        self.max_samples = max_samples
        self.data = defaultdict(lambda: deque(maxlen=self.max_samples))
        self.lock = threading.Lock()

    def add_sample(self, sample: dict):
        timestamped = {"timestamp": time.time(), **sample}
        with self.lock:
            for key, value in timestamped.items():
                self.data[key].append(value)

    def get_history(self, key):
        with self.lock:
            return list(self.data.get(key, []))

    def latest_snapshot(self):
        with self.lock:
            return {k: v[-1] if v else None for k, v in self.data.items()}


class TelemetryState:
    """
    Mutable telemetry state owned by the manager but consumed by UI.
    """
    __slots__ = ("device_info", "uid", "sensor_data")

    def __init__(self, uid=None):
        self.device_info = None
        self.uid = uid
        self.sensor_data = None


class TelemetryContext:
    def __init__(self, port, ser, device_info, uid, history):
        self.port = port
        self.ser = ser
        self.device_info = device_info
        self.uid = uid
        self.history = history
        self.sessions = []   # attached WigiSessions


def telemetry_step(app, device_info=None, sensor_struct=None):
    """
    Process one telemetry iteration.
    - device_info: optional dict already read from serial
    - sensor_struct: optional raw sensor data (from serial) or already
      translated dict (from DataSource)
    """
    try:
        # Use device info if provided
        if device_info and app.device_info is None:
            app.device_info = device_info
            if 'UID' in device_info and app.uid is None:
                app.uid = device_info['UID']

        if sensor_struct is None:
            logger.warning("No sensor data provided to telemetry_step")
            return

        # sensor_struct is always a pre-translated dict from DataSourceManager
        if not isinstance(sensor_struct, dict):
            logger.warning(
                "Unexpected sensor_struct type: %s",
                type(sensor_struct))
            return
        data = dict(sensor_struct)

        # --- Cleanup & normalization ---
        # Copy Fans/Vin too so the in-place mutations below don't leak back
        # into the caller's original lists/dicts.
        data["Fans"] = [dict(fan) for fan in data.get("Fans", [])]
        data["Vin"] = list(data.get("Vin", []))
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
