# benchlab/graph/app.py

import threading
import time
from dearpygui import dearpygui as dpg
from benchlab.graph import device, sensors, ui
from benchlab.core.sensor_translation import translate_sensor_struct

class GraphApp:
    def __init__(self):
        # Device + sensor state
        self.devices = []
        self.active_device = None
        self.ser = None
        self.sensor_struct = None
        self.connected = False

        # Metadata
        self.latest_uid = "?"
        self.latest_fw = "?"

        # Threads + synchronization
        self.lock = threading.Lock()
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.graph_updater_thread = None

        # Graphing state
        self.selected_sensor = None
        self.graph_x_axis = None
        self.graph_y_axis = None
        self.graph_line = None

    # -----------------------------
    # Device Management
    # -----------------------------
    def detect_devices(self):
        """Scan for devices and update combo box."""
        device.detect_devices(self)

    def device_changed(self, sender, app_data):
        """Callback when user selects a different device from combo box."""
        device.device_changed(self, sender, app_data)

    def start_sensor_thread(self):
        device.start_sensor_thread(self)

    def stop_sensor_thread(self):
        device.stop_sensor_thread(self)

    def restart_sensor_thread(self):
        device.restart_sensor_thread(self)

    # -----------------------------
    # Sensor Logic
    # -----------------------------
    def get_sensor_value(self, sensor_struct, sensor_name):
        return sensors.get_sensor_value(sensor_struct, sensor_name)

    # -----------------------------
    # UI Windows
    # -----------------------------
    def show_sensor_selection(self):
        ui.show_sensor_selection(self)

    def open_graph_window(self, sender, app_data):
        ui.open_graph_window(self, sender, app_data)

    # -----------------------------
    # Graph Update Loop
    # -----------------------------
    def update_graph_loop(self):
        import time

        t = 0
        x_data, y_data = [], []

        # Attach to line_series user data
        user_data = dpg.get_item_user_data(self.graph_line)
        if user_data is None:
            user_data = {"x_data": [], "y_data": []}
            dpg.set_item_user_data(self.graph_line, user_data)

        current_sensor = self.selected_sensor
        current_device = self.selected_device

        while dpg.does_item_exist("graph_window") and self.connected:
            # Skip loop if graph line not ready
            if not self.graph_line or not dpg.does_item_exist(self.graph_line):
                time.sleep(0.1)
                continue

            # Check if sensor/device changed
            if self.selected_sensor != current_sensor or self.selected_device != current_device:
                # Reset all data
                t = 0
                x_data.clear()
                y_data.clear()
                user_data["x_data"].clear()
                user_data["y_data"].clear()

                current_sensor = self.selected_sensor
                current_device = self.selected_device

            value = None
            with self.lock:
                if self.sensor_struct:
                    value = self.get_sensor_value(self.sensor_struct, self.selected_sensor)

            if value is not None:
                t += 1
                x_data.append(t)
                y_data.append(value)

                # Keep last N points
                N = 50
                x_data = x_data[-N:]
                y_data = y_data[-N:]

                # Update series
                user_data["x_data"] = x_data
                user_data["y_data"] = y_data
                dpg.set_value(self.graph_line, [x_data, y_data])

                # Adjust Y axis dynamically
                if y_data:
                    min_y = min(y_data)
                    max_y = max(y_data)
                    avg_y = sum(y_data) / len(y_data)
                else:
                    min_y = max_y = avg_y = None

                margin = (max_y - min_y) * 0.1 if max_y != min_y else 1

                dpg.set_axis_limits(self.graph_y_axis, min_y - margin, max_y + margin)
                dpg.set_axis_limits(self.graph_x_axis, x_data[0], x_data[-1])

                # Update the individual text items
                if dpg.does_item_exist("graph_min"):
                    dpg.set_value("graph_min", f"Min: {min_y:.2f}")
                if dpg.does_item_exist("graph_max"):
                    dpg.set_value("graph_max", f"Max: {max_y:.2f}")
                if dpg.does_item_exist("graph_avg"):
                    dpg.set_value("graph_avg", f"Avg: {avg_y:.2f}")

            time.sleep(0.2)

    # -----------------------------
    # Main Run Loop
    # -----------------------------
    def run(self):
        """Create and show the GUI."""
        dpg.create_context()
        dpg.create_viewport(title="BENCHLAB Graphs", width=718, height=588)

        # Build main device selection window
        ui.build_device_window(self)

        dpg.setup_dearpygui()
        dpg.show_viewport()

        # GUI update loop
        while dpg.is_dearpygui_running():
            with self.lock:
                status_text = f"Device status: {'Connected' if self.connected else 'Disconnected'}"
                dpg.set_value("device_status", status_text)
                dpg.set_value("device_uid", f"Device UID: {self.latest_uid}")
                dpg.set_value("device_fw", f"Firmware: {self.latest_fw}")

            dpg.render_dearpygui_frame()
