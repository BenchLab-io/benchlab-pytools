# benchlab/graph/device.py

import threading
import time
import logging
from dearpygui import dearpygui as dpg

_logger = logging.getLogger("benchlab.graph.device")


def detect_devices(app):
    """Discover devices via the datasource and update the combo box."""
    datasource = app.datasource
    if datasource is None:
        _logger.error("No datasource configured")
        return

    try:
        raw = datasource.list_devices()
        if isinstance(raw, dict):
            devices = [{"uid": uid, "port": info.get("port", "?"), **info}
                       for uid, info in raw.items()]
        else:
            devices = [{"uid": d.get("uid", "?"), "port": d.get("port", "?"), **d}
                       for d in raw]

        devices_sorted = sorted(devices, key=lambda d: d["port"])

        with app.lock:
            app.devices = devices_sorted
            if app.devices:
                app.active_device = app.devices[0]
                device_items = [d["port"] for d in app.devices]
                if dpg.does_item_exist("##device_combo"):
                    dpg.configure_item("##device_combo",
                                       items=device_items,
                                       default_value=device_items[0])
                app.start_sensor_thread()
            else:
                app.active_device = None
                if dpg.does_item_exist("##device_combo"):
                    dpg.configure_item("##device_combo",
                                       items=["<No devices>"],
                                       default_value="<No devices>")
    except Exception as e:
        _logger.error("Failed to detect devices: %s", e)


def device_changed(app, sender, app_data):
    """Callback when the user picks a different device from the combo box."""
    with app.lock:
        app.active_device = next(
            (d for d in app.devices if d["port"] == app_data), None
        )
    if app.active_device:
        threading.Thread(target=restart_sensor_thread, args=(app,), daemon=True).start()


def restart_sensor_thread(app):
    stop_sensor_thread(app)
    start_sensor_thread(app)


def start_sensor_thread(app):
    """Start the datasource polling loop in a background thread."""
    stop_sensor_thread(app)

    if app.datasource is None:
        _logger.error("Cannot start sensor thread: no datasource")
        return

    app.worker_thread = threading.Thread(
        target=_datasource_loop, args=(app,), daemon=True, name="GraphSensor"
    )
    app.worker_thread.start()


def _datasource_loop(app):
    """Poll DataSourceManager.snapshot() and push data into app.sensor_struct."""
    datasource = app.datasource
    try:
        # Resolve which device to poll
        raw = datasource.list_devices()
        if isinstance(raw, dict):
            device_list = [{"uid": uid, "port": info.get("port", "?"), **info}
                           for uid, info in raw.items()]
        else:
            device_list = list(raw)

        # Match by port if active_device already set, else take first
        selected_port = (app.active_device or {}).get("port")
        target = next((d for d in device_list if d.get("port") == selected_port), None)
        if target is None and device_list:
            target = device_list[0]

        if not target:
            _logger.warning("No devices found in datasource")
            return

        uid = target.get("uid", "?")

        with app.lock:
            app.connected = True
            app.latest_uid = uid
            if app.active_device:
                app.active_device["uid"] = uid
            else:
                app.active_device = {"port": target.get("port", "?"), "uid": uid}
                # Preserve the full device list in the combo — don't overwrite with one item
                all_ports = [d["port"] for d in app.devices] if app.devices else [app.active_device["port"]]
                if dpg.does_item_exist("##device_combo"):
                    dpg.configure_item("##device_combo",
                                       items=all_ports,
                                       default_value=app.active_device["port"])

        # Main poll loop
        while not app.stop_event.is_set():
            try:
                datasource.select_device(uid)
                snap = datasource.snapshot()
                data = (snap.get("sensor_data")
                        or snap.get("all_telemetry", {}).get(uid)
                        or {})
                if data:
                    with app.lock:
                        app.sensor_struct = data
            except Exception as e:
                _logger.warning("Poll error: %s", e)

            app.stop_event.wait(app.sensor_read_interval)

    except Exception as e:
        _logger.error("Datasource loop error: %s", e)
    finally:
        with app.lock:
            app.connected = False
            app.latest_uid = "?"


def stop_sensor_thread(app):
    """Stop the background sensor thread."""
    if app.worker_thread and app.worker_thread.is_alive():
        app.stop_event.set()
        app.worker_thread.join(timeout=2)
        app.stop_event.clear()
    app.connected = False
    app.sensor_struct = None