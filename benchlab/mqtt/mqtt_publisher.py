"""
MQTT client for BENCHLAB telemetry
"""

import configparser
import json
import logging
import os
import paho.mqtt.client as mqtt
import sys
import threading
import time

from benchlab_pycore.core import (
    translate_sensor_struct,
    read_sensors,
    read_device,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
)
from benchlab_pycore.core.serial_io import get_fleet_info

# Import DeviceRegistry so the MQTT publisher publishes device lifecycle events
from benchlab.core.device_registry import DeviceRegistry
# benchlab_pycore.core.serial_io has no connection-opening helper; use the
# local wrapper instead (see benchlab.core.shared_serial).
from benchlab.core.shared_serial import open_serial_connection
# Shared with benchlab.core.datasource.MQTTDataSource so both the publisher
# and consumer sides resolve MQTT_PROTOCOL the same way.
from benchlab.core.datasource import (
    resolve_mqtt_protocol as _resolve_mqtt_protocol,
)

MQTTV5_REASON_CODES = {
    0: "Success",
    128: "Unspecified error",
    129: "Malformed packet",
    130: "Protocol error",
    131: "Implementation specific error",
    132: "Unsupported protocol version",
    133: "Client identifier not valid",
    134: "Bad username or password",
    135: "Not authorized",
    136: "Server unavailable",
    137: "Server busy",
    138: "Banned",
    140: "Topic name invalid",
    143: "Packet too large",
    144: "Quota exceeded",
    149: "Connection rate exceeded",
}

# --- Logger setup ---


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            log_record.update(record.extra_data)
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logger = logging.getLogger("mqtt_publisher")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S")
handler.setFormatter(formatter)
logger.addHandler(handler)

# Graceful shutdown flags (QUAL-3.1: per-device stop events)
global_stop_event = threading.Event()
device_stop_events = {}  # {uid: threading.Event}


def resolve_mqtt_protocol(value):
    """Resolve *value* to one of paho's MQTTv31/MQTTv311/MQTTv5 int constants.

    Thin wrapper around benchlab.core.datasource.resolve_mqtt_protocol,
    kept here (with this module's paho import already available) so both
    the publisher and MQTTDataSource share one resolver implementation.
    See that function's docstring for accepted value formats.
    """
    return _resolve_mqtt_protocol(value, mqtt)


def load_local_config(filename="mqtt.config"):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, filename)
    cfg = configparser.ConfigParser()
    if os.path.exists(path):
        cfg.read(path)
        try:
            poll_rate = float(cfg.get("settings", "poll_rate", fallback="1"))
        except ValueError:
            poll_rate = 1
    else:
        poll_rate = 1  # default if file missing
    return poll_rate


def load_mqtt_config():
    poll_rate = load_local_config()

    return {
        "broker": os.getenv("MQTT_BROKER", "localhost"),
        "port": int(os.getenv("MQTT_PORT", 1883)),
        # default tcp, can override
        "transport": os.getenv("MQTT_TRANSPORT", "tcp"),
        "username": os.getenv("MQTT_USERNAME"),
        "password": os.getenv("MQTT_PASSWORD"),
        "protocol": resolve_mqtt_protocol(
            os.getenv("MQTT_PROTOCOL", mqtt.MQTTv311)),
        "qos": int(os.getenv("MQTT_QOS", 0)),
        "path": os.getenv("MQTT_PATH"),
        "poll_rate": float(os.getenv("MQTT_POLL_RATE", poll_rate)),
        # QUAL-3.2 fix: Make topic prefix configurable
        "topic_prefix": os.getenv("MQTT_TOPIC_PREFIX", "benchlab")
    }


def map_sensors_to_payload(sensor_struct, timestamp):
    """
    Converts a SensorStruct into a JSON-ready dict for MQTT.
    Uses translate_sensor_struct to safely extract values.
    """
    try:
        payload = translate_sensor_struct(sensor_struct)
        payload["timestamp"] = timestamp
        logger.debug("Translated payload: %s", payload)
        return payload
    except Exception as e:
        logger.error("Failed to translate sensor_struct: %s", e)
        return None


def reason_code_lookup(rc):
    """Resolve an MQTTV5 reason string for *rc*.

    paho-mqtt's v2 callback API passes a ReasonCode object (unhashable, but
    comparable to int) rather than a plain int, so dict lookups must key off
    its .value instead of the object itself.
    """
    rc_value = getattr(rc, "value", rc)
    return MQTTV5_REASON_CODES.get(rc_value, f"Unknown reason code {rc_value}")


def mqtt_publish(client, topic, payload, qos=0, retain=False):
    """
    Publish payload to MQTT topic if payload is not empty.
    Returns MQTTMessageInfo or None if skipped.
    """
    if not payload:  # covers None or empty dict/list
        return None

    try:
        json_payload = json.dumps(payload)

        logger.debug("Publishing to %s: %s", topic, json_payload)

        result = client.publish(
            topic,
            json_payload.encode("utf-8"),
            qos=qos,
            retain=retain)

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            # Look up reason if using MQTTv5
            reason_str = MQTTV5_REASON_CODES.get(result.rc, "Unknown reason")
            logger.warning(
                "Publish failed to topic %s, rc=%s (%s)",
                topic,
                result.rc,
                reason_str)

        return result

    except Exception as e:
        logger.error("Failed to publish to topic %s: %s", topic, e)
        return None

# Main MQTT loop


def device_thread(device, cfg, publish_interval=1):
    port = device["port"]
    uid = device["uid"]
    client_id = port.replace(":", "_")
    qos = cfg["qos"]

    # Create MQTT client - paho-mqtt v2.x compatible
    # In v2+, client_id is passed via callback_api_version parameter
    try:
        # Try v2.x API first
        from paho.mqtt.enums import CallbackAPIVersion
        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=cfg["protocol"],
            transport=cfg["transport"]
        )
        use_v2_api = True
    except (ImportError, TypeError):
        # Fall back to v1.x API
        client = mqtt.Client(
            client_id=client_id,
            protocol=cfg["protocol"],
            transport=cfg["transport"]
        )
        use_v2_api = False

    if cfg["username"] and cfg["password"]:
        client.username_pw_set(cfg["username"], cfg["password"])
    if cfg["port"] in (443, 8084, 8883, 8884):
        client.tls_set()
    if cfg.get("path"):
        client.ws_set_options(path=cfg["path"])

    client.connected_flag = False

    # Store client_id locally for logging (v2.x removed _client_id attribute)
    local_client_id = client_id

    # MQTT callbacks - v2.x has different signature
    if use_v2_api:
        def on_connect(c, userdata, flags, rc, properties=None):
            if rc == 0:
                logger.info("MQTT client %s connected", local_client_id)
                c.connected_flag = True
            else:
                reason = reason_code_lookup(rc)
                logger.error(
                    "MQTT client %s failed to connect: rc=%s (%s)",
                    local_client_id,
                    rc,
                    reason,
                )

        def on_disconnect(c, userdata, flags, reason_code, properties=None):
            rc = reason_code if reason_code else 0
            if rc == 0:
                logger.info(
                    "MQTT client %s disconnected cleanly",
                    local_client_id)
            else:
                reason = reason_code_lookup(rc)
                logger.warning(
                    "MQTT client %s disconnected unexpectedly: rc=%s (%s)",
                    local_client_id,
                    rc,
                    reason,
                )
            c.connected_flag = False
    else:
        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                logger.info("MQTT client %s connected", local_client_id)
                c.connected_flag = True
            else:
                reason = reason_code_lookup(rc)
                logger.error(
                    "MQTT client %s failed to connect: rc=%s (%s)",
                    local_client_id,
                    rc,
                    reason,
                )

        def on_disconnect(c, userdata, rc):
            if rc == 0:
                logger.info(
                    "MQTT client %s disconnected cleanly",
                    local_client_id)
            else:
                reason = reason_code_lookup(rc)
                logger.warning(
                    "MQTT client %s disconnected unexpectedly: rc=%s (%s)",
                    local_client_id,
                    rc,
                    reason,
                )
            c.connected_flag = False

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    # on_publish callback for v2.x has 5 positional args
    if use_v2_api:
        client.on_publish = lambda c, u, mid, reason_code, props: None
    else:
        client.on_publish = lambda c, u, mid: None

    # paho-mqtt v2.x uses connect() instead of connect_async()
    try:
        client.connect(cfg["broker"], cfg["port"])
    except Exception as e:
        logger.warning("Failed to connect to MQTT broker initially: %s", e)
    client.loop_start()

    # Per-device stop event (QUAL-3.1)
    device_stop_event = threading.Event()
    device_stop_events[uid] = device_stop_event

    # Wait until connected (non-blocking, allows graceful shutdown)
    while not client.connected_flag and not global_stop_event.is_set(
    ) and not device_stop_event.is_set():
        time.sleep(0.5)

    # Serial connection loop
    ser = None
    retry_count = 0
    max_retries = 10

    try:
        while (not global_stop_event.is_set()
               and not device_stop_event.is_set()):
            if ser is None:
                try:
                    ser = open_serial_connection(port)
                    retry_count = 0
                except OSError as e:
                    # open_serial_connection raises OSError on failure; we no
                    # longer depend on serial.SerialException
                    logger.warning(
                        "Failed to open serial port %s: %s", port, e)
                    retry_count += 1
                    if retry_count >= max_retries:
                        logger.error(
                            "Too many failed attempts for %s, "
                            "stopping thread.", uid)
                        break
                    time.sleep(1)
                    continue

                # Send initial info payload (retain=True so late subscribers
                # like the TUI can discover this device)
                info_payload = {
                    "uid": uid,
                    "com_port": port,
                    "firmware": device.get("firmware")}
                topic_info = f"{cfg['topic_prefix']}/{uid}/info"
                mqtt_publish(
                    client,
                    topic_info,
                    info_payload,
                    qos=qos,
                    retain=True)

                # Register device in the DeviceRegistry so tools can discover
                # it
                registry = DeviceRegistry.get_instance()
                registry.register(
                    uid=uid,
                    port=port,
                    firmware=str(device.get("firmware", "?")),
                    data_source="mqtt",
                )

            # Read sensors and publish telemetry
            try:
                # Get product_id for correct sensor interpretation (BL2 vs
                # ORIGINAL)
                product_id = BENCHLAB_ORIGINAL_PRODUCT_ID
                try:
                    device_info = read_device(ser)
                    if device_info:
                        product_id = device_info.get(
                            'ProductId', BENCHLAB_ORIGINAL_PRODUCT_ID)
                except Exception:
                    pass
                sensors = read_sensors(ser, product_id=product_id)
                payload = map_sensors_to_payload(
                    sensors, int(time.time() * 1000))
                topic_telemetry = f"{cfg['topic_prefix']}/{uid}/telemetry"
                result = mqtt_publish(
                    client, topic_telemetry, payload, qos=qos)

                if result and payload:  # only log if a payload was sent
                    payload_size = len(json.dumps(payload))
                    logger.debug(
                        "%s payload sent: %d bytes", uid, payload_size)

                retry_count = 0
                time.sleep(publish_interval)

            except OSError as e:
                # Serial connection errors surface as OSError from
                # open_serial_connection/read_sensors
                logger.warning("Lost connection to %s: %s", uid, e)
                try:
                    if ser:
                        ser.close()
                except Exception:
                    pass
                ser = None

                # Wait a moment before rescanning (allow /dev to settle)
                time.sleep(0.5)

                # Attempt to rescan for devices and see if this UID still
                # exists
                try:
                    current_fleet = get_fleet_info()
                    uids = [d["uid"] for d in current_fleet]
                    if uid not in uids:
                        logger.info(
                            "Device %s removed from fleet, "
                            "stopping thread.", uid)
                        break  # exit device thread gracefully
                    else:
                        logger.info(
                            "Device %s still detected, "
                            "retrying connection...", uid)
                except Exception as scan_err:
                    logger.error("Rescan failed: %s", scan_err)

                retry_count += 1
                # Cap retry_count to prevent overflow and use exponential
                # backoff
                capped_retry = min(retry_count, 5)
                time.sleep(min(2 ** capped_retry, 30))

            except Exception as e:
                logger.error("Unexpected error for %s: %s", uid, e)
                time.sleep(1)

    # Clean up
    finally:
        # Unregister device from the DeviceRegistry
        registry = DeviceRegistry.get_instance()
        registry.unregister(uid)

        # Remove this device's stop event so device_stop_events doesn't grow
        # unbounded across reconnects/hot-plug cycles.
        device_stop_events.pop(uid, None)

        logger.info("Stopping MQTT client for %s (%s)", uid, port)
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        client.loop_stop()
        client.disconnect()
        logger.info("MQTT client %s disconnected.", uid)


def log_connected_devices_periodically(fleet, interval=30):
    """
    Periodically logs all devices in fleet.
    """
    while not global_stop_event.is_set():
        device_list = ", ".join(f"{d['port']} {d['uid']}" for d in fleet)
        logger.info("Connected devices: %s", device_list)
        time.sleep(interval)


def run_mqtt_mode(broker_type="localhost"):
    fleet = get_fleet_info()
    if not fleet:
        logger.error("No Benchlab devices found.")
        return

    cfg = load_mqtt_config()

    logger.info(
        "MQTT mode: %s, sending to %s:%s",
        broker_type,
        cfg['broker'],
        cfg['port'])
    logger.info("Using poll_rate = %s seconds", cfg["poll_rate"])

    threads = []
    for device in fleet:
        t = threading.Thread(
            target=device_thread,
            args=(device, cfg, cfg["poll_rate"]),
            daemon=True
        )
        t.start()
        threads.append(t)

    # Start periodic logging thread
    # The periodic log thread sleeps for a relatively long interval
    # (default 30 s).
    # Store the interval so we can use an appropriate join timeout during
    # shutdown.
    log_interval = 30
    log_thread = threading.Thread(
        target=log_connected_devices_periodically,
        args=(fleet, log_interval),
        daemon=True,
    )
    log_thread.start()

    # Helper to join threads with a timeout and log if they do not finish.
    def safe_join(thread: threading.Thread, timeout: float = 5.0) -> None:
        """Join *thread* with *timeout* seconds.

        If the thread is still alive after the timeout, a warning is logged
        and the function returns, allowing shutdown to continue.
        """
        try:
            thread.join(timeout)
        except Exception as e:
            logger.error("Error while joining thread %s: %s", thread.name, e)
        if thread.is_alive():
            logger.warning(
                "Thread %s did not terminate within %.1f seconds",
                thread.name,
                timeout)

    # Wait until Ctrl+C. The outer try/except ensures a second interrupt during
    # shutdown does not produce a second traceback.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        global_stop_event.set()
        # Signal all device threads to stop (QUAL-3.1)
        for dev_stop in device_stop_events.values():
            dev_stop.set()
        # Join device threads with timeout protection.
        for t in threads:
            safe_join(t, timeout=5.0)
        # Join the periodic log thread with a timeout longer than its sleep
        # interval.
        safe_join(log_thread, timeout=log_interval + 5.0)
    # Ensure any stray KeyboardInterrupts during the cleanup are ignored.
    except KeyboardInterrupt:
        logger.info("Forced shutdown during cleanup.")
