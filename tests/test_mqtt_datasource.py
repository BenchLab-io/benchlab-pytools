"""Non-hardware regression tests for benchlab.core.datasource.MQTTDataSource.

These tests exercise MQTTDataSource's message-parsing and protocol
resolution logic directly — no real MQTT broker or BenchLab hardware is
required (unlike tests/test_data_sources.py::TestMQTTSource, which needs
both and is excluded from CI). Cover the topic-prefix bug found while
auditing MQTT test coverage: _on_message filtered incoming messages
against a hardcoded "benchlab" literal instead of the configurable
self.topic_prefix, silently dropping every message whenever a non-default
MQTT_TOPIC_PREFIX was set.
"""

import json

import pytest

from benchlab.core.datasource import MQTTDataSource, resolve_mqtt_protocol

try:
    import paho.mqtt.client as mqtt
    HAS_PAHO = True
except ImportError:
    mqtt = None
    HAS_PAHO = False

pytestmark = pytest.mark.skipif(not HAS_PAHO, reason="paho-mqtt not available")


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")


def test_on_message_default_topic_prefix():
    ds = MQTTDataSource(broker="localhost")
    ds._on_message(None, None, FakeMsg(
        "benchlab/UID1/telemetry", {"temp": 42.0}))
    assert ds._latest_data["UID1"] == {"temp": 42.0}


def test_on_message_custom_topic_prefix():
    """Regression test: _on_message used to hardcode 'benchlab' as the
    prefix check regardless of self.topic_prefix, so a custom prefix
    silently dropped every message."""
    ds = MQTTDataSource(broker="localhost", topic_prefix="custom-prefix")
    ds._on_message(None, None, FakeMsg(
        "custom-prefix/UID2/telemetry", {"temp": 10.0}))
    assert ds._latest_data["UID2"] == {"temp": 10.0}


def test_on_message_info_payload():
    ds = MQTTDataSource(broker="localhost", topic_prefix="custom-prefix")
    ds._on_message(None, None, FakeMsg(
        "custom-prefix/UID3/info", {"firmware": "1.2.3"}))
    assert ds._device_info["UID3"] == {"firmware": "1.2.3"}


def test_on_message_ignores_mismatched_prefix():
    ds = MQTTDataSource(broker="localhost", topic_prefix="custom-prefix")
    ds._on_message(None, None, FakeMsg(
        "wrong-prefix/UID4/telemetry", {"temp": 1.0}))
    assert "UID4" not in ds._latest_data


def test_on_message_ignores_malformed_topic():
    ds = MQTTDataSource(broker="localhost")
    ds._on_message(None, None, FakeMsg("benchlab/onlytwo", {"temp": 1.0}))
    assert ds._latest_data == {}


def test_on_message_does_not_raise_on_bad_json():
    ds = MQTTDataSource(broker="localhost")

    class BadMsg:
        topic = "benchlab/UID5/telemetry"
        payload = b"not json"

    ds._on_message(None, None, BadMsg())  # must not raise
    assert "UID5" not in ds._latest_data


# ----------------------------------------------------------------------
# Protocol resolution
# ----------------------------------------------------------------------

def test_protocol_defaults_to_v311_when_unset(monkeypatch):
    monkeypatch.delenv("MQTT_PROTOCOL", raising=False)
    ds = MQTTDataSource(broker="localhost")
    assert resolve_mqtt_protocol(ds._protocol_setting, mqtt) == mqtt.MQTTv311


def test_protocol_reads_env_var(monkeypatch):
    monkeypatch.setenv("MQTT_PROTOCOL", "MQTTv5")
    ds = MQTTDataSource(broker="localhost")
    assert resolve_mqtt_protocol(ds._protocol_setting, mqtt) == mqtt.MQTTv5


def test_protocol_explicit_param_overrides_env(monkeypatch):
    monkeypatch.setenv("MQTT_PROTOCOL", "MQTTv5")
    ds = MQTTDataSource(broker="localhost", protocol="v3.1")
    assert resolve_mqtt_protocol(ds._protocol_setting, mqtt) == mqtt.MQTTv31


def test_protocol_invalid_setting_raises_on_connect_attempt():
    ds = MQTTDataSource(broker="localhost", protocol="garbage")
    with pytest.raises(ValueError, match="Unrecognized MQTT_PROTOCOL"):
        resolve_mqtt_protocol(ds._protocol_setting, mqtt)
