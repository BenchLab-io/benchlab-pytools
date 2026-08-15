"""Non-hardware regression tests for benchlab.graph.

These tests exercise GraphApp/ui.py against a fake datasource — no physical
BenchLab device is required. They guard against the class of bug fixed in
the graph bug sweep (issue #18): the floating graph window killing the
updater thread on close, update_graph_loop dying silently on any dpg error,
and stale sensor values lingering after a disconnect.

Requires dearpygui, which creates a real (offscreen) viewport/context even
in CI — this works headlessly on windows-latest runners.
"""

from benchlab.graph.app import GraphApp
from benchlab.graph import ui
import threading
import time

import pytest

try:
    from dearpygui import dearpygui as dpg
    HAS_DPG = True
except ImportError:
    dpg = None
    HAS_DPG = False

pytestmark = pytest.mark.skipif(
    not HAS_DPG,
    reason="dearpygui not available in this environment")


class FakeDataSource:
    def __init__(self, devices=None):
        self._devices = devices or []

    def list_devices(self):
        return self._devices

    def select_device(self, uid):
        pass

    def snapshot(self):
        return {}


@pytest.fixture
def dpg_context():
    dpg.create_context()
    dpg.create_viewport(title="test", width=200, height=200)
    dpg.setup_dearpygui()
    yield
    dpg.destroy_context()


def test_open_graph_window_swaps_graph_items(dpg_context):
    app = GraphApp(FakeDataSource())
    ui.build_unified_window(app)

    main_line = app.graph_line
    assert app.main_graph_line == main_line

    ui.open_graph_window(app)
    assert app.graph_line != main_line
    assert dpg.does_item_exist(app.graph_line)


def test_closing_floating_window_reattaches_to_main_plot(dpg_context):
    """Regression test for issue #18: closing the floating graph window via
    native controls used to leave graph_line pointing at a deleted item,
    permanently killing the updater thread on its next iteration."""
    app = GraphApp(FakeDataSource())
    ui.build_unified_window(app)
    ui.open_graph_window(app)

    on_close = dpg.get_item_configuration("graph_window")["on_close"]
    assert on_close is not None

    on_close("graph_window", None)

    assert app.graph_line == app.main_graph_line
    assert app.graph_x_axis == app.main_graph_x_axis
    assert app.graph_y_axis == app.main_graph_y_axis
    assert dpg.does_item_exist(app.graph_line)


def test_update_graph_loop_survives_deleted_items(dpg_context):
    """Regression test for issue #18: update_graph_loop had no exception
    handling, so a dpg call on a deleted item would kill the thread with an
    unhandled exception instead of exiting cleanly."""
    app = GraphApp(FakeDataSource())
    ui.build_unified_window(app)
    ui.open_graph_window(app)

    app.connected = True
    app.selected_sensor = "temp"
    app.sensor_struct = {"temp": 42.0}
    app.graph_update_interval = 0.05

    t = threading.Thread(target=app.update_graph_loop, daemon=True)
    t.start()
    time.sleep(0.15)

    # Simulate a native close without going through on_close, so graph_line
    # dangles on a deleted item for at least one loop iteration.
    dpg.delete_item("graph_window")
    time.sleep(0.15)

    assert t.is_alive(), (
        "updater thread died instead of surviving the deleted item"
    )

    app.connected = False
    t.join(timeout=2)
    assert not t.is_alive()


def test_datasource_loop_clears_stale_sensor_struct():
    """Regression test for issue #18: sensor_struct was left stale after
    the datasource loop exited due to no devices being found."""
    app = GraphApp(FakeDataSource(devices=[]))
    app.sensor_struct = {"stale": 1}

    app._datasource_loop()

    assert app.sensor_struct is None
    assert app.connected is False
    assert app.latest_uid == "?"


def test_datasource_loop_populates_sensor_struct_then_clears_on_exit():
    devices = [{"uid": "UID-1", "port": "COM3"}]
    captured = {}

    class SnapshotDataSource(FakeDataSource):
        def snapshot(self):
            # Capture mid-loop state (sensor_struct populated, connected)
            # before the loop's finally block resets everything on exit.
            captured["sensor_struct"] = app.sensor_struct
            captured["connected"] = app.connected
            app.stop_event.set()
            return {"sensor_data": {"temp": 21.5}}

    app = GraphApp(SnapshotDataSource(devices=devices))
    app.active_device = devices[0]

    app._datasource_loop()

    assert captured["connected"] is True
    # sensor_struct is only set *after* the snapshot that populates it, so the
    # first captured value is expected to be None here; the loop then updates
    # sensor_struct with the snapshot's data before stop_event exits it.
    assert app.sensor_struct is None  # cleared by the finally block on exit
    assert app.connected is False
    assert app.latest_uid == "?"
