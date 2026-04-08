# benchlab/graph/app.py

import logging
import os
import threading
import time
from collections import deque
from dearpygui import dearpygui as dpg
from benchlab.graph import device, sensors, ui
from benchlab_pycore.core import translate_sensor_struct

_logger = logging.getLogger("benchlab.graph")

class GraphApp:
    def __init__(self, datasource=None):
        # Device + sensor state
        self.devices = []
        self.active_device = None
        self.ser = None
        self.sensor_struct = None
        self.connected = False
        self.datasource = datasource  # Optional DataSource instance

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
        self.selected_device = None  # Fixed: was missing this variable
        self.graph_x_axis = None
        self.graph_y_axis = None
        self.graph_line = None
        
        # Configuration
        self.sensor_read_interval = 1.0  # Configurable sensor reading interval
        self.graph_update_interval = 0.2  # Configurable graph update interval
        self.history_length = 50  # Configurable history length
        
        # Statistics
        self.session_stats = {"min": None, "max": None, "avg": None, "count": 0}
        
        # Logger (PERF-6.2)
        self._graph_logger = _logger

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
        from collections import deque

        # Use circular buffers for better performance
        x_data = deque(maxlen=self.history_length)
        y_data = deque(maxlen=self.history_length)
        t = 0

        # Debug: Ensure graph elements exist (PERF-6.2: use logger)
        if not self.graph_line or not dpg.does_item_exist(self.graph_line):
            self._graph_logger.debug("Graph line not ready: %s", self.graph_line)
            time.sleep(1)  # Avoid tightloop if graph not ready
            return

        # Attach to line_series user data
        user_data = dpg.get_item_user_data(self.graph_line)
        if user_data is None:
            user_data = {"x_data": [], "y_data": []}
            dpg.set_item_user_data(self.graph_line, user_data)

        current_sensor = self.selected_sensor
        current_device = self.selected_device

        self._graph_logger.debug("Starting graph loop for sensor: %s", current_sensor)

        while dpg.does_item_exist("##main_plot") and self.connected and self.graph_line:
            # Check if sensor/device changed
            if self.selected_sensor != current_sensor or self.selected_device != current_device:
                # Reset all data
                t = 0
                x_data.clear()
                y_data.clear()
                user_data["x_data"].clear()
                user_data["y_data"].clear()
                self.session_stats = {"min": None, "max": None, "avg": None, "count": 0}

                current_sensor = self.selected_sensor
                current_device = self.selected_device
                self._graph_logger.debug("Sensor changed to: %s", current_sensor)

            value = None
            with self.lock:
                if self.sensor_struct:
                    value = self.get_sensor_value(self.sensor_struct, self.selected_sensor)

            if value is not None:
                t += 1
                x_data.append(t)
                y_data.append(value)

                # Update session statistics
                self._update_session_stats(value)

                # Update series using deque data
                user_data["x_data"] = list(x_data)
                user_data["y_data"] = list(y_data)
                
                # Debug: Log data being sent to graph (PERF-6.2: rate-limited)
                if len(x_data) > 0 and t % 10 == 0:
                    self._graph_logger.debug("Graph data: x=%s, y=%s", list(x_data)[-3:], list(y_data)[-3:])
                
                dpg.set_value(self.graph_line, [list(x_data), list(y_data)])

                # Adjust Y axis dynamically
                if y_data:
                    min_y = min(y_data)
                    max_y = max(y_data)
                    avg_y = sum(y_data) / len(y_data)
                else:
                    min_y = max_y = avg_y = None

                margin = (max_y - min_y) * 0.1 if max_y != min_y else 1

                # Update axes with bounds checking (BUG-6.3: None-safe)
                if x_data and self.graph_x_axis is not None:
                    dpg.set_axis_limits(self.graph_x_axis, float(x_data[0]), float(x_data[-1]))
                if self.graph_y_axis is not None:
                    dpg.set_axis_limits(self.graph_y_axis, 
                                        min_y - margin if min_y is not None else 0, 
                                        max_y + margin if max_y is not None else 1)

                # Update the individual text items with session statistics
                session_min = self.session_stats["min"]
                session_max = self.session_stats["max"]
                session_avg = self.session_stats["avg"]
                
                if dpg.does_item_exist("graph_min"):
                    dpg.set_value("graph_min", f"Min: {session_min:.2f}" if session_min is not None else "Min: --")
                if dpg.does_item_exist("graph_max"):
                    dpg.set_value("graph_max", f"Max: {session_max:.2f}" if session_max is not None else "Max: --")
                if dpg.does_item_exist("graph_avg"):
                    dpg.set_value("graph_avg", f"Avg: {session_avg:.2f}" if session_avg is not None else "Avg: --")

            time.sleep(self.graph_update_interval)
    
    def _update_session_stats(self, value):
        """Update session-wide statistics for the selected sensor."""
        # Initialize history deque if not present
        if "history" not in self.session_stats:
            self.session_stats["history"] = deque(maxlen=1000)
        
        self.session_stats["history"].append(value)
        
        if self.session_stats["min"] is None or value < self.session_stats["min"]:
            self.session_stats["min"] = value
        if self.session_stats["max"] is None or value > self.session_stats["max"]:
            self.session_stats["max"] = value
        
        self.session_stats["count"] += 1
        # Calculate average from history deque
        if self.session_stats["history"]:
            self.session_stats["avg"] = sum(self.session_stats["history"]) / len(self.session_stats["history"])

    # -----------------------------
    # Main Run Loop
    # -----------------------------
    def run(self):
        """Create and show the GUI."""
        dpg.create_context()
        dpg.create_viewport(title="BENCHLAB Graph Interface", width=1000, height=700)

        # Build unified main window
        ui.build_unified_window(self)

        dpg.setup_dearpygui()
        dpg.show_viewport()

        # Start background threads AFTER GUI is set up
        # Sensor thread reads from serial/device
        self.start_sensor_thread()
        
        # Graph updater thread updates the plot
        self.graph_updater_thread = threading.Thread(target=self.update_graph_loop, daemon=True, name="GraphUpdater")
        self.graph_updater_thread.start()

        # GUI update loop
        try:
            while dpg.is_dearpygui_running():
                with self.lock:
                    status_text = f"{'Connected' if self.connected else 'Disconnected'}"
                    status_color = (0, 255, 0) if self.connected else (255, 0, 0)
                    
                    # Update device status
                    if dpg.does_item_exist("device_status"):
                        dpg.set_value("device_status", status_text)
                        dpg.configure_item("device_status", color=status_color)
                    
                    # Update device information
                    if dpg.does_item_exist("device_uid"):
                        dpg.set_value("device_uid", self.latest_uid if self.latest_uid != "?" else "Unknown")
                    if dpg.does_item_exist("device_fw"):
                        dpg.set_value("device_fw", self.latest_fw if self.latest_fw != "?" else "Unknown")

                    # Update current sensor values display
                    ui.update_current_values_display(self)

                    dpg.render_dearpygui_frame()
        finally:
            # Clean up threads on exit (QUAL-6.1: graceful shutdown)
            self.stop_event.set()
            self.stop_sensor_thread()
            if self.graph_updater_thread and self.graph_updater_thread.is_alive():
                self.graph_updater_thread.join(timeout=2)
            dpg.destroy_context()
