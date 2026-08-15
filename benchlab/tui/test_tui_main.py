"""Non-hardware regression tests for TUIApplication fleet-scanning logic.

No physical BenchLab device or curses screen is required — these mock
DataSourceManager directly.
"""

import types
from unittest.mock import MagicMock

from benchlab.tui.tui_main import TUIApplication


def _make_app():
    args = types.SimpleNamespace(source="direct", interval=1.0)
    app = TUIApplication(args)
    app.datasource_manager = MagicMock()
    return app


def test_scan_local_fleet_includes_currently_connected_device():
    """Regression test: the connected device must not vanish from 'f' rescan.

    discover_devices() probes each port with a *fresh* serial connection,
    which fails for the port our own active connection already holds
    exclusively (pyserial raises SerialException on the second open), and
    that failure is silently swallowed and the port dropped from the
    results. Before this fix, pressing 'f' to rescan the fleet while
    connected in direct mode returned zero devices, even though we were
    actively connected to one.
    """
    app = _make_app()
    # the busy port yields nothing
    app.datasource_manager.discover_devices.return_value = []
    app.datasource_manager.is_connected.return_value = True
    app.datasource_manager.get_selected_uid.return_value = "CONNECTED-UID"
    app.datasource_manager.snapshot.return_value = {
        "port": "COM5",
        "device_info": {"FwVersion": 0x01020304, "variant": "ORIGINAL"},
    }

    fleet = app._scan_local_fleet()

    assert len(fleet) == 1
    assert fleet[0]["uid"] == "CONNECTED-UID"
    assert fleet[0]["port"] == "COM5"
    assert fleet[0]["variant"] == "ORIGINAL"


def test_scan_local_fleet_does_not_duplicate_connected_device():
    """If discover_devices() already found the connected device, don't add
    it twice."""
    app = _make_app()
    app.datasource_manager.discover_devices.return_value = [
        {"uid": "CONNECTED-UID", "port": "COM5", "fw": "0x01020304",
         "variant": "ORIGINAL"},
    ]
    app.datasource_manager.is_connected.return_value = True
    app.datasource_manager.get_selected_uid.return_value = "CONNECTED-UID"
    app.datasource_manager.snapshot.return_value = {
        "port": "COM5",
        "device_info": {"FwVersion": 0x01020304, "variant": "ORIGINAL"},
    }

    fleet = app._scan_local_fleet()

    assert len(fleet) == 1


def test_scan_local_fleet_survives_none_port_when_disconnected():
    app = _make_app()
    app.datasource_manager.discover_devices.return_value = []
    app.datasource_manager.is_connected.return_value = False

    fleet = app._scan_local_fleet()

    assert fleet == []
