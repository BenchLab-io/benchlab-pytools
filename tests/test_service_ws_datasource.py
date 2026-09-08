"""Non-hardware tests for the ``service_ws`` data source.

Exercises ServiceWsDataSource's frame-dispatch logic and the
ServiceWsConfig URL validator directly — no running BenchLab service or
hardware required (unlike the integration tests in
tests/test_data_sources.py). Frame shapes follow
bl-benchlab-service/docs/events.md.
"""

import pytest

from benchlab.core.config import ServiceWsConfig
from benchlab.core.datasource import (
    ServiceWsDataSource,
    _normalise_cs_v,
    create_datasource,
)

try:
    import websockets  # noqa: F401
    HAS_WS = True
except ImportError:
    HAS_WS = False

pytestmark = pytest.mark.skipif(
    not HAS_WS, reason="websockets not available")


HELLO = {
    "type": "hello",
    "serviceVersion": "2.5.1",
    "pollIntervalMs": 1000,
    "devices": [
        {
            "uid": "UID1",
            "name": "BENCHLAB",
            "productId": 0x11,
            "port": "COM5",
            "status": "CONNECTED",
            "sensorCount": 66,
            "firmwareVersion": 6,
        }
    ],
}

TELEMETRY = {
    "type": "telemetry",
    "uid": "UID1",
    "ts": "2026-09-03T12:00:00.100Z",
    "v": {"EPS1_P": 96.3, "TS1": 31.2, "ATX12V_V": 12.02, "V1": 3.31},
}


def _ds():
    return ServiceWsDataSource(url="ws://localhost:8585/events")


def test_hello_populates_devices():
    ds = _ds()
    ds._dispatch(HELLO)

    devices = ds.list_devices()
    assert len(devices) == 1
    dev = devices[0]
    assert dev["uid"] == "UID1"
    assert dev["ProductId"] == 0x11
    assert dev["variant"] == "BL2"
    assert ds.is_connected()
    assert ds._hello_event.is_set()
    assert ds._service_version == "2.5.1"


def test_telemetry_is_normalised():
    ds = _ds()
    ds._dispatch(HELLO)
    ds._dispatch(TELEMETRY)

    tele = ds.get_telemetry("UID1")
    assert tele is not None
    # ShortName keys mapped to TUI keys via the shared CS_SHORT_NAME_MAP
    assert tele["EPS1_Power"] == 96.3
    assert tele["TS_1"] == 31.2
    assert tele["ATX12V_Voltage"] == 12.02
    assert tele["VIN_0"] == 3.31
    assert tele["timestamp"] == "2026-09-03T12:00:00.100Z"


def test_telemetry_omits_absent_sensors():
    ds = _ds()
    ds._dispatch(HELLO)
    ds._dispatch(TELEMETRY)

    tele = ds.get_telemetry("UID1")
    # Only the four sensors present in v (+ timestamp) should be there
    assert set(tele) == {
        "EPS1_Power", "TS_1", "ATX12V_Voltage", "VIN_0", "timestamp"}


def test_device_disconnected_removes_uid():
    ds = _ds()
    ds._dispatch(HELLO)
    ds._dispatch(TELEMETRY)
    ds._dispatch({
        "type": "device",
        "uid": "UID1",
        "event": "disconnected",
        "device": HELLO["devices"][0],
    })

    assert ds.list_devices() == []
    assert ds.get_telemetry("UID1") is None


def test_device_connected_adds_uid():
    ds = _ds()
    ds._dispatch(HELLO)
    ds._dispatch({
        "type": "device",
        "uid": "UID2",
        "event": "connected",
        "device": {"uid": "UID2", "productId": 0x10, "port": "COM7"},
    })

    uids = {d["uid"] for d in ds.list_devices()}
    assert uids == {"UID1", "UID2"}
    dev2 = ds.get_device_info("UID2")
    assert dev2["variant"] == "ORIGINAL"


def test_control_frames_do_not_raise():
    ds = _ds()
    ds._dispatch({"type": "subscribed", "all": True, "uids": []})
    ds._dispatch({"type": "pong", "ts": "2026-09-03T12:00:00.100Z"})
    ds._dispatch({"type": "error", "error": "invalid-argument",
                  "message": "nope"})


def test_normalise_cs_v_skips_sentinel():
    out = _normalise_cs_v({"EPS1_P": 12.0, "TS1": -3.0e300})
    assert out == {"EPS1_Power": 12.0}


# ----------------------------------------------------------------------
# Config URL validation
# ----------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("ws://localhost:8585/events", "ws://localhost:8585/events"),
    ("http://localhost:8585", "ws://localhost:8585/events"),
    ("https://host:9000", "wss://host:9000/events"),
    ("localhost:8585", "ws://localhost:8585/events"),
    ("ws://localhost:8585/events/", "ws://localhost:8585/events"),
])
def test_config_url_validator(raw, expected):
    assert ServiceWsConfig(url=raw).url == expected


def test_factory_creates_service_ws():
    ds = create_datasource("service_ws", url="ws://localhost:8585/events")
    assert isinstance(ds, ServiceWsDataSource)
    assert ds.source_type == "service_ws"
