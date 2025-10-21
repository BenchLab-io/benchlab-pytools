"""
MQTT client for BENCHLAB telemetry
"""

import os
import json
import time
import serial
import threading
import logging
import sys
import paho.mqtt.client as mqtt
from benchlab.core.serial_io import get_fleet_info, open_serial_connection, read_sensors
from benchlab.core.sensor_translation import translate_sensor_struct

MQTTV5_REASON_CODES = {
    0:  "Success",
    128:"Unspecified error",
    129:"Malformed packet",
    130:"Protocol error",
    131:"Implementation specific error",
    132:"Unsupported protocol version",
    133:"Client identifier not valid",
    134:"Bad username or password",
    135:"Not authorized",
    136:"Server unavailable",
    137:"Server busy",
    138:"Banned",
    140:"Topic name invalid",
    143:"Packet too large",
    144:"Quota exceeded",
    149:"Connection rate exceeded",
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
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
handler.setFormatter(formatter)
logger.addHandler(handler)

# Graceful shutdown flag
stop_event = threading.Event()

def load_mqtt_config():
    return {
        "broker": os.getenv("MQTT_BROKER", "localhost"),
        "port": int(os.getenv("MQTT_PORT", 1883)),
        "transport": os.getenv("MQTT_TRANSPORT", "tcp"),   # default tcp, can override
        "username": os.getenv("MQTT_USERNAME"),
        "password": os.getenv("MQTT_PASSWORD"),
        "protocol": os.getenv("MQTT_PROTOCOL",mqtt.MQTTv311),
        "qos": int(os.getenv("MQTT_QOS", 0)),
        "path": os.getenv("MQTT_PATH"),
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

        result = client.publish(topic, json_payload.encode("utf-8"), qos=qos, retain=retain)

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            # Look up reason if using MQTTv5
            reason_str = MQTTV5_REASON_CODES.get(result.rc, "Unknown reason")
            logger.warning(
                "Publish failed to topic %s, rc=%s (%s)", topic, result.rc, reason_str
            )

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

    # Create MQTT client
    client = mqtt.Client(client_id=client_id, protocol=cfg["protocol"], transport=cfg["transport"])
    if cfg["username"] and cfg["password"]:
        client.username_pw_set(cfg["username"], cfg["password"])
    if cfg["port"] in (443, 8084, 8883, 8884):
        client.tls_set()
    if cfg.get("path"):
        client.ws_set_options(path=cfg["path"])

    client.connected_flag = False

    # MQTT callbacks
    def on_connect(c, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("MQTT client %s connected", c._client_id.decode())
            c.connected_flag = True
        else:
            reason = MQTTV5_REASON_CODES.get(rc, f"Unknown reason code {rc}")
            logger.error(
                "MQTT client %s failed to connect: rc=%s (%s)",
                c._client_id.decode(),
                rc,
                reason,
            )

    def on_disconnect(c, userdata, rc, properties=None):
        if rc == 0:
            logger.info("MQTT client %s disconnected cleanly", c._client_id.decode())
        else:
            reason = MQTTV5_REASON_CODES.get(rc, f"Unknown reason code {rc}")
            logger.warning(
                "MQTT client %s disconnected unexpectedly: rc=%s (%s)",
                c._client_id.decode(),
                rc,
                reason,
            )
        c.connected_flag = False

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = lambda c, u, mid: None  # optional debug

    client.connect_async(cfg["broker"], cfg["port"])
    client.loop_start()

    # Wait until connected (non-blocking, allows graceful shutdown)
    while not client.connected_flag and not stop_event.is_set():
        time.sleep(0.5)

    # Serial connection loop
    ser = None
    retry_count = 0
    max_retries = 10

    try:
        while not stop_event.is_set():
            if ser is None:
                try:
                    ser = open_serial_connection(port)
                    retry_count = 0
                except (serial.SerialException, OSError) as e:
                    logger.warning("Failed to open serial port %s: %s", port, e)
                    retry_count += 1
                    if retry_count >= max_retries:
                        logger.error("Too many failed attempts for %s, stopping thread.", uid)
                        break
                    time.sleep(1)
                    continue

                # Send initial info payload
                info_payload = {"uid": uid, "com_port": port, "firmware": device.get("firmware")}
                topic_info = f"clients/client_uuid/links/link_uuid/benchlabs/{uid}/info"
                mqtt_publish(client, topic_info, info_payload, qos=qos)

            # Read sensors and publish telemetry
            try:
                sensors = read_sensors(ser)
                payload = map_sensors_to_payload(sensors, time.time())
                topic_telemetry = f"clients/client_uuid/links/link_uuid/benchlabs/{uid}/telemetry"
                result = mqtt_publish(client, topic_telemetry, payload, qos=qos)

                if result and payload:  # only log if a payload was actually sent
                    payload_size = len(json.dumps(payload))
                    logger.info("%s payload sent: %d bytes", uid, payload_size)

                retry_count = 0
                time.sleep(publish_interval) 

            except (serial.SerialException, OSError) as e:
                logger.warning("Lost connection to %s: %s", uid, e)
                try:
                    if ser:
                        ser.close()
                except Exception:
                    pass
                ser = None

                # Wait a moment before rescanning (allow /dev to settle)
                time.sleep(0.5)

                # Attempt to rescan for devices and see if this UID still exists
                try:
                    current_fleet = get_fleet_info()
                    uids = [d["uid"] for d in current_fleet]
                    if uid not in uids:
                        logger.info("Device %s removed from fleet, stopping thread.", uid)
                        break  # exit device thread gracefully
                    else:
                        logger.info("Device %s still detected, retrying connection...", uid)
                except Exception as scan_err:
                    logger.error("Rescan failed: %s", scan_err)

                retry_count += 1
                time.sleep(min(2 ** retry_count, 30))

            except Exception as e:
                logger.error("Unexpected error for %s: %s", uid, e)
                time.sleep(1)

    # Clean up
    finally:
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
    while not stop_event.is_set():
        device_list = ", ".join(f"{d['port']} {d['uid']}" for d in fleet)
        logger.info("Connected devices: %s", device_list)
        time.sleep(interval)

def run_mqtt_mode(broker_type="localhost"):
    fleet = get_fleet_info()
    if not fleet:
        logger.error("No Benchlab devices found.")
        return

    cfg = load_mqtt_config()
    logger.info("MQTT mode: %s, sending to %s:%s", broker_type, cfg['broker'], cfg['port'])

    threads = []
    for device in fleet:
        t = threading.Thread(
            target=device_thread, 
            args=(device, cfg, 1), 
            daemon=True
        )
        t.start()
        threads.append(t)

    # Start periodic logging thread
    log_thread = threading.Thread(
        target=log_connected_devices_periodically, 
        args=(fleet, 30), 
        daemon=True
    )
    log_thread.start()

    # Wait until Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stop_event.set()
        for t in threads:
            t.join()
        log_thread.join()