# benchlab/graph/device.py

import threading
import time
import logging
from dearpygui import dearpygui as dpg
from benchlab_pycore.core import read_sensors, get_benchlab_ports, read_uid, read_device, translate_sensor_struct
from benchlab_pycore.core.serial_io import open_serial_connection

_logger = logging.getLogger("benchlab.graph.device")


def detect_devices(app):
    """Scan for devices (no port opening) and update combo box."""
    try:
        ports = get_benchlab_ports()
        devices = [{"port": p["port"], "uid": "?", "firmware": "?"} for p in ports]
        devices_sorted = sorted(devices, key=lambda d: d["port"])

        with app.lock:
            app.devices = devices_sorted
            if app.devices:
                app.active_device = app.devices[0]
                device_items = [d["port"] for d in app.devices]
                if dpg.does_item_exist("##device_combo"):
                    dpg.configure_item("##device_combo", items=device_items, default_value=device_items[0])
                app.start_sensor_thread()
            else:
                app.active_device = None
                if dpg.does_item_exist("##device_combo"):
                    dpg.configure_item("##device_combo", items=["<No devices>"], default_value="<No devices>")
    except Exception as e:
        _logger.error("Failed to detect devices: %s", e)


def device_changed(app, sender, app_data):
    """Callback when user selects a different device from combo box."""
    with app.lock:
        app.active_device = next((d for d in app.devices if d["port"] == app_data), None)

    if app.active_device:
        threading.Thread(target=app.restart_sensor_thread, daemon=True).start()


def restart_sensor_thread(app):
    """Stop current sensor thread and start a new one without freezing GUI."""
    app.stop_sensor_thread()
    app.start_sensor_thread()


def start_sensor_thread(app):
    """Start reading sensors from the active device in a separate thread.

    Uses DataSource if available, otherwise falls back to direct serial.
    """
    app.stop_sensor_thread()
    if not app.active_device:
        return

    def sensor_loop():
        with app.lock:
            device = app.active_device.copy()
        device_port = device["port"]

        # ── DataSource path ──────────────────────────────────────────
        datasource = getattr(app, "datasource", None)
        if datasource is not None:
            _run_datasource_loop(app, datasource, device_port)
            return

        # ── Direct serial fallback ───────────────────────────────────
        _run_serial_loop(app, device_port)

    app.worker_thread = threading.Thread(target=sensor_loop, daemon=True)
    app.worker_thread.start()


def _run_datasource_loop(app, datasource, device_port):
    """Sensor loop using a DataSource abstraction."""
    try:
        devices = datasource.list_devices()
        target = None
        for d in devices:
            if d.get("port") == device_port:
                target = d
                break

        if target is None and devices:
            target = devices[0]

        uid = target.get("uid", "?") if target else "?"

        with app.lock:
            app.connected = True
            app.active_device["uid"] = uid
            app.latest_uid = uid

        # Main polling loop — DataSource pre-polls telemetry in background
        while not app.stop_event.is_set():
            data = datasource.get_telemetry(uid)
            if data:
                # Create a pseudo sensor_struct-like object for backward compat
                # by storing the dict directly (translate_sensor_struct returns dict)
                with app.lock:
                    app.sensor_struct = data  # dict instead of raw struct
                    app.sensor_data_dict = data

            time.sleep(app.sensor_read_interval)

    except Exception as e:
        _logger.error("DataSource sensor loop error: %s", e)
    finally:
        with app.lock:
            app.connected = False
            app.latest_uid = "?"


def _run_serial_loop(app, device_port):
    """Sensor loop using direct serial connection (legacy)."""
    ser = open_serial_connection(device_port)
    if ser is None:
        return

    with app.lock:
        app.ser = ser
        app.connected = True

    try:
        # Query UID and firmware once
        try:
            uid = read_uid(ser)
            device_info = read_device(ser)
            fw = device_info.get("FwVersion") if device_info else "?"
            app.latest_uid = uid
            app.latest_fw = fw
        except Exception as e:
            _logger.error("Failed to read device info via serial: %s", e)
            uid, fw = "?", "?"

        with app.lock:
            app.active_device["uid"] = uid

        # Main sensor loop
        while not app.stop_event.is_set():
            try:
                sensor_data = read_sensors(app.ser)
                with app.lock:
                    app.sensor_struct = sensor_data
                time.sleep(app.sensor_read_interval)
            except Exception as e:
                _logger.warning("Sensor read error: %s", e)
                try:
                    app.ser.close()
                except Exception:
                    pass
                time.sleep(1.0)
                break
    finally:
        with app.lock:
            app.ser = None
            app.connected = False
            app.latest_uid = "?"
            app.latest_fw = "?"


def stop_sensor_thread(app):
    """Stop the background sensor reading thread."""
    if app.worker_thread and app.worker_thread.is_alive():
        app.stop_event.set()
        app.worker_thread.join(timeout=0.5)
        app.stop_event.clear()

    app.ser = None
    app.connected = False
    app.sensor_struct = None