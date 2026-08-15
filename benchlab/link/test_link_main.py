"""Non-hardware regression tests for benchlab.link.link_main.

No real MQTT broker or BenchLab hardware required — CloudMQTTClient and
DataSourceManager are stubbed out. Covers:
- topic pattern interpolation of both {uid} and {client_uuid}
- config resolution priority (args > env > .env > config file > defaults)
- CloudMQTTClient._build_client() pins callback_api_version explicitly
  (paho-mqtt 2.x deprecates the implicit VERSION1 default) and its
  on_connect/on_disconnect callbacks use the matching 5-arg v2 signature
"""

import pytest

from benchlab.link.link_main import (
    BenchlabLink,
    CloudMQTTClient,
    _resolve_config,
)


class FakeDataSourceManager:
    def __init__(self, snapshots):
        self._snapshots = snapshots

    def list_devices(self):
        return {uid: {} for uid in self._snapshots}

    def select_device(self, uid):
        return True

    def snapshot(self):
        return {}


class FakeCloud:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=1):
        self.published.append((topic, payload, qos))
        return True


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "REMOTE_MQTT_HOST", "REMOTE_MQTT_PORT", "REMOTE_MQTT_USER",
        "REMOTE_MQTT_PASS", "CLIENT_UUID", "LINK_TOPIC_PATTERN",
        "MQTT_POLL_RATE",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_link(topic_pattern, client_uuid, snapshots):
    link = BenchlabLink.__new__(BenchlabLink)
    link.datasource = FakeDataSourceManager(snapshots)
    link.cloud = FakeCloud()
    link.cfg = {"topic": topic_pattern, "qos": 1, "client_uuid": client_uuid}
    link._snapshots = snapshots
    import threading
    link._snap_lock = threading.Lock()
    return link


def test_publish_all_interpolates_uid_only_pattern():
    link = _make_link("benchlab/{uid}/telemetry",
                      None, {"dev-1": {"temp": 42}})
    published = link.publish_all()
    assert published == 1
    topic, payload, qos = link.cloud.published[0]
    assert topic == "benchlab/dev-1/telemetry"
    assert payload == {"uid": "dev-1", "temp": 42}


def test_publish_all_interpolates_client_uuid_token():
    link = _make_link(
        "clients/{client_uuid}/devices/{uid}/telemetry",
        "client-abc",
        {"dev-1": {"temp": 42}},
    )
    published = link.publish_all()
    assert published == 1
    topic, _, _ = link.cloud.published[0]
    assert topic == "clients/client-abc/devices/dev-1/telemetry"


def test_publish_all_client_uuid_defaults_to_empty_string_when_unset():
    link = _make_link(
        "clients/{client_uuid}/devices/{uid}/telemetry",
        None,
        {"dev-1": {"temp": 42}},
    )
    published = link.publish_all()
    assert published == 1
    topic, _, _ = link.cloud.published[0]
    assert topic == "clients//devices/dev-1/telemetry"


def test_resolve_config_reads_client_uuid_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIENT_UUID", "env-client-uuid")
    monkeypatch.setenv("LINK_CONFIG_PATH", str(tmp_path / "missing.config"))
    cfg = _resolve_config(args=None)
    assert cfg["client_uuid"] == "env-client-uuid"


def test_resolve_config_args_override_env(monkeypatch, tmp_path):
    import types

    monkeypatch.setenv("CLIENT_UUID", "env-client-uuid")
    monkeypatch.setenv("LINK_CONFIG_PATH", str(tmp_path / "missing.config"))
    args = types.SimpleNamespace(client_uuid="arg-client-uuid")
    cfg = _resolve_config(args=args)
    assert cfg["client_uuid"] == "arg-client-uuid"


def test_build_client_pins_callback_api_version_v2(monkeypatch):
    import paho.mqtt.client as mqtt

    captured = {}
    real_client_cls = mqtt.Client

    def spy_client(*args, **kwargs):
        captured.update(kwargs)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(mqtt, "Client", spy_client)

    cfg = {
        "client_id": "test-client",
        "transport": "tcp",
        "protocol": "mqtt.MQTTv5",
    }
    CloudMQTTClient(cfg)

    assert captured.get(
        "callback_api_version") == mqtt.CallbackAPIVersion.VERSION2


def test_on_connect_uses_v2_five_arg_signature():
    cfg = {
        "client_id": "test-client",
        "transport": "tcp",
        "protocol": "mqtt.MQTTv5"}
    client = CloudMQTTClient(cfg)

    # v2 callback signature: (client, userdata, flags, reason_code, properties)
    client._on_connect(None, None, {}, 0, None)
    assert client.is_connected is True

    client._on_disconnect(None, None, {}, 0, None)
    assert client.is_connected is False
