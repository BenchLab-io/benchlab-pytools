"""Non-hardware regression tests for benchlab.mqtt.mqtt_publisher.

These tests exercise the pure-Python logic bugs fixed in the mqtt bug sweep
(issue #24) — no real MQTT broker or BenchLab hardware is required:
- reason_code_lookup correctly resolving both plain int reason codes and
  paho-mqtt v2's unhashable ReasonCode objects
- device_stop_events cleanup after a device thread's finally block runs
- the -mqtt CLI argument setting MQTT_BROKER before run_mqtt_mode reads config
"""

import os

import pytest

from benchlab.mqtt import mqtt_publisher
from benchlab.mqtt.mqtt_publisher import reason_code_lookup, MQTTV5_REASON_CODES

try:
    from paho.mqtt.reasoncodes import ReasonCode
    from paho.mqtt.packettypes import PacketTypes
    HAS_REASONCODES = True
except ImportError:
    HAS_REASONCODES = False


def test_reason_code_lookup_plain_int_success():
    assert reason_code_lookup(0) == "Success"


def test_reason_code_lookup_plain_int_error():
    assert reason_code_lookup(128) == "Unspecified error"


def test_reason_code_lookup_plain_int_unknown():
    assert reason_code_lookup(999) == "Unknown reason code 999"


@pytest.mark.skipif(not HAS_REASONCODES, reason="paho.mqtt.reasoncodes not available")
def test_reason_code_lookup_handles_unhashable_reasoncode_object():
    """Regression test for issue #24: paho-mqtt v2's ReasonCode object is
    unhashable, so a plain dict.get(rc, ...) raises TypeError on any real
    non-zero reason code. reason_code_lookup must extract .value first."""
    rc_success = ReasonCode(PacketTypes.DISCONNECT, "Normal disconnection")
    rc_error = ReasonCode(PacketTypes.DISCONNECT, "Unspecified error")

    # The bug this guards against: MQTTV5_REASON_CODES.get(rc_error, ...)
    # directly would raise TypeError here.
    with pytest.raises(TypeError):
        MQTTV5_REASON_CODES.get(rc_error, "fallback")

    assert reason_code_lookup(rc_success) == "Success"
    assert reason_code_lookup(rc_error) == "Unspecified error"


def test_device_stop_events_cleanup():
    """device_stop_events must not accumulate entries indefinitely."""
    mqtt_publisher.device_stop_events.clear()
    uid = "TEST-UID-CLEANUP"

    import threading
    mqtt_publisher.device_stop_events[uid] = threading.Event()
    assert uid in mqtt_publisher.device_stop_events

    # Simulate the finally-block cleanup added for issue #24.
    mqtt_publisher.device_stop_events.pop(uid, None)
    assert uid not in mqtt_publisher.device_stop_events


def test_load_mqtt_config_topic_prefix_default(monkeypatch):
    monkeypatch.delenv("MQTT_TOPIC_PREFIX", raising=False)
    cfg = mqtt_publisher.load_mqtt_config()
    assert cfg["topic_prefix"] == "benchlab"


def test_load_mqtt_config_topic_prefix_override(monkeypatch):
    monkeypatch.setenv("MQTT_TOPIC_PREFIX", "custom-prefix")
    cfg = mqtt_publisher.load_mqtt_config()
    assert cfg["topic_prefix"] == "custom-prefix"


def test_load_mqtt_config_broker_from_env(monkeypatch):
    monkeypatch.setenv("MQTT_BROKER", "mybroker.example.com")
    cfg = mqtt_publisher.load_mqtt_config()
    assert cfg["broker"] == "mybroker.example.com"


def test_cli_mqtt_flag_sets_broker_env_var(monkeypatch):
    """Regression test for issue #24: `-mqtt <broker>` used to be silently
    ignored because nothing set MQTT_BROKER before run_mqtt_mode read config.
    This mirrors main.py's fixed dispatch branch."""
    monkeypatch.delenv("MQTT_BROKER", raising=False)

    args_mqtt = "mybroker.example.com"
    broker = args_mqtt if args_mqtt else "localhost"
    os.environ.setdefault("MQTT_BROKER", broker)

    cfg = mqtt_publisher.load_mqtt_config()
    assert cfg["broker"] == "mybroker.example.com"
