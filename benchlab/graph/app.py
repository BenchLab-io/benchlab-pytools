# benchlab/graph/app.py

import logging
import threading
import time
from collections import deque

from dearpygui import dearpygui as dpg
from benchlab.graph import ui

_logger = logging.getLogger("benchlab.graph")


class GraphApp:
    def __init__(self, datasource):
        self.datasource = datasource

        # Device state
        self.devices = []
        self.active_device = None
        self.latest_uid = "?"
        self.latest_fw = "?"
        self.connected = False
        self.sensor_struct = None   # plain dict from datasource snapshot

        # Threads
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.graph_updater_thread = None

        # Graphing state
        self.selected_sensor = None
        self.selected_device = None
        self.graph_x_axis = None
        self.graph_y_axis = None
        self.graph_line = None

        # Configuration
        self.sensor_read_interval = 1.0
        self.graph_update_interval = 0.2
        self.history_length = 50

        # Statistics
        self.session_stats = {
            "min": None,
            "max": None,
            "avg": None,
            "count": 0}

    # ------------------------------------------------------------------
    # Device detection (called by Detect button in UI)
    # ------------------------------------------------------------------

    def detect_devices(self):
        """Re-query the datasource for devices and update the combo box."""
        try:
            raw = self.datasource.list_devices()
            devices = self._normalise_device_list(raw)
            devices_sorted = sorted(devices, key=lambda d: d["port"])
            with self.lock:
                self.devices = devices_sorted
                if self.devices:
                    self.active_device = self.devices[0]
                    items = [d["port"] for d in self.devices]
                    if dpg.does_item_exist("##device_combo"):
                        dpg.configure_item("##device_combo",
                                           items=items, default_value=items[0])
                    self.start_sensor_thread()
                else:
                    self.active_device = None
                    if dpg.does_item_exist("##device_combo"):
                        dpg.configure_item("##device_combo",
                                           items=["<No devices>"],
                                           default_value="<No devices>")
        except Exception as e:
            _logger.error("detect_devices error: %s", e)

    def device_changed(self, sender, app_data):
        """Combo box callback — switch active device."""
        with self.lock:
            self.active_device = next(
                (d for d in self.devices if d["port"] == app_data), None
            )
        if self.active_device:
            threading.Thread(target=self._restart_sensor_thread,
                             daemon=True).start()

    # ------------------------------------------------------------------
    # Sensor value helper
    # ------------------------------------------------------------------

    def get_sensor_value(self, sensor_struct, sensor_name):
        """Return value for sensor_name from a telemetry dict."""
        if not sensor_struct or not sensor_name:
            return None
        return sensor_struct.get(sensor_name)

    # ------------------------------------------------------------------
    # UI delegation
    # ------------------------------------------------------------------

    def open_graph_window(self, sender, app_data):
        ui.open_graph_window(self, sender, app_data)

    # ------------------------------------------------------------------
    # Sensor polling thread
    # ------------------------------------------------------------------

    def start_sensor_thread(self):
        self._stop_sensor_thread()
        self.worker_thread = threading.Thread(
            target=self._datasource_loop, daemon=True, name="GraphSensor"
        )
        self.worker_thread.start()

    def _restart_sensor_thread(self):
        self._stop_sensor_thread()
        self.start_sensor_thread()

    def _stop_sensor_thread(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.worker_thread.join(timeout=2)
            self.stop_event.clear()
        self.connected = False
        self.sensor_struct = None

    def _normalise_device_list(self, raw) -> list:
        if isinstance(raw, dict):
            return [{"uid": uid, "port": info.get("port", "?"), **info}
                    for uid, info in raw.items()]
        return [{"uid": d.get("uid", "?"), "port": d.get("port", "?"), **d}
                for d in raw]

    def _datasource_loop(self):
        """Poll DataSourceManager.snapshot() and update sensor_struct."""
        try:
            raw = self.datasource.list_devices()
            device_list = self._normalise_device_list(raw)

            selected_port = (self.active_device or {}).get("port")
            target = next((d for d in device_list
                           if d.get("port") == selected_port), None)
            if target is None and device_list:
                target = device_list[0]
            if not target:
                _logger.warning("No devices found in datasource")
                return

            uid = target.get("uid", "?")

            with self.lock:
                self.connected = True
                self.latest_uid = uid
                if self.active_device:
                    self.active_device["uid"] = uid
                else:
                    self.active_device = {
                        "port": target.get(
                            "port", "?"), "uid": uid}
                    all_ports = [
                        d["port"] for d in self.devices] if self.devices else [
                        self.active_device["port"]]
                    if dpg.does_item_exist("##device_combo"):
                        dpg.configure_item(
                            "##device_combo",
                            items=all_ports,
                            default_value=self.active_device["port"])

            while not self.stop_event.is_set():
                try:
                    self.datasource.select_device(uid)
                    snap = self.datasource.snapshot()
                    data = (snap.get("sensor_data")
                            or snap.get("all_telemetry", {}).get(uid)
                            or {})
                    if data:
                        with self.lock:
                            self.sensor_struct = data
                except Exception as e:
                    _logger.warning("Poll error: %s", e)
                self.stop_event.wait(self.sensor_read_interval)

        except Exception as e:
            _logger.error("Datasource loop error: %s", e)
        finally:
            with self.lock:
                self.connected = False
                self.latest_uid = "?"
                self.sensor_struct = None

    # ------------------------------------------------------------------
    # Graph update loop
    # ------------------------------------------------------------------

    def update_graph_loop(self):
        x_data = deque(maxlen=self.history_length)
        y_data = deque(maxlen=self.history_length)
        t = 0

        if not self.graph_line or not dpg.does_item_exist(self.graph_line):
            time.sleep(1)
            return

        user_data = dpg.get_item_user_data(self.graph_line) or {
            "x_data": [], "y_data": []}
        dpg.set_item_user_data(self.graph_line, user_data)

        current_sensor = self.selected_sensor
        current_device = self.selected_device

        while dpg.does_item_exist(
                "##main_plot") and self.connected and self.graph_line:
            try:
                if (self.selected_sensor != current_sensor
                        or self.selected_device != current_device):
                    t = 0
                    x_data.clear()
                    y_data.clear()
                    user_data["x_data"].clear()
                    user_data["y_data"].clear()
                    self.session_stats = {
                        "min": None,
                        "max": None,
                        "avg": None,
                        "count": 0,
                        "history": deque(
                            maxlen=1000)}
                    current_sensor = self.selected_sensor
                    current_device = self.selected_device

                value = None
                with self.lock:
                    if self.sensor_struct:
                        value = self.get_sensor_value(
                            self.sensor_struct, self.selected_sensor)

                if (value is not None and self.graph_line
                        and dpg.does_item_exist(self.graph_line)):
                    t += 1
                    x_data.append(t)
                    y_data.append(value)
                    self._update_session_stats(value)

                    user_data["x_data"] = list(x_data)
                    user_data["y_data"] = list(y_data)
                    dpg.set_value(
                        self.graph_line, [
                            list(x_data), list(y_data)])

                    min_y = min(y_data)
                    max_y = max(y_data)
                    margin = (max_y - min_y) * 0.1 if max_y != min_y else 1

                    if (x_data and self.graph_x_axis is not None
                            and dpg.does_item_exist(self.graph_x_axis)):
                        dpg.set_axis_limits(self.graph_x_axis, float(
                            x_data[0]), float(x_data[-1]))
                    if self.graph_y_axis is not None and dpg.does_item_exist(
                            self.graph_y_axis):
                        dpg.set_axis_limits(self.graph_y_axis,
                                            min_y - margin, max_y + margin)

                    s = self.session_stats
                    min_text = (
                        f"Min: {s['min']:.2f}"
                        if s["min"] is not None else "Min: --")
                    max_text = (
                        f"Max: {s['max']:.2f}"
                        if s["max"] is not None else "Max: --")
                    avg_text = (
                        f"Avg: {s['avg']:.2f}"
                        if s["avg"] is not None else "Avg: --")
                    for tag, text in (
                            ("graph_min", min_text),
                            ("graph_min_float", min_text),
                            ("graph_max", max_text),
                            ("graph_max_float", max_text),
                            ("graph_avg", avg_text),
                            ("graph_avg_float", avg_text)):
                        if dpg.does_item_exist(tag):
                            dpg.set_value(tag, text)
            except Exception as e:
                _logger.warning("Graph update error: %s", e)

            time.sleep(self.graph_update_interval)

    def _update_session_stats(self, value):
        if not isinstance(self.session_stats.get("history"), deque):
            self.session_stats["history"] = deque(maxlen=1000)
        self.session_stats["history"].append(value)
        s = self.session_stats
        s["min"] = value if s["min"] is None else min(s["min"], value)
        s["max"] = value if s["max"] is None else max(s["max"], value)
        s["count"] += 1
        s["avg"] = sum(s["history"]) / len(s["history"])

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self):
        dpg.create_context()
        dpg.create_viewport(
            title="BENCHLAB Graph Interface",
            width=1000,
            height=700)
        ui.build_unified_window(self)
        dpg.setup_dearpygui()
        dpg.show_viewport()

        self.start_sensor_thread()
        self.graph_updater_thread = threading.Thread(
            target=self.update_graph_loop, daemon=True, name="GraphUpdater"
        )
        self.graph_updater_thread.start()

        _sensors_populated = False
        try:
            while dpg.is_dearpygui_running():
                with self.lock:
                    status_text = (
                        "Connected" if self.connected else "Disconnected")
                    status_color = (
                        0, 255, 0) if self.connected else (
                        255, 0, 0)
                    if dpg.does_item_exist("device_status"):
                        dpg.set_value("device_status", status_text)
                        dpg.configure_item("device_status", color=status_color)
                    if dpg.does_item_exist("device_uid"):
                        dpg.set_value(
                            "device_uid",
                            self.latest_uid
                            if self.latest_uid != "?" else "Unknown")

                    # Populate sensor combo once first data arrives
                    if not _sensors_populated and self.sensor_struct:
                        keys = [
                            k for k in self.sensor_struct
                            if k.lower() != "timestamp"]
                        if keys and dpg.does_item_exist("##sensor_combo"):
                            dpg.configure_item(
                                "##sensor_combo", items=keys,
                                default_value=keys[0])
                            _sensors_populated = True

                    ui.update_current_values_display(self)
                    dpg.render_dearpygui_frame()
        finally:
            self.stop_event.set()
            self._stop_sensor_thread()
            if (self.graph_updater_thread
                    and self.graph_updater_thread.is_alive()):
                self.graph_updater_thread.join(timeout=2)
            dpg.destroy_context()
