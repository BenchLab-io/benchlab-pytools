# benchlab/graph/ui.py

import threading
from collections import deque
from dearpygui import dearpygui as dpg


def build_unified_window(app):
    """Build the main two-column window."""
    with dpg.window(label="BENCHLAB Graph Interface",
                    width=950, height=700, tag="##main_window"):
        with dpg.group(horizontal=True):

            # ── Left column: controls ────────────────────────────────
            with dpg.child_window(label="Controls", width=400, border=True):

                # Device Management
                dpg.add_text("Device Management", color=(200, 200, 200))
                dpg.add_separator()
                with dpg.group(horizontal=True):
                    dpg.add_text("Port:", color=(180, 180, 180))
                    dpg.add_spacer(width=4)
                    dpg.add_combo(
                        items=[
                            d["port"] for d in app.devices],
                        default_value=(
                            app.devices[0]["port"]
                            if app.devices else "<No devices>"
                        ),
                        callback=app.device_changed,
                        tag="##device_combo",
                        width=180,
                    )
                    dpg.add_button(label="Detect",
                                   callback=lambda: app.detect_devices(),
                                   tag="##detect_button", width=80)
                with dpg.group(horizontal=True):
                    dpg.add_text("Status:", color=(180, 180, 180))
                    dpg.add_spacer(width=4)
                    dpg.add_text(
                        "Disconnected",
                        tag="device_status",
                        color=(
                            255,
                            0,
                            0))
                    dpg.add_spacer(width=8)
                    dpg.add_text("UID:", color=(180, 180, 180))
                    dpg.add_text(
                        "Unknown", tag="device_uid", color=(
                            200, 200, 200))

                dpg.add_separator()

                # Configuration
                dpg.add_text("Configuration", color=(200, 200, 200))
                with dpg.group(horizontal=True):
                    dpg.add_text("History:", color=(180, 180, 180))
                    dpg.add_spacer(width=8)
                    dpg.add_input_int(
                        default_value=app.history_length,
                        min_value=10,
                        max_value=1000,
                        callback=lambda s,
                        v: setattr(
                            app,
                            "history_length",
                            v),
                        tag="##history_length",
                        width=120,
                    )
                with dpg.group(horizontal=True):
                    dpg.add_text("Graph Update:", color=(180, 180, 180))
                    dpg.add_spacer(width=4)
                    dpg.add_input_float(
                        default_value=app.graph_update_interval,
                        min_value=0.05,
                        max_value=5.0,
                        callback=lambda s,
                        v: setattr(
                            app,
                            "graph_update_interval",
                            v),
                        tag="##graph_update_interval",
                        width=120,
                    )
                with dpg.group(horizontal=True):
                    dpg.add_text("Sensor Read:", color=(180, 180, 180))
                    dpg.add_spacer(width=4)
                    dpg.add_input_float(
                        default_value=app.sensor_read_interval,
                        min_value=0.1,
                        max_value=10.0,
                        callback=lambda s,
                        v: setattr(
                            app,
                            "sensor_read_interval",
                            v),
                        tag="##sensor_read_interval",
                        width=120,
                    )

                dpg.add_separator()

                # Sensor Selection
                dpg.add_text("Sensor Selection", color=(200, 200, 200))
                with dpg.group(horizontal=True):
                    dpg.add_text("Sensor:", color=(180, 180, 180))
                    dpg.add_spacer(width=4)
                    dpg.add_combo(
                        items=[],
                        default_value="",
                        tag="##sensor_combo",
                        width=180,
                        callback=lambda s, v: _update_sensor_selection(app),
                    )
                dpg.add_button(label="Start Graph",
                               callback=lambda: start_graph(app),
                               tag="##start_graph_button", width=180)

                dpg.add_separator()

                # Current Values
                dpg.add_text("Current Values", color=(200, 200, 200))
                with dpg.group(horizontal=True):
                    dpg.add_text("Current:", color=(180, 180, 180))
                    dpg.add_spacer(width=4)
                    dpg.add_text("--", tag="current_value")
                    dpg.add_spacer(width=8)
                    dpg.add_text("Min:", color=(180, 180, 180))
                    dpg.add_text("--", tag="current_min")
                with dpg.group(horizontal=True):
                    dpg.add_text("Max:", color=(180, 180, 180))
                    dpg.add_spacer(width=12)
                    dpg.add_text("--", tag="current_max")
                    dpg.add_spacer(width=8)
                    dpg.add_text("Avg:", color=(180, 180, 180))
                    dpg.add_text("--", tag="current_avg")
                dpg.add_button(label="Reset Stats",
                               callback=lambda: _reset_session_stats(app),
                               tag="##reset_stats_button", width=180)

            # ── Right column: graph ──────────────────────────────────
            with dpg.child_window(label="Graph", border=True):
                dpg.add_text("Live Graph", color=(200, 200, 200))
                dpg.add_separator()
                with dpg.group(horizontal=True):
                    dpg.add_text("Sensor:", color=(180, 180, 180))
                    dpg.add_text("None", tag="selected_sensor_name")
                    dpg.add_spacer(width=8)
                    dpg.add_text("Device:", color=(180, 180, 180))
                    dpg.add_text("None", tag="selected_device_name")
                with dpg.group(horizontal=True):
                    dpg.add_text("Session Stats:", color=(180, 180, 180))
                    dpg.add_text("Min: --", tag="graph_min")
                    dpg.add_text("Max: --", tag="graph_max")
                    dpg.add_text("Avg: --", tag="graph_avg")
                dpg.add_separator()
                with dpg.plot(
                    label="Sensor Data", height=-1, width=-1,
                    tag="##main_plot",
                ):
                    dpg.add_plot_legend()
                    app.graph_x_axis = dpg.add_plot_axis(
                        dpg.mvXAxis, label="Time")
                    app.graph_y_axis = dpg.add_plot_axis(
                        dpg.mvYAxis, label="Value")
                    app.graph_line = dpg.add_line_series(
                        [], [], label="Sensor Data", parent=app.graph_y_axis
                    )
                    dpg.set_item_user_data(
                        app.graph_line, {
                            "x_data": [], "y_data": []})

    # Remember the main window's graph items so the floating window can
    # hand control back to them when it is closed.
    app.main_graph_x_axis = app.graph_x_axis
    app.main_graph_y_axis = app.graph_y_axis
    app.main_graph_line = app.graph_line


def _reset_session_stats(app):
    app.session_stats = {"min": None, "max": None, "avg": None, "count": 0,
                         "history": deque(maxlen=1000)}
    for tag, val in [
        ("graph_min", "Min: --"),
        ("graph_max", "Max: --"),
        ("graph_avg", "Avg: --"),
        ("graph_min_float", "Min: --"),
        ("graph_max_float", "Max: --"),
        ("graph_avg_float", "Avg: --"),
    ]:
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, val)


def _update_sensor_selection(app):
    app.selected_sensor = dpg.get_value("##sensor_combo")
    app.selected_device = app.latest_uid if app.latest_uid != "?" else None
    if dpg.does_item_exist("selected_sensor_name"):
        dpg.set_value("selected_sensor_name", app.selected_sensor or "None")
    if dpg.does_item_exist("selected_device_name"):
        dpg.set_value("selected_device_name", app.selected_device or "None")


def start_graph(app):
    """Start graphing the selected sensor."""
    if not app.selected_sensor:
        app.selected_sensor = dpg.get_value("##sensor_combo")
    if not app.selected_device:
        app.selected_device = app.latest_uid if app.latest_uid != "?" else None
    _reset_session_stats(app)
    if not (getattr(app, "graph_updater_thread", None)
            and app.graph_updater_thread.is_alive()):
        app.graph_updater_thread = threading.Thread(
            target=app.update_graph_loop, daemon=True
        )
        app.graph_updater_thread.start()


def update_current_values_display(app):
    """Update the live value display. Called from the GUI render loop."""
    if not app.selected_sensor or not app.sensor_struct:
        return
    value = app.get_sensor_value(app.sensor_struct, app.selected_sensor)
    if value is None:
        return
    if dpg.does_item_exist("current_value"):
        dpg.set_value("current_value", f"{value:.2f}")
    h = app.session_stats.get("history")
    if h:
        if dpg.does_item_exist("current_min"):
            dpg.set_value("current_min", f"{min(h):.2f}")
        if dpg.does_item_exist("current_max"):
            dpg.set_value("current_max", f"{max(h):.2f}")
        if dpg.does_item_exist("current_avg"):
            dpg.set_value("current_avg", f"{sum(h) / len(h):.2f}")


def open_graph_window(app, sender=None, app_data=None):
    """Open a floating graph window for the selected sensor."""
    app.selected_sensor = dpg.get_value("##sensor_combo")
    app.selected_device = app.latest_uid if app.latest_uid != "?" else None

    if dpg.does_item_exist("graph_window"):
        dpg.delete_item("graph_window")

    def _on_close(sender, app_data):
        if dpg.does_item_exist(app.main_graph_line):
            app.graph_x_axis = app.main_graph_x_axis
            app.graph_y_axis = app.main_graph_y_axis
            app.graph_line = app.main_graph_line
        else:
            app.graph_x_axis = None
            app.graph_y_axis = None
            app.graph_line = None

    with dpg.window(label=f"Graph: {app.selected_sensor}",
                    tag="graph_window", width=701, height=400, pos=(0, 151),
                    on_close=_on_close):
        dpg.add_text(
            f"Real-time: {app.selected_sensor} — {app.selected_device}")
        with dpg.group(horizontal=True):
            dpg.add_text("Min: --", tag="graph_min_float")
            dpg.add_text("Max: --", tag="graph_max_float")
            dpg.add_text("Avg: --", tag="graph_avg_float")
            dpg.add_button(label="Reset Stats",
                           callback=lambda: _reset_session_stats(app),
                           tag="##reset_stats")
        with dpg.plot(label="Sensor Data", height=-1, width=-1):
            dpg.add_plot_legend()
            x_axis = dpg.add_plot_axis(dpg.mvXAxis, label="Time")
            y_axis = dpg.add_plot_axis(dpg.mvYAxis, label=app.selected_sensor)
            line_series = dpg.add_line_series(
                [], [], label=app.selected_sensor, parent=y_axis
            )
            dpg.set_item_user_data(line_series, {"x_data": [], "y_data": []})

    app.graph_x_axis = x_axis
    app.graph_y_axis = y_axis
    app.graph_line = line_series

    if not (getattr(app, "graph_updater_thread", None)
            and app.graph_updater_thread.is_alive()):
        app.graph_updater_thread = threading.Thread(
            target=app.update_graph_loop, daemon=True
        )
        app.graph_updater_thread.start()
