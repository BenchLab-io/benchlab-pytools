"""Non-hardware regression tests for benchlab.hwinfo.hwinfo_export.

These tests exercise the sensor grouping/filtering logic and the
export_all_devices stale-device cleanup with a fake datasource and a mocked
winreg — no physical BenchLab device or Windows registry access is required.
They guard against the bugs fixed in the hwinfo bug sweep (issue #20):
timestamp leaking into the exported sensors, stale registry entries for
disconnected devices, and the dead sensor-type branch.
"""

from benchlab.hwinfo import hwinfo_export as hw
import sys

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="hwinfo export is Windows-only"
)


def test_get_sensor_type_and_unit_fanextduty_still_other_percent():
    """FanExtDuty is matched by the 'duty' branch; removing the redundant
    'fanextduty' elif must not change its classification."""
    assert hw.get_sensor_type_and_unit("FanExtDuty") == ("Other", "%")


def test_process_sensor_data_skips_timestamp():
    data = {"Chip_Temp": 42.123, "timestamp": "2026-08-15T10:00:00+00:00"}
    grouped = hw._process_sensor_data(data)

    all_keys = [key for group in grouped.values() for key, _, _ in group]
    assert "timestamp" not in all_keys
    assert "Chip_Temp" in all_keys


def test_process_sensor_data_skips_fanextduty_and_fan_status():
    data = {
        "FanExtDuty": 50.0,
        "Fan1_Status": 1,
        "Fan1_RPM": 1200.0,
    }
    grouped = hw._process_sensor_data(data)

    all_keys = [key for group in grouped.values() for key, _, _ in group]
    assert "FanExtDuty" not in all_keys
    assert "Fan1_Status" not in all_keys
    assert "Fan1_RPM" in all_keys


class FakeDataSource:
    def __init__(self, fleets):
        self._fleets = list(fleets)
        self.source_type = "direct"

    def list_devices(self):
        return self._fleets.pop(0) if self._fleets else []

    def get_telemetry(self, uid):
        return {"Chip_Temp": 42.0}

    def disconnect(self):
        pass


def test_export_all_devices_removes_stale_registry_entry(monkeypatch):
    """Regression test for issue #20: a device present in one cycle and gone
    the next must have its registry subkey removed, not left stale."""
    hw.exported_devices.clear()

    deleted_paths = []
    monkeypatch.setattr(
        hw,
        "delete_registry_tree",
        lambda root,
        path: deleted_paths.append(path))
    monkeypatch.setattr(hw, "write_hwinfo_sensor", lambda *a, **kw: None)
    monkeypatch.setattr(hw.winreg, "OpenKey", lambda *a, **
                        kw: (_ for _ in ()).throw(FileNotFoundError()))

    device = {"uid": "UID-1", "port": "COM3"}
    device_name = "BENCHLAB_COM3_UID-1"

    # Cycle 1: device present. Cycle 2: fleet empty (device unplugged).
    # KeyboardInterrupt on the third list_devices() call ends the loop.
    call_count = {"n": 0}
    fleets = [[device], []]

    def list_devices_side_effect():
        call_count["n"] += 1
        if call_count["n"] > len(fleets):
            raise KeyboardInterrupt
        return fleets[call_count["n"] - 1]

    ds = FakeDataSource([])
    ds.list_devices = list_devices_side_effect

    monkeypatch.setattr(hw.time, "sleep", lambda s: None)

    hw.export_all_devices(update_interval=0, datasource=ds)

    assert f"{hw.HWINFO_CUSTOM_PATH}\\{device_name}" in deleted_paths
    assert device_name not in hw.exported_devices
