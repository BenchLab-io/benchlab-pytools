# benchlab/graph/ui.py

import time
import threading
from dearpygui import dearpygui as dpg
from benchlab.graph import sensors
from benchlab_pycore.core import translate_sensor_struct

def build_unified_window(app):
    """Create the unified main window with two-column layout."""
    with dpg.window(label="BENCHLAB Graph Interface", width=950, height=700, tag="##main_window"):
        # Two-column layout
        with dpg.group(horizontal=True):
            # Left Column: Controls and Configuration (40%)
            with dpg.child_window(label="Controls", width=400, border=True):
                # Device Management Section
                dpg.add_text("Device Management", color=(200, 200, 200))
                dpg.add_separator()
                
                # Grid-based layout for consistent alignment
                with dpg.group():
                    # Row 1: Port and Detect
                    with dpg.group(horizontal=True):
                        dpg.add_text("Port:", color=(180, 180, 180))
                        dpg.add_spacing(count=2)  # Add spacing for alignment
                        dpg.add_combo(
                            items=[d["port"] for d in app.devices],
                            default_value=app.devices[0]["port"] if app.devices else "<No devices>",
                            callback=app.device_changed,
                            tag="##device_combo",
                            width=180
                        )
                        dpg.add_button(
                            label="Detect",
                            callback=lambda: app.detect_devices(),
                            tag="##detect_button",
                            width=80
                        )
                    
                    # Row 2: Status with color indicator
                    with dpg.group(horizontal=True):
                        dpg.add_text("Status:", color=(180, 180, 180))
                        dpg.add_spacing(count=2)  # Add spacing for alignment
                        dpg.add_text("Disconnected", tag="device_status", color=(255, 0, 0))
                        dpg.add_spacing(count=4)  # Add spacing for alignment
                        dpg.add_text("UID:", color=(180, 180, 180))
                        dpg.add_text("Unknown", tag="device_uid", color=(200, 200, 200))
                
                dpg.add_separator()
                
                # Configuration Section
                dpg.add_text("Configuration", color=(200, 200, 200))
                
                # Grid-based configuration layout
                with dpg.group():
                    # Row 1: History
                    with dpg.group(horizontal=True):
                        dpg.add_text("History:", color=(180, 180, 180))
                        dpg.add_spacing(count=4)  # Add spacing for alignment
                        dpg.add_input_int(
                            default_value=app.history_length,
                            min_value=10,
                            max_value=1000,
                            callback=lambda sender, app_data: setattr(app, 'history_length', app_data),
                            tag="##history_length",
                            width=120
                        )
                    
                    # Row 2: Graph Update
                    with dpg.group(horizontal=True):
                        dpg.add_text("Graph Update:", color=(180, 180, 180))
                        dpg.add_spacing(count=2)  # Add spacing for alignment
                        dpg.add_input_float(
                            default_value=app.graph_update_interval,
                            min_value=0.05,
                            max_value=5.0,
                            callback=lambda sender, app_data: setattr(app, 'graph_update_interval', app_data),
                            tag="##graph_update_interval",
                            width=120
                        )
                    
                    # Row 3: Sensor Read
                    with dpg.group(horizontal=True):
                        dpg.add_text("Sensor Read:", color=(180, 180, 180))
                        dpg.add_spacing(count=2)  # Add spacing for alignment
                        dpg.add_input_float(
                            default_value=app.sensor_read_interval,
                            min_value=0.1,
                            max_value=10.0,
                            callback=lambda sender, app_data: setattr(app, 'sensor_read_interval', app_data),
                            tag="##sensor_read_interval",
                            width=120
                        )
                
                dpg.add_separator()
                
                # Sensor Selection Section
                dpg.add_text("Sensor Selection", color=(200, 200, 200))
                
                # Grid-based sensor selection layout
                with dpg.group():
                    # Row 1: Sensor dropdown
                    with dpg.group(horizontal=True):
                        dpg.add_text("Sensor:", color=(180, 180, 180))
                        dpg.add_spacing(count=4)  # Add spacing for alignment
                        available_sensors = sensors.get_available_sensors()
                        dpg.add_combo(
                            items=available_sensors,
                            default_value=available_sensors[0] if available_sensors else None,
                            tag="##sensor_combo",
                            width=180,
                            callback=lambda sender, app_data: update_sensor_selection(app)
                        )
                    
                    # Row 2: Start Graph button
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Start Graph",
                            callback=lambda: start_graph(app),
                            tag="##start_graph_button",
                            width=180
                        )
                
                dpg.add_separator()
                
                # Current Values Section
                dpg.add_text("Current Values", color=(200, 200, 200))
                
                # Grid-based current values layout
                with dpg.group():
                    # Row 1: Current and Min
                    with dpg.group(horizontal=True):
                        dpg.add_text("Current:", color=(180, 180, 180))
                        dpg.add_spacing(count=2)  # Add spacing for alignment
                        dpg.add_text("--", tag="current_value")
                        dpg.add_spacing(count=4)  # Add spacing for alignment
                        dpg.add_text("Min:", color=(180, 180, 180))
                        dpg.add_text("--", tag="current_min")
                    
                    # Row 2: Max and Avg
                    with dpg.group(horizontal=True):
                        dpg.add_text("Max:", color=(180, 180, 180))
                        dpg.add_spacing(count=6)  # Add spacing for alignment
                        dpg.add_text("--", tag="current_max")
                        dpg.add_spacing(count=4)  # Add spacing for alignment
                        dpg.add_text("Avg:", color=(180, 180, 180))
                        dpg.add_text("--", tag="current_avg")
                    
                    # Row 3: Reset Stats button
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Reset Stats",
                            callback=lambda: reset_session_stats(app),
                            tag="##reset_stats_button",
                            width=180
                        )

            # Right Column: Graph Area (60%)
            with dpg.child_window(label="Graph", border=True):
                dpg.add_text("Live Graph", color=(200, 200, 200))
                dpg.add_separator()
                
                with dpg.group(horizontal=True):
                    dpg.add_text("Sensor:", color=(180, 180, 180))
                    dpg.add_text("None", tag="selected_sensor_name")
                    dpg.add_text("Device:", color=(180, 180, 180))
                    dpg.add_text("None", tag="selected_device_name")
                
                with dpg.group(horizontal=True):
                    dpg.add_text("Session Stats:", color=(180, 180, 180))
                    dpg.add_text("Min: --", tag="graph_min")
                    dpg.add_text("Max: --", tag="graph_max")
                    dpg.add_text("Avg: --", tag="graph_avg")
                
                dpg.add_separator()
                
                with dpg.plot(label="Sensor Data", height=-1, width=-1, tag="##main_plot"):
                    dpg.add_plot_legend()
                    app.graph_x_axis = dpg.add_plot_axis(dpg.mvXAxis, label="Time")
                    app.graph_y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Value")
                    app.graph_line = dpg.add_line_series([], [], label="Sensor Data", parent=app.graph_y_axis)
                    
                    # Attach user data for tooltip and updating
                    dpg.set_item_user_data(app.graph_line, {"x_data": [], "y_data": []})

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

def reset_session_stats(app):
    """Reset session statistics for the current sensor."""
    app.session_stats = {"min": None, "max": None, "avg": None, "count": 0, "history": []}
    if dpg.does_item_exist("graph_min"):
        dpg.set_value("graph_min", "Min: --")
    if dpg.does_item_exist("graph_max"):
        dpg.set_value("graph_max", "Max: --")
    if dpg.does_item_exist("graph_avg"):
        dpg.set_value("graph_avg", "Avg: --")

def update_sensor_selection(app):
    """Update the current sensor selection and display current values."""
    app.selected_sensor = dpg.get_value("##sensor_combo")
    app.selected_device = dpg.get_value("device_uid")
    
    # Update labels
    if dpg.does_item_exist("selected_sensor_name"):
        dpg.set_value("selected_sensor_name", app.selected_sensor if app.selected_sensor else "None")
    if dpg.does_item_exist("selected_device_name"):
        dpg.set_value("selected_device_name", app.selected_device if app.selected_device else "None")

def start_graph(app):
    """Start the graphing process for the selected sensor."""
    if not app.selected_sensor:
        app.selected_sensor = dpg.get_value("##sensor_combo")
    if not app.selected_device:
        app.selected_device = dpg.get_value("device_uid")
    
    # Reset session statistics
    reset_session_stats(app)
    
    # Start the graph updater thread if not already running
    if not getattr(app, "graph_updater_thread", None) or not app.graph_updater_thread.is_alive():
        app.graph_updater_thread = threading.Thread(target=app.update_graph_loop, daemon=True)
        app.graph_updater_thread.start()

def update_current_values_display(app):
    """Update the current sensor values display in the middle section."""
    if not app.selected_sensor or not app.sensor_struct:
        return
    
    value = app.get_sensor_value(app.sensor_struct, app.selected_sensor)
    if value is not None:
        # Update current value
        if dpg.does_item_exist("current_value"):
            dpg.set_value("current_value", f"{value:.2f}")
        
        # Calculate current session stats for display
        if "history" in app.session_stats and app.session_stats["history"]:
            current_min = min(app.session_stats["history"])
            current_max = max(app.session_stats["history"])
            current_avg = sum(app.session_stats["history"]) / len(app.session_stats["history"])
            
            if dpg.does_item_exist("current_min"):
                dpg.set_value("current_min", f"{current_min:.2f}")
            if dpg.does_item_exist("current_max"):
                dpg.set_value("current_max", f"{current_max:.2f}")
            if dpg.does_item_exist("current_avg"):
                dpg.set_value("current_avg", f"{current_avg:.2f}")

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
            dpg.add_button(
                label="Reset Stats",
                callback=lambda: reset_session_stats(app),
                tag="##reset_stats"
            )
        
        with dpg.group(horizontal=True):
            dpg.add_text("Session Statistics (since last reset):", color=(0, 255, 255))
        
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
