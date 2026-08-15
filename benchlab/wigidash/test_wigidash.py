"""Non-hardware regression tests for benchlab.wigidash.

These tests exercise the pure-Python logic bugs fixed in the wigidash bug
sweep (issue #22) with fakes/mocks — no physical WigiDash USB device or
BenchLab hardware is required:
- WigidashManager.get_available_benchlabs pruning stale/disconnected ports
- WigidashManager.start_telemetry's lock preventing duplicate telemetry
  contexts/threads when called concurrently for the same port
- telemetry_step not mutating the caller's input dict in place
- The touch debounce threshold in benchlab_overview/benchlab_graph/benchlab_fleet
"""

import threading
import time

from benchlab.wigidash.wigidash_manager import WigidashManager
from benchlab.wigidash.benchlab_telemetry import telemetry_step, TelemetryContext, TelemetryHistory


class FakeDataSource:
    def __init__(self, fleets):
        self._fleets = list(fleets)
        self._selected = None

    def list_devices(self):
        return self._fleets.pop(0) if self._fleets else []

    def select_device(self, uid):
        self._selected = uid

    def snapshot(self):
        return {"device_info": {}, "sensor_data": {}}


def test_get_available_benchlabs_prunes_stale_port():
    device = {"port": "COM3", "uid": "UID-1", "firmware": "1.0"}
    ds = FakeDataSource([[device], []])
    mgr = WigidashManager(datasource=ds)

    mgr.get_available_benchlabs(log_info=False)
    assert "COM3" in mgr.benchlab_devices

    mgr.get_available_benchlabs(log_info=False)
    assert "COM3" not in mgr.benchlab_devices


def test_get_available_benchlabs_keeps_devices_on_query_failure():
    """A transient list_devices() failure must not wipe out known devices."""
    device = {"port": "COM3", "uid": "UID-1", "firmware": "1.0"}

    class FlakyDataSource(FakeDataSource):
        def __init__(self):
            super().__init__([[device]])
            self._calls = 0

        def list_devices(self):
            self._calls += 1
            if self._calls == 1:
                return [device]
            raise RuntimeError("transient failure")

    ds = FlakyDataSource()
    mgr = WigidashManager(datasource=ds)

    mgr.get_available_benchlabs(log_info=False)
    assert "COM3" in mgr.benchlab_devices

    mgr.get_available_benchlabs(log_info=False)
    assert "COM3" in mgr.benchlab_devices


class FakeSession:
    def __init__(self):
        self.ser = None
        self.device_info = None
        self.uid = None
        self.telemetry_history = None
        self.history = None
        self.selected_port = None
        self.telemetry_context = None


def test_start_telemetry_lock_prevents_duplicate_contexts():
    """Regression test for issue #22: two near-simultaneous start_telemetry
    calls for the same port must not create two telemetry_contexts entries."""
    device = {"port": "COM3", "uid": "UID-1", "firmware": "1.0"}
    ds = FakeDataSource([[device]])
    mgr = WigidashManager(datasource=ds)
    mgr.get_available_benchlabs(log_info=False)
    mgr.shutdown_event.set()  # stop telemetry_loop threads immediately after they start

    barrier = threading.Barrier(2)

    def call_start_telemetry(session):
        barrier.wait(timeout=2)
        mgr.start_telemetry("COM3", session)

    sessions = [FakeSession(), FakeSession()]
    threads = [threading.Thread(target=call_start_telemetry, args=(s,)) for s in sessions]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    assert len(mgr.telemetry_contexts) == 1
    ctx = mgr.telemetry_contexts["COM3"]
    assert len(ctx.sessions) == 2


def test_telemetry_step_does_not_mutate_caller_dict():
    ctx = TelemetryContext(port="COM3", ser=None, device_info={}, uid="UID-1", history=TelemetryHistory())
    original = {"Fans": [{"RPM": 1200}], "Vin": [None, 3.3]}
    original_fans_list = original["Fans"]
    original_fan_dict = original["Fans"][0]

    telemetry_step(ctx, sensor_struct=original)

    # Caller's dict/list/nested-dict identities must be untouched.
    assert "Duty" not in original_fan_dict
    assert original["Vin"] == [None, 3.3]
    assert original["Fans"] is original_fans_list
    assert ctx.sensor_data["Fans"][0]["Duty"] == 0
    assert ctx.sensor_data["Vin"] == [0.0, 3.3]


class FakeTouch:
    def __init__(self, x, y):
        self.Type = 1
        self.X = x
        self.Y = y


def test_overview_debounce_rejects_rapid_touch_and_accepts_later_one():
    """Regression test for issue #22: check_touch used a 0.1ms threshold and
    never set last_touch_time, making the debounce permanently dead."""
    from benchlab.wigidash.benchlab_overview import BenchlabOverview

    overview = BenchlabOverview(wigidash=object(), wigi=object())
    overview.running = True
    overview.footer_btn_config = []

    # Coordinates inside the bottom-left "power" card (x0=PADDING, y0=bottom_y).
    padding = overview.PADDING
    bottom_y = overview.HEADER_HEIGHT + padding + 160 + padding
    hit_x, hit_y = padding + 5, bottom_y + 5

    # First touch (well past any startup guard) hits the card and starts debounce.
    overview.last_touch_time = 0
    overview.check_touch(FakeTouch(hit_x, hit_y))
    assert overview.requested_graph_metrics is not None

    # Reset state, then simulate a second touch arriving 10ms later — within
    # the debounce window — it must be ignored.
    overview.running = True
    overview.requested_graph_metrics = None
    overview.last_touch_time = int(time.monotonic() * 1000) - 10
    overview.check_touch(FakeTouch(hit_x, hit_y))
    assert overview.requested_graph_metrics is None, (
        "debounce failed to reject a touch 10ms after the previous one"
    )

    # A touch after the debounce window elapses is allowed through again.
    overview.last_touch_time = int(time.monotonic() * 1000) - 200
    overview.check_touch(FakeTouch(hit_x, hit_y))
    assert overview.requested_graph_metrics is not None

