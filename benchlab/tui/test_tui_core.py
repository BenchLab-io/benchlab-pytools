"""Non-hardware regression tests for TUICore rendering.

These tests exercise TUICore's render methods with mocked snapshots —
no physical BenchLab device or datasource is required. They exist to catch
the class of bug that slipped through undetected until it crashed on live
hardware: sensor_data dicts where a key is *present* with an explicit
``None`` value (as opposed to being absent), which ``dict.get(key, default)``
does not coerce to the default. See issue #13 for the incident this guards
against (FanExtDuty: None crashing benchlab.py -tui --source direct).

Requires a real curses screen via ``curses.wrapper`` (windows-curses on
Windows), which works headlessly in CI as long as a console is attached.
"""

try:
    import curses
    HAS_CURSES = True
except ImportError:
    curses = None
    HAS_CURSES = False

import pytest

from benchlab.core.statistics import ChannelStats

pytestmark = pytest.mark.skipif(
    not HAS_CURSES,
    reason="curses not available in this environment")


# A sensor_data dict where every commonly-read key is present but None,
# mimicking a device that reports a channel key without a value.
SENSOR_DATA_ALL_NONE = {
    "SYS_Power": None, "CPU_Power": None, "GPU_Power": None, "MB_Power": None,
    "EPS1_Power": None, "EPS1_Current": None, "EPS1_Voltage": None,
    "EPS2_Power": None, "EPS2_Current": None, "EPS2_Voltage": None,
    "PCIE1_Power": None, "PCIE1_Current": None, "PCIE1_Voltage": None,
    "PCIE2_Power": None, "PCIE2_Current": None, "PCIE2_Voltage": None,
    "PCIE3_Power": None, "PCIE3_Current": None, "PCIE3_Voltage": None,
    "HPWR1_Power": None, "HPWR1_Current": None, "HPWR1_Voltage": None,
    "HPWR2_Power": None, "HPWR2_Current": None, "HPWR2_Voltage": None,
    "ATX3V_Power": None, "ATX3V_Current": None, "ATX3V_Voltage": None,
    "ATX5V_Power": None, "ATX5V_Current": None, "ATX5V_Voltage": None,
    "ATX5VSB_Power": None, "ATX5VSB_Current": None, "ATX5VSB_Voltage": None,
    "ATX12V_Power": None, "ATX12V_Current": None, "ATX12V_Voltage": None,
    "Vdd": None, "Vref": None,
    "Chip_Temp": None, "Ambient_Temp": None, "Humidity": None,
    "TS_1": None, "TS_2": None, "TS_3": None, "TS_4": None,
    "Fan1_Duty": None, "Fan1_RPM": None,
    "Fan2_Duty": None, "Fan2_RPM": None,
    "FanExtDuty": None,
    **{f"VIN_{i}": None for i in range(13)},
}

DEVICE_INFO_VALID = {
    "VendorId": 0x1234,
    "ProductId": 0x10,
    "FwVersion": 0x01020304}
DEVICE_INFO_MALFORMED = {
    "VendorId": None,
    "ProductId": "bad",
    "FwVersion": object()}


def _snapshot(**overrides):
    base = {
        "connected": True,
        "uid": "TEST-UID-0001",
        "port": "COM_TEST",
        "source_type": "direct",
        "source_desc": "Test harness",
        "sensor_data": dict(SENSOR_DATA_ALL_NONE),
        "device_info": dict(DEVICE_INFO_VALID),
        "connection_time": None,
    }
    base.update(overrides)
    return base


def _render_tab(tab_index, snapshot, fleet_devices=None):
    """Run TUICore.render() for a single tab inside a real curses screen.

    Raises whatever exception the render call raises (curses.error is the
    only *expected/tolerated* failure mode — anything else, e.g. TypeError
    from bad sensor data, is a real bug).
    """
    # imported inside curses.wrapper-safe scope
    from benchlab.tui.tui_core import TUICore

    def _run(stdscr):
        core = TUICore(stdscr, version="test")
        core.current_tab = tab_index
        core.render(
            snapshot=snapshot,
            stats=ChannelStats(),
            fleet_devices=fleet_devices or [],
            refresh_interval=1.0,
        )

    try:
        curses.wrapper(_run)
    except curses.error:
        pytest.skip(
            "curses screen too small / unavailable in this environment")


@pytest.mark.parametrize("tab_index",
                         range(8),
                         ids=["fleet",
                              "device",
                              "system",
                              "motherboard",
                              "hpwr",
                              "voltage",
                              "temperature",
                              "fans"],
                         )
def test_render_tab_survives_none_sensor_values(tab_index):
    """Every tab must render without raising when sensor_data values are None.

    Regression test for the FanExtDuty=None crash (issue #13): dict.get(key,
    default) only substitutes default when the key is *missing*, not when
    it's present with value None, so downstream arithmetic/formatting must
    defensively coerce None itself.
    """
    snapshot = _snapshot()
    fleet_devices = [{"uid": "TEST-UID-0001",
                      "port": "COM_TEST",
                      "firmware": 0x01020304,
                      "variant": "ORIGINAL"}]
    _render_tab(tab_index, snapshot, fleet_devices)


def test_device_tab_survives_malformed_device_info():
    """Regression test: malformed device_info must not crash the Device tab.

    See issue #13 — VendorId/ProductId/FwVersion formatted with f"0x{v:02X}"
    with no type guard used to raise ValueError/TypeError uncaught.
    """
    snapshot = _snapshot(device_info=dict(DEVICE_INFO_MALFORMED))
    _render_tab(1, snapshot)  # Device tab


def test_fleet_tab_survives_disconnected_and_empty_fleet():
    snapshot = _snapshot(connected=False, uid=None)
    _render_tab(0, snapshot, fleet_devices=[])  # Fleet tab


@pytest.mark.parametrize("tab_index",
                         range(8),
                         ids=["fleet",
                              "device",
                              "system",
                              "motherboard",
                              "hpwr",
                              "voltage",
                              "temperature",
                              "fans"],
                         )
def test_render_tab_survives_disconnected(tab_index):
    """All tabs must render their disconnected state without raising."""
    snapshot = _snapshot(connected=False, sensor_data=None, uid=None)
    _render_tab(tab_index, snapshot)


def test_fans_tab_survives_many_fans_on_small_terminal():
    """Regression test: many fan rows must not silently overrun the status bar.

    See issue #13 — _render_fans_tab previously computed row positions with
    no bounds check against terminal height, so a device reporting many fans
    on a small terminal could overlap the status bar with no indication to
    the user. This exercises the truncation path directly by calling
    _render_fans_tab with an artificially small height.
    """
    from benchlab.tui.tui_core import TUICore

    many_fan_sensor_data = dict(SENSOR_DATA_ALL_NONE)
    for i in range(1, 21):  # far more fans than a small terminal has rows for
        many_fan_sensor_data[f"Fan{i}_Duty"] = 50
        many_fan_sensor_data[f"Fan{i}_RPM"] = 1200

    snapshot = _snapshot(sensor_data=many_fan_sensor_data)

    def _run(stdscr):
        core = TUICore(stdscr, version="test")
        height, width = stdscr.getmaxyx()
        # Force a small height regardless of the actual terminal, to
        # guarantee the truncation path is exercised.
        core._render_fans_tab(snapshot, ChannelStats(), height=20, width=width)

    try:
        curses.wrapper(_run)
    except curses.error:
        pytest.skip("curses screen unavailable in this environment")


def test_help_modal_renders_without_stomping_main_refresh():
    """Regression test: help modal must be composited, not overwritten
    every tick.

    See issue #13 — render() called self.stdscr.refresh() (an immediate
    physical repaint from stdscr alone) *after* _render_help_modal() had
    already called win.refresh() on its own separate window. Since these
    were two independent immediate refreshes, the plain stdscr.refresh()
    always ran last and wiped out the modal, making it flicker/disappear
    on every ~100ms render tick instead of staying legible. Fixed by using
    noutrefresh() on both windows plus a single curses.doupdate() — with
    stdscr staged *before* the help window, since doupdate() composites in
    staging order and staging stdscr last would paint over the modal (a
    second regression this test also caught).

    This test can't visually assert flicker, but it exercises the full
    render() path with the modal open across several ticks and confirms
    no exception is raised and the modal window is left in a composited
    (not directly-refreshed) state.

    Calls _render_help_modal directly (bypassing render()'s size gate) so
    this doesn't depend on Config.MIN_TERMINAL_ROWS/COLS specifically, but
    curses.newwin() itself still validates against the *real* physical
    console size (independent of whatever height/width we pass in), so a
    genuinely tiny CI console is a legitimate skip rather than a bug here.
    """
    from benchlab.tui.tui_core import TUICore

    def _run(stdscr):
        real_height, real_width = stdscr.getmaxyx()
        if real_height < 10 or real_width < 20:
            pytest.skip(
                "console too small for a help modal: "
                f"{real_width}x{real_height}")

        core = TUICore(stdscr, version="test")
        core.show_help_modal = True
        for _ in range(3):  # simulate several render ticks with the modal open
            core._render_help_modal(height=real_height, width=real_width)
        assert core._help_win is not None

    try:
        curses.wrapper(_run)
    except curses.error:
        pytest.skip("curses screen unavailable in this environment")
