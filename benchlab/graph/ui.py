# benchlab/graph/ui.py

import time
import threading
from dearpygui import dearpygui as dpg
from benchlab.graph import sensors
from benchlab.core.sensor_translation import translate_sensor_struct

def build_device_window(app):
    """Create the main device selection window."""
    with dpg.window(label="BENCHLAB Devices", width=400, height=150):
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=[d["port"] for d in app.devices],
                default_value=app.devices[0]["port"] if app.devices else "<No devices>",
                callback=app.device_changed,
                tag="##device_combo"
            )
            dpg.add_button(
                label="Detect Devices",
                callback=lambda: app.detect_devices()
            )

        dpg.add_text("Device status: Disconnected", tag="device_status")
        dpg.add_text("Device UID: ", tag="device_uid")
        dpg.add_text("Firmware: ", tag="device_fw")
        dpg.add_button(
            label="Select Sensor to Graph",
            callback=lambda: show_sensor_selection(app)
        )

def show_sensor_selection(app):
    """Create a window for selecting which sensor to graph."""
    if dpg.does_item_exist("sensor_window"):
        dpg.delete_item("sensor_window")  # remove existing window

    available_sensors = sensors.get_available_sensors()

    with dpg.window(label="Select Sensor", tag="sensor_window", width=300, height=150, pos=(401, 0)):
        dpg.add_text("Select sensor to graph:")
        dpg.add_combo(
            items=available_sensors,
            default_value=available_sensors[0] if available_sensors else None,
            tag="sensor_combo"
        )
        dpg.add_button(
            label="Open Graph",
            callback=app.open_graph_window
        )

def open_graph_window(app, sender=None, app_data=None):
    """Create a single graph window for the selected sensor."""
    app.selected_sensor = dpg.get_value("sensor_combo")
    app.selected_device = dpg.get_value("device_uid")

    # Delete previous graph window if it exists
    if dpg.does_item_exist("graph_window"):
        dpg.delete_item("graph_window")

    with dpg.window(label=f"Graph: {app.selected_sensor}", tag="graph_window", width=701, height=400, pos=(0, 151)):
        dpg.add_text(f"Real-time graph for {app.selected_sensor} from {app.selected_device}")
        
        with dpg.group(horizontal=True):
            dpg.add_text("Min: --", tag="graph_min")
            dpg.add_text("Max: --", tag="graph_max")
            dpg.add_text("Avg: --", tag="graph_avg")        
        
        with dpg.plot(label="Sensor Data", height=-1, width=-1) as plot_id:
            dpg.add_plot_legend()
            x_axis = dpg.add_plot_axis(dpg.mvXAxis, label="Time")
            y_axis = dpg.add_plot_axis(dpg.mvYAxis, label=app.selected_sensor)
            line_series = dpg.add_line_series([], [], label=app.selected_sensor, parent=y_axis)

            # Attach user data for tooltip and updating
            dpg.set_item_user_data(line_series, {"x_data": [], "y_data": []})

    # Save IDs for updates
    app.graph_x_axis = x_axis
    app.graph_y_axis = y_axis
    app.graph_line = line_series

    # Reset graph data in app instance (flush old points)
    if hasattr(app, "graph_points"):
        app.graph_points.clear()  # optional if you store in app
    else:
        app.graph_points = []

    # Start or restart the updater thread
    if getattr(app, "graph_updater_thread", None) and app.graph_updater_thread.is_alive():
        # No need to start a new thread; the old one will pick up the new sensor
        pass
    else:
        app.graph_updater_thread = threading.Thread(target=app.update_graph_loop, daemon=True)
        app.graph_updater_thread.start()
