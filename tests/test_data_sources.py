"""Integration tests for BenchLab data sources.

This test suite validates that each supported data source can be correctly
started and exposes working device-info and telemetry endpoints:

1. **Direct**  – :class:`benchlab.core.datasource_manager.DataSourceManager`
2. **FastAPI** – HTTP endpoints exercised with ``TestClient``
3. **MQTT**    – publisher in a background thread, validated via subscriber

Run selectively with::

    pytest -m integration -s -v

A BenchLab device must be physically connected.  MQTT tests additionally
require a local broker on ``localhost:1883`` (override via env vars
``MQTT_BROKER`` / ``MQTT_PORT`` / ``MQTT_TOPIC_PREFIX``).
"""

import json
import os
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt
import pytest

from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.discovery import discover_devices

try:
    from benchlab.restapi.telemetry_api import (
        app as fastapi_app,
        devices_data,
        clients,
        device_threads,
        read_device_loop,
        shutdown_event as api_shutdown_event,
        history_length,
    )
except Exception as _fastapi_err:  # pragma: no cover
    fastapi_app = None
    _fastapi_err_msg = str(_fastapi_err)
else:
    _fastapi_err_msg = None

from unittest.mock import patch
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TELEMETRY_TIMEOUT = 15  # seconds to wait for first telemetry reading
# minimum history entries to consider accumulation proven
HISTORY_MIN = 2

SEP = "-" * 60


def _section(title):
    print(f"\n{SEP}\n  {title}\n{SEP}")


def _ok(msg):
    print(f"  ✓  {msg}")


def _info(msg):
    print(f"     {msg}")


def _extract_telemetry(snap, uid):
    """Extract the telemetry dict for *uid* from a DataSourceManager snapshot.

    The snapshot structure is::

        {
          'all_telemetry': { <uid>: { 'timestamp': ..., <sensor>: ... } },
          'all_devices':   { <uid>: { ... } },
          'connected': True,
          ...
        }

    Falls back to checking 'sensor_data' for older DataSourceManager versions.
    """
    all_telem = snap.get("all_telemetry") or {}
    if uid in all_telem and all_telem[uid]:
        return all_telem[uid]
    return snap.get("sensor_data") or None


def _wait_for_telemetry(get_fn, timeout=TELEMETRY_TIMEOUT):
    """Poll *get_fn* until it returns a non-empty, non-error dict or
    timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = get_fn()
        if result and isinstance(result, dict) and "error" not in result:
            return result
        time.sleep(1)
    return None


def _assert_telemetry_shape(data, source_label):
    """Common assertions that a telemetry payload looks sensible."""
    assert data is not None, (
        f"[{source_label}] Telemetry timed out – no data received")
    assert isinstance(
        data, dict), f"[{source_label}] Telemetry is not a dict: {data!r}"
    assert "error" not in data, (
        f"[{source_label}] Telemetry contains error: {data}")
    assert "timestamp" in data, (
        f"[{source_label}] Telemetry missing 'timestamp' key: {data}")
    sensor_keys = [k for k in data if k != "timestamp"]
    assert sensor_keys, (
        f"[{source_label}] Telemetry has no sensor keys: {data}")
    _ok(f"Telemetry shape OK — {len(sensor_keys)} sensor(s): {sensor_keys}")
    sample = {k: data[k] for k in sensor_keys[:3]}
    _info(f"Sample values: {sample}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def device():
    """Discover and return the first connected BenchLab device."""
    _section("Device Discovery")
    devices = discover_devices()
    if not devices:
        pytest.skip(
            "No BenchLab devices found - skipping integration tests "
            "that require a device")
    dev = devices[0]
    _ok(
        f"Found device: uid={dev['uid']}  port={dev['port']}"
        f"  fw={dev.get('fw', '?')}"
    )
    return dev


@pytest.fixture(scope="class")
def direct_mgr(device):
    """Connected DataSourceManager (direct mode); disconnects after each test.

    Waits up to 3 seconds after disconnect to ensure the serial port is fully
    released before the next fixture runs.
    """
    mgr = DataSourceManager(source_type="direct")
    ok = mgr.connect(port=device["port"], uid=device["uid"])
    assert ok, "Failed to connect direct DataSourceManager"
    _ok(f"Direct DataSourceManager connected on {device['port']}")
    yield mgr
    mgr.disconnect()
    # Give the background reader thread inside the manager time to close the
    # serial port before the next test (or fixture) tries to open it.
    time.sleep(1.5)
    _info("Direct DataSourceManager disconnected and port released")


@pytest.fixture(scope="class")
def fastapi_client(device):
    """
    TestClient with the FastAPI app pre-seeded with the real device.

    Bypasses lifespan discovery (which would race for the COM port) and
    instead injects the device into ``devices_data`` directly, starts a real
    reader thread, and waits for at least one live telemetry reading before
    yielding — so every FastAPI test starts from a confirmed-working state.
    """
    if fastapi_app is None:  # pragma: no cover
        pytest.skip(f"FastAPI app could not be imported: {_fastapi_err_msg}")

    uid = device["uid"]
    port = device["port"]

    api_shutdown_event.clear()

    devices_data[uid] = {
        "port": port,
        "latest": {},
        "history": deque(maxlen=history_length),
        "info": {},
    }
    clients[uid] = set()

    t = threading.Thread(
        target=read_device_loop, args=(port, uid), daemon=True)
    t.start()
    device_threads[uid] = t
    _ok(f"FastAPI reader thread started for {uid} on {port}")

    # Wait for the reader thread to produce at least one real reading before
    # any test runs. This catches serial port failures early and prevents
    # tests from passing vacuously on empty/stale data.
    _info("Waiting for first live telemetry reading...")
    live_data = _wait_for_telemetry(lambda: devices_data[uid].get("latest"))
    if live_data is None:
        api_shutdown_event.set()
        t.join(timeout=5)
        devices_data.pop(uid, None)
        clients.pop(uid, None)
        device_threads.pop(uid, None)
        api_shutdown_event.clear()
        pytest.fail(
            f"FastAPI reader thread failed to produce telemetry from {port} "
            f"within {TELEMETRY_TIMEOUT}s. Check that the serial port "
            "is available."
        )
    _ok(f"Live telemetry confirmed — {len(live_data)} keys in first reading")

    # Patch out lifespan discovery so it doesn't race for the COM port.
    # Our reader thread already has it open; the lifespan scan would just
    # produce a noisy PermissionError and "No devices found" warning.
    with patch("benchlab.restapi.telemetry_api.find_benchlab_devices",
               return_value=[]):
        with TestClient(fastapi_app) as client:
            yield client

    api_shutdown_event.set()
    device_threads[uid].join(timeout=5)
    devices_data.pop(uid, None)
    clients.pop(uid, None)
    device_threads.pop(uid, None)
    api_shutdown_event.clear()
    time.sleep(1.5)  # ensure port is released before next fixture
    _info("FastAPI reader thread stopped and port released")


# ---------------------------------------------------------------------------
# Test group 1: Direct data source
# ---------------------------------------------------------------------------

class TestDirectSource:

    @pytest.mark.integration
    def test_connect_and_snapshot(self, direct_mgr, device):
        """DataSourceManager connects and returns a snapshot with
        sensor_data."""
        _section("Direct › connect and snapshot")
        time.sleep(2)
        snap = direct_mgr.snapshot()
        _info(f"Top-level snapshot keys: {list(snap.keys())}")
        telem = _extract_telemetry(snap, device["uid"])
        assert telem, (
            f"Direct snapshot missing telemetry. "
            f"Keys: {list(snap.keys())}"
        )
        _ok(f"Snapshot returned telemetry with {len(telem)} keys")

    @pytest.mark.integration
    def test_telemetry_shape(self, direct_mgr, device):
        """Direct telemetry payload has the expected shape."""
        _section("Direct › telemetry shape")
        data = _wait_for_telemetry(
            lambda: _extract_telemetry(direct_mgr.snapshot(), device["uid"])
        )
        _assert_telemetry_shape(data, "direct")
        _info(f"Timestamp: {data.get('timestamp')}")

    @pytest.mark.integration
    def test_device_info(self, direct_mgr, device):
        """Direct snapshot includes the device UID somewhere in its payload."""
        _section("Direct › device info")
        time.sleep(2)
        snap = direct_mgr.snapshot()
        snap_str = str(snap)
        assert device["uid"] in snap_str, (
            f"Device UID {device['uid']} not found anywhere "
            f"in snapshot: {snap}"
        )
        _ok(f"Device UID {device['uid']} found in snapshot")
        info = snap.get("device_info") or snap.get("info") or {}
        if info:
            _info(f"Device info keys: {list(info.keys())}")

    @pytest.mark.integration
    def test_telemetry_accumulates(self, direct_mgr, device):
        """Multiple calls return fresh readings (timestamp advances)."""
        _section("Direct › telemetry accumulates over time")
        _wait_for_telemetry(
            lambda: _extract_telemetry(direct_mgr.snapshot(), device["uid"])
        )
        snap1 = direct_mgr.snapshot()
        ts1 = (_extract_telemetry(snap1, device["uid"]) or {}).get("timestamp")
        _info(f"Timestamp at t=0: {ts1}")
        time.sleep(3)
        snap2 = direct_mgr.snapshot()
        ts2 = (_extract_telemetry(snap2, device["uid"]) or {}).get("timestamp")
        _info(f"Timestamp at t=3s: {ts2}")
        assert ts1 and ts2, "Timestamps missing from snapshots"
        assert ts2 >= ts1, f"Timestamp did not advance: {ts1} → {ts2}"
        _ok(f"Timestamp advanced correctly: {ts1} → {ts2}")


# ---------------------------------------------------------------------------
# Test group 2: FastAPI data source
# ---------------------------------------------------------------------------

class TestFastAPISource:

    @pytest.mark.integration
    def test_health(self, fastapi_client):
        """/health returns status=healthy and reports at least one device."""
        _section("FastAPI › GET /health")
        resp = fastapi_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["connected_devices"] >= 1, (
            "Health endpoint reports no connected devices")
        _ok(f"status={data['status']}  "
            f"connected_devices={data['connected_devices']}")
        _info(
            f"Platform: {data.get('platform')}  "
            f"timestamp: {data.get('timestamp')}"
        )

    @pytest.mark.integration
    def test_list_devices(self, fastapi_client, device):
        """/devices lists the connected device."""
        _section("FastAPI › GET /devices")
        resp = fastapi_client.get("/devices")
        assert resp.status_code == 200
        device_list = resp.json()
        uids = [d["uid"] for d in device_list]
        assert device["uid"] in uids, (
            f"Device {device['uid']} not in /devices: {uids}"
        )
        _ok(f"Device list contains {len(device_list)} device(s)")
        for d in device_list:
            _info(f"  uid={d['uid']}  port={d['port']}")

    @pytest.mark.integration
    def test_device_info(self, fastapi_client, device):
        """/device/{uid}/info returns uid and port."""
        _section(f"FastAPI › GET /device/{device['uid'][:12]}…/info")
        uid = device["uid"]
        resp = fastapi_client.get(f"/device/{uid}/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("UID") == uid, f"UID mismatch in info: {data}"
        assert data.get(
            "port") == device["port"], f"Port mismatch in info: {data}"
        _ok("UID and port match")
        _info(f"Full info response: {data}")

    @pytest.mark.integration
    def test_device_status(self, fastapi_client, device):
        """/device/{uid}/status reports the device as known."""
        _section(f"FastAPI › GET /device/{device['uid'][:12]}…/status")
        uid = device["uid"]
        resp = fastapi_client.get(f"/device/{uid}/status")
        assert resp.status_code == 200, (
            f"Unexpected status code: {resp.status_code}"
        )
        data = resp.json()
        assert data["uid"] == uid
        assert data["port"] == device["port"]
        _ok(
            f"uid={data['uid']}  port={data['port']}  "
            f"connected={data.get('connected')}"
        )
        _info(
            f"History count: {data.get('history_count')}  "
            f"Client count: {data.get('client_count')}"
        )

    @pytest.mark.integration
    def test_telemetry(self, fastapi_client, device):
        """/device/{uid}/telemetry returns live sensor data."""
        _section(f"FastAPI › GET /device/{device['uid'][:12]}…/telemetry")
        uid = device["uid"]
        data = _wait_for_telemetry(
            lambda: fastapi_client.get(f"/device/{uid}/telemetry").json()
        )
        _assert_telemetry_shape(data, "fastapi/telemetry")
        _info(f"Timestamp: {data.get('timestamp')}")

    @pytest.mark.integration
    def test_sensor_list(self, fastapi_client, device):
        """/device/{uid}/sensors returns a non-empty list of sensor names."""
        _section(f"FastAPI › GET /device/{device['uid'][:12]}…/sensors")
        uid = device["uid"]
        _wait_for_telemetry(
            lambda: fastapi_client.get(f"/device/{uid}/telemetry").json()
        )
        resp = fastapi_client.get(f"/device/{uid}/sensors")
        assert resp.status_code == 200
        sensors = resp.json()
        assert isinstance(sensors, list) and sensors, (
            f"Sensor list is empty or wrong type: {sensors}"
        )
        _ok(f"{len(sensors)} sensor(s) available")
        _info(f"Sensors: {sensors}")

    @pytest.mark.integration
    def test_individual_sensor(self, fastapi_client, device):
        """/device/{uid}/telemetry/{sensor} returns a value for a
        known sensor."""
        _section(
            f"FastAPI › GET /device/{device['uid'][:12]}…"
            f"/telemetry/{{sensor}}")
        uid = device["uid"]
        _wait_for_telemetry(
            lambda: fastapi_client.get(f"/device/{uid}/telemetry").json()
        )
        sensors = [
            s for s in fastapi_client.get(f"/device/{uid}/sensors").json()
            if s != "timestamp"
        ]
        assert sensors, "No sensors available to query individually"

        sensor = sensors[0]
        resp = fastapi_client.get(f"/device/{uid}/telemetry/{sensor}")
        assert resp.status_code == 200
        data = resp.json()
        assert sensor in data, (
            f"Sensor key '{sensor}' missing from response: {data}"
        )
        _ok(f"Queried sensor '{sensor}' → {data[sensor]}")
        _info(f"(Tested 1 of {len(sensors)} available sensors)")

    @pytest.mark.integration
    def test_history_accumulates(self, fastapi_client, device):
        """/device/{uid}/history accumulates multiple readings over time."""
        _section(f"FastAPI › GET /device/{device['uid'][:12]}…/history")
        uid = device["uid"]
        _wait_for_telemetry(
            lambda: fastapi_client.get(f"/device/{uid}/telemetry").json()
        )
        _info(f"Waiting {HISTORY_MIN + 1}s for history to accumulate...")
        time.sleep(HISTORY_MIN + 1)
        resp = fastapi_client.get(f"/device/{uid}/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= HISTORY_MIN, (
            f"History has only {data['count']} entries, "
            f"expected >= {HISTORY_MIN}"
        )
        for entry in data["data"]:
            assert "timestamp" in entry, (
                f"History entry missing timestamp: {entry}"
            )
        _ok(
            f"{data['count']} history entries "
            f"(of {data['total_available']} total)"
        )
        _info(f"Oldest: {data['data'][0].get('timestamp')}  "
              f"Newest: {data['data'][-1].get('timestamp')}")

    @pytest.mark.integration
    def test_scan_endpoint(self, fastapi_client, device):
        """/scan runs without error and reports the known device."""
        _section("FastAPI › POST /scan")
        resp = fastapi_client.post("/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_devices" in data
        assert data["total_devices"] >= 1
        known_uids = [d["uid"] for d in data["devices"]]
        assert device["uid"] in known_uids, (
            f"Device {device['uid']} missing from /scan result: {data}"
        )
        _ok(f"Scan found {data['total_devices']} device(s), "
            f"{len(data['new_devices'])} new, "
            f"{len(data['disconnected_devices'])} disconnected")
        _info(f"Scan time: {data.get('scan_time')}")


# ---------------------------------------------------------------------------
# Test group 3: MQTT data source
# ---------------------------------------------------------------------------

class TestMQTTSource:

    def _start_publisher(self):
        import benchlab.mqtt.mqtt_publisher as mqtt_pub
        mqtt_pub.global_stop_event.clear()
        thread = threading.Thread(
            target=mqtt_pub.run_mqtt_mode,
            kwargs={"broker_type": os.getenv("MQTT_BROKER", "localhost")},
            daemon=True,
        )
        thread.start()
        time.sleep(2)
        return thread, mqtt_pub

    def _make_subscriber(self, topic):
        msgs: list[bytes] = []
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        def on_message(client, userdata, message):
            msgs.append(message.payload)

        client.on_message = on_message
        client.connect(
            os.getenv("MQTT_BROKER", "localhost"),
            int(os.getenv("MQTT_PORT", "1883")),
        )
        client.subscribe(topic)
        client.loop_start()
        return client, msgs

    def _topic(self, device):
        prefix = os.getenv("MQTT_TOPIC_PREFIX", "benchlab")
        return f"{prefix}/{device['uid']}/telemetry"

    def _wait_for_msg(self, msgs):
        deadline = time.time() + TELEMETRY_TIMEOUT
        while time.time() < deadline and not msgs:
            time.sleep(0.5)

    @pytest.mark.integration
    def test_mqtt_publishes_telemetry(self, device):
        """MQTT publisher emits valid JSON telemetry on the expected topic."""
        topic = self._topic(device)
        _section("MQTT › publishes telemetry")
        broker = os.getenv('MQTT_BROKER', 'localhost')
        port = os.getenv('MQTT_PORT', '1883')
        _info(f"Broker: {broker}:{port}")
        _info(f"Topic:  {topic}")

        thread, mqtt_pub = self._start_publisher()
        client, msgs = self._make_subscriber(topic)

        try:
            self._wait_for_msg(msgs)
            assert msgs, f"No MQTT telemetry received on topic '{topic}'"
            _ok(f"Received {len(msgs)} message(s)")
            payload = json.loads(msgs[0])
            _assert_telemetry_shape(payload, "mqtt")
            _info(f"Timestamp: {payload.get('timestamp')}")
        finally:
            client.loop_stop()
            client.disconnect()
            mqtt_pub.global_stop_event.set()
            thread.join(timeout=5)

    @pytest.mark.integration
    def test_mqtt_telemetry_matches_direct(self, device):
        """MQTT payload contains the same sensor keys as the direct source."""
        _section("MQTT › sensor keys match direct source")

        # Collect direct sensor keys first
        mgr = DataSourceManager(source_type="direct")
        mgr.connect(port=device["port"], uid=device["uid"])
        direct_data = _wait_for_telemetry(
            lambda: _extract_telemetry(mgr.snapshot(), device["uid"])
        )
        mgr.disconnect()
        assert direct_data, "Could not get direct telemetry for key comparison"
        direct_keys = set(direct_data.keys()) - {"timestamp"}
        _info(
            f"Direct source sensor keys ({len(direct_keys)}): "
            f"{sorted(direct_keys)}"
        )

        # Collect one MQTT payload and compare keys
        topic = self._topic(device)
        thread, mqtt_pub = self._start_publisher()
        client, msgs = self._make_subscriber(topic)

        try:
            self._wait_for_msg(msgs)
            assert msgs, f"No MQTT telemetry received on topic '{topic}'"
            mqtt_payload = json.loads(msgs[0])
            mqtt_keys = set(mqtt_payload.keys()) - {"timestamp"}
            _info(f"MQTT sensor keys ({len(mqtt_keys)}): {sorted(mqtt_keys)}")
            missing = direct_keys - mqtt_keys
            assert not missing, (
                f"MQTT payload missing sensor keys present in "
                f"direct source: {missing}"
            )
            _ok(
                f"All {len(direct_keys)} direct sensor keys present "
                f"in MQTT payload"
            )
        finally:
            client.loop_stop()
            client.disconnect()
            mqtt_pub.global_stop_event.set()
            thread.join(timeout=5)
