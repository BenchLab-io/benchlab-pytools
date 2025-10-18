# benchlab/graph/device.py

import threading
import time
from dearpygui import dearpygui as dpg
from benchlab.core.serial_io import (
    open_serial_connection,
    read_sensors,
    get_benchlab_ports,
    read_uid,
    read_device,
)

def detect_devices(app):
    """Scan for devices (no port opening) and update combo box."""
    ports = get_benchlab_ports()  # [{"port": "COM17"}, {"port": "COM18"}]
    devices = [{"port": p["port"], "uid": "?", "firmware": "?"} for p in ports]
    devices_sorted = sorted(devices, key=lambda d: d["port"])

    with app.lock:
        app.devices = devices_sorted
        if app.devices:
            app.active_device = app.devices[0]
            device_items = [d["port"] for d in app.devices]
            dpg.configure_item("##device_combo", items=device_items, default_value=device_items[0])
            app.start_sensor_thread()
        else:
            app.active_device = None
            dpg.configure_item("##device_combo", items=["<No devices>"], default_value="<No devices>")

def device_changed(app, sender, app_data):
    """Callback when user selects a different device from combo box."""
    print(f"[DEBUG] Combo selected: {app_data}")

    with app.lock:
        app.active_device = next((d for d in app.devices if d["port"] == app_data), None)

    if app.active_device:
        print(f"[DEBUG] Active device now: {app.active_device}")
        threading.Thread(target=app.restart_sensor_thread, daemon=True).start()
    else:
        print(f"[ERROR] Selected device not found in app.devices: {app_data}")

def restart_sensor_thread(app):
    """Stop current sensor thread and start a new one without freezing GUI."""
    app.stop_sensor_thread()
    app.start_sensor_thread()

def start_sensor_thread(app):
    """Start reading sensors from the active device in a separate thread."""
    print(f"[DEBUG] Starting sensor thread for {app.active_device}")

    app.stop_sensor_thread()
    if not app.active_device:
        return

    def sensor_loop():
        with app.lock:
            device = app.active_device.copy()
        device_port = device["port"]
        print(f"[DEBUG] Opening serial port {device_port}")

        ser = open_serial_connection(device_port)
        if ser is None:
            print(f"[ERROR] Failed to open serial port {device_port}")
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
                print(f"[DEBUG] UID={uid}, FW={fw}")
            except Exception as e:
                print(f"[ERROR] Failed to read device info: {e}")
                uid, fw = "?", "?"

            with app.lock:
                app.active_device["uid"] = uid
                app.active_device["firmware"] = fw
                app.latest_uid = uid
                app.latest_fw = fw

            # Main sensor loop
            while not app.stop_event.is_set():
                try:
                    sensor_data = read_sensors(app.ser)
                    with app.lock:
                        app.sensor_struct = sensor_data
                    time.sleep(1.0)
                except Exception as e:
                    print(f"[ERROR] Sensor read error: {e}")
                    break
        finally:
            with app.lock:
                app.ser = None
                app.connected = False
                app.latest_uid = "?"
                app.latest_fw = "?"

    app.worker_thread = threading.Thread(target=sensor_loop, daemon=True)
    app.worker_thread.start()

def stop_sensor_thread(app):
    """Stop the background sensor reading thread."""
    if app.worker_thread and app.worker_thread.is_alive():
        app.stop_event.set()
        app.worker_thread.join(timeout=0.5)  # avoid GUI freeze
        app.stop_event.clear()

    app.ser = None
    app.connected = False
    app.sensor_struct = None
