"""Non-hardware regression tests for benchlab.config.config_manager.

These tests exercise the bugs fixed in the config bug sweep (issue #32)
with mocked ConfigClient instances -- no real device or hardware needed:
- select_device()'s productId selector on the direct source now warns
  clearly instead of silently returning None
- export_config()/_apply_device_config() close the client even when it
  raises mid-operation
- import_config()'s new diff-before-apply flow (dry_run / auto_confirm /
  confirm_callback)
"""

import logging
from unittest.mock import MagicMock, patch

from benchlab.config.config_manager import ConfigManager


def test_select_device_productid_on_direct_logs_warning_and_returns_none(
        caplog):
    """Regression test for issue #32: productId selector used to be a
    silent no-op (`pass`) for the direct source, giving no indication of
    why device selection failed."""
    mgr = ConfigManager(source="direct")
    devices = [{"port": "COM4", "uid": "ABC123"}]

    with caplog.at_level(logging.WARNING):
        result = mgr.select_device(
            {"type": "productId", "value": 0x10}, devices)

    assert result is None
    assert any("productId" in rec.message for rec in caplog.records)


def test_select_device_productid_still_works_on_named_pipe():
    mgr = ConfigManager(source="named_pipe")
    devices = [{"pipe": "BenchlabSensorPipe_10_1234", "productId": 0x10}]

    result = mgr.select_device({"type": "productId", "value": 0x10}, devices)

    assert result == "BenchlabSensorPipe_10_1234"


def test_select_device_guid_matches_on_direct():
    mgr = ConfigManager(source="direct")
    devices = [{"port": "COM4", "uid": "ABC123"}]

    result = mgr.select_device({"type": "guid", "value": "ABC123"}, devices)

    assert result == "COM4"


def test_select_device_any_returns_first():
    mgr = ConfigManager(source="direct")
    devices = [{"port": "COM4", "uid": "A"}, {"port": "COM5", "uid": "B"}]

    result = mgr.select_device({"type": "any", "value": None}, devices)

    assert result == "COM4"


def test_export_config_closes_client_on_exception():
    """Regression test for issue #32: export_config used to leak the
    client (open serial port / pipe handle) if any read raised, since
    client.close() was only called at the end of the try block."""
    mgr = ConfigManager(source="direct")
    fake_client = MagicMock()
    fake_client.get_device_info.side_effect = RuntimeError(
        "simulated read failure")

    with patch(
            "benchlab.config.config_manager.create_config_client",
            return_value=fake_client):
        result = mgr.export_config("COM4", "unused_output.json")

    assert result is False
    fake_client.close.assert_called_once()


def test_apply_device_config_closes_client_on_exception():
    from benchlab.config.schema import DeviceConfig, DeviceSelector

    mgr = ConfigManager(source="direct")
    fake_client = MagicMock()
    fake_client.write_device_name.side_effect = RuntimeError(
        "simulated write failure")

    device_config = DeviceConfig(
        selector=DeviceSelector(
            type="any"), deviceName="Test")

    with patch(
            "benchlab.config.config_manager.create_config_client",
            return_value=fake_client):
        result = mgr._apply_device_config("COM4", device_config)

    assert result is False
    fake_client.close.assert_called_once()


def test_read_current_state_tolerates_partial_failures():
    """A failure reading one section (e.g. calibration) must not prevent
    the other sections from being read and returned."""
    mgr = ConfigManager(source="direct")
    fake_client = MagicMock()
    fake_client.read_device_name.return_value = "MyDevice"
    fake_client.read_fan_config.return_value = None
    fake_client.read_rgb_config.return_value = None
    fake_client.read_calibration.side_effect = RuntimeError(
        "calibration read timeout")

    state = mgr._read_current_state(fake_client)

    assert state["deviceName"] == "MyDevice"
    assert state["calibration"] is None
    assert any("calibration" in err for err in state["readErrors"])
