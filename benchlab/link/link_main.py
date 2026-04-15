# benchlab/link/link_main.py
"""
Benchlab Link — cloud MQTT publisher.

Reads telemetry from any DataSourceManager source (direct / fastapi / mqtt)
and publishes JSON payloads to a remote (cloud) MQTT broker.

Configuration is loaded from environment variables first, with a JSON config
file as fallback.  Any env var overrides the corresponding file value.

Environment variables
---------------------
LINK_REMOTE_HOST      Cloud MQTT broker hostname
LINK_REMOTE_PORT      Cloud MQTT broker port          (default: 8883)
LINK_REMOTE_USER      MQTT username
LINK_REMOTE_PASS      MQTT password
LINK_REMOTE_TLS       Enable TLS: "true" / "false"    (default: true)
LINK_TOPIC_PATTERN    Topic pattern with {uid} token   (default: benchlab/{uid}/telemetry)
LINK_PUBLISH_INTERVAL Publish interval in seconds      (default: 1.0)
LINK_CLIENT_ID        MQTT client ID                   (default: benchlab-link-<hostname>)

Config file
-----------
Loaded from LINK_CONFIG_PATH env var or benchlab/link/link.config by default.
Keys match env var names in lowercase (without the LINK_ prefix):
  remote_host, remote_port, remote_user, remote_pass, remote_tls,
  topic_pattern, publish_interval, client_id
"""

import json
import logging
import os
import platform
import socket
import threading
import time
import types
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from benchlab.core.datasource_manager import DataSourceManager

logger = logging.getLogger("benchlab.link")

DEFAULT_CONFIG_PATH = Path(__file__).parent / "link.config"
DEFAULT_TOPIC       = "benchlab/{uid}/telemetry"
DEFAULT_PORT        = 8883
DEFAULT_INTERVAL    = 1.0


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(config_path: Optional[Path] = None) -> dict:
    """Load config file, return empty dict on failure."""
    path = config_path or Path(
        os.environ.get("LINK_CONFIG_PATH", str(DEFAULT_CONFIG_PATH))
    )
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load link config from {path}: {e}")
    return {}


def _resolve_config(args=None) -> dict:
    """Merge config file values with env var overrides.

    Priority (highest first): args > env vars > config file > defaults.
    """
    file_cfg = _load_config()

    def _get(env_key: str, file_key: str, default):
        # args namespace takes priority if the attribute is set
        if args is not None:
            val = getattr(args, file_key.replace("-", "_"), None)
            if val is not None:
                return val
        # env var next
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val
        # config file
        if file_key in file_cfg:
            return file_cfg[file_key]
        return default

    host     = _get("LINK_REMOTE_HOST",      "remote_host",      None)
    port     = int(_get("LINK_REMOTE_PORT",  "remote_port",      DEFAULT_PORT))
    user     = _get("LINK_REMOTE_USER",      "remote_user",      None)
    password = _get("LINK_REMOTE_PASS",      "remote_pass",      None)
    tls_raw  = _get("LINK_REMOTE_TLS",       "remote_tls",       "true")
    tls      = str(tls_raw).lower() not in ("false", "0", "no")
    topic    = _get("LINK_TOPIC_PATTERN",    "topic_pattern",    DEFAULT_TOPIC)
    interval = float(_get("LINK_PUBLISH_INTERVAL", "publish_interval", DEFAULT_INTERVAL))
    hostname = socket.gethostname()
    client_id = _get("LINK_CLIENT_ID",       "client_id",
                     f"benchlab-link-{hostname}")

    return {
        "host":      host,
        "port":      port,
        "user":      user,
        "password":  password,
        "tls":       tls,
        "topic":     topic,
        "interval":  interval,
        "client_id": client_id,
    }


# ---------------------------------------------------------------------------
# Cloud MQTT client
# ---------------------------------------------------------------------------

class CloudMQTTClient:
    """Thin wrapper around paho-mqtt for publishing to a remote broker."""

    def __init__(self, cfg: dict):
        self.cfg      = cfg
        self._client  = mqtt.Client(client_id=cfg["client_id"],
                                    transport="websockets",
                                    protocol=mqtt.MQTTv311)
        self._client.ws_set_options(path="/mqtt")
        self._connected = False
        self._lock      = threading.Lock()

        if cfg.get("user"):
            self._client.username_pw_set(cfg["user"], cfg.get("password"))

        if cfg.get("tls"):
            self._client.tls_set()   # uses system CA bundle by default

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def connect(self) -> bool:
        host = self.cfg.get("host")
        if not host:
            logger.error("LINK_REMOTE_HOST is not configured — cannot connect")
            return False
        try:
            self._client.connect(host, self.cfg["port"], keepalive=60)
            self._client.loop_start()
            # Give the broker a moment to accept the connection
            deadline = time.time() + 10
            while not self._connected and time.time() < deadline:
                time.sleep(0.1)
            if not self._connected:
                logger.error(f"Timed out connecting to {host}:{self.cfg['port']}")
                return False
            logger.info(f"Connected to cloud broker {host}:{self.cfg['port']}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to cloud broker: {e}")
            return False

    def disconnect(self):
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: dict) -> bool:
        if not self._connected:
            logger.warning("Not connected — skipping publish")
            return False
        try:
            result = self._client.publish(
                topic, json.dumps(payload), qos=1, retain=False
            )
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Publish failed: {e}")
            return False

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            with self._lock:
                self._connected = True
            logger.info("Cloud broker: connection established")
        else:
            logger.error(f"Cloud broker: connection refused (rc={rc})")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        with self._lock:
            self._connected = False
        if rc != 0:
            logger.warning(f"Cloud broker: unexpected disconnect (rc={rc}) — will reconnect")


# ---------------------------------------------------------------------------
# Link worker
# ---------------------------------------------------------------------------

class BenchlabLink:
    """Reads telemetry from DataSourceManager and publishes to cloud MQTT."""

    def __init__(self, datasource: DataSourceManager,
                 cloud: CloudMQTTClient, cfg: dict):
        self.datasource   = datasource
        self.cloud        = cloud
        self.cfg          = cfg
        self._stop        = threading.Event()
        self._snapshots   = {}   # uid → latest telemetry dict
        self._snap_lock   = threading.Lock()
        self._worker      = threading.Thread(
            target=self._poll_loop, daemon=True, name="LinkPoller"
        )

    def start(self):
        self._worker.start()
        logger.info("Link poller started")

    def stop(self):
        self._stop.set()
        self._worker.join(timeout=5)

    def _poll_loop(self):
        """Background thread: keeps telemetry snapshots fresh."""
        while not self._stop.is_set():
            try:
                raw = self.datasource.list_devices()
                uids = list(raw.keys()) if isinstance(raw, dict) \
                    else [d.get("uid") for d in raw if d.get("uid")]

                for uid in uids:
                    try:
                        self.datasource.select_device(uid)
                        snap = self.datasource.snapshot()
                        data = (snap.get("sensor_data")
                                or snap.get("all_telemetry", {}).get(uid)
                                or {})
                        if data:
                            with self._snap_lock:
                                self._snapshots[uid] = data
                    except Exception as e:
                        logger.debug(f"Poll error for {uid}: {e}")

            except Exception as e:
                logger.warning(f"Device list error: {e}")

            self._stop.wait(self.cfg["interval"])

    def publish_all(self):
        """Publish latest snapshot for every known device."""
        topic_pattern = self.cfg["topic"]
        with self._snap_lock:
            snapshots = dict(self._snapshots)

        published = 0
        for uid, data in snapshots.items():
            if not data:
                continue
            topic   = topic_pattern.format(uid=uid)
            payload = {"uid": uid, **data}
            if self.cloud.publish(topic, payload):
                published += 1
                logger.debug(f"Published {len(data)} sensors to {topic}")
            else:
                logger.warning(f"Failed to publish for {uid}")

        return published


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_link(args=None):
    """Run the Benchlab Link cloud publisher.

    Parameters
    ----------
    args:
        Standard benchlab args namespace.  Fields used:
            source      – "direct" | "fastapi" | "mqtt"
            interval    – poll/publish interval in seconds
            api_url     – FastAPI base URL (fastapi source)
            mqtt_broker – local MQTT broker host (mqtt source)
            mqtt_port   – local MQTT broker port (mqtt source)

        Link-specific fields (optional, fall back to env vars / config file):
            remote_host      – cloud MQTT broker hostname
            remote_port      – cloud MQTT broker port
            remote_user      – cloud MQTT username
            remote_pass      – cloud MQTT password
            remote_tls       – enable TLS ("true"/"false")
            topic_pattern    – MQTT topic pattern with {uid}
            publish_interval – publish interval in seconds
            client_id        – MQTT client ID
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args is None:
        args = types.SimpleNamespace(
            source=os.environ.get("BENCHLAB_DATA_SOURCE", "direct"),
            interval=float(os.environ.get("POLL_INTERVAL", "1.0")),
            api_url=os.environ.get("BENCHLAB_API_URL", "http://127.0.0.1:8000"),
            mqtt_broker=os.environ.get("MQTT_BROKER", "localhost"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
        )

    cfg = _resolve_config(args)

    if not cfg["host"]:
        logger.error(
            "No remote MQTT host configured.\n"
            "Set LINK_REMOTE_HOST env var or add 'remote_host' to link.config"
        )
        return

    # Connect local datasource
    source    = args.source
    ds_kwargs = {}
    if source == "fastapi":
        ds_kwargs["base_url"] = args.api_url
    elif source == "mqtt":
        ds_kwargs["broker"] = args.mqtt_broker
        ds_kwargs["port"]   = args.mqtt_port

    logger.info(f"Link: connecting to {source} datasource")
    datasource = DataSourceManager(source_type=source, **ds_kwargs)
    if not datasource.connect():
        logger.error(f"Failed to connect to {source} datasource — aborting")
        return

    # Connect cloud MQTT
    cloud = CloudMQTTClient(cfg)
    if not cloud.connect():
        datasource.disconnect()
        return

    link = BenchlabLink(datasource, cloud, cfg)
    link.start()

    logger.info(
        f"Benchlab Link running  |  source={source}  "
        f"broker={cfg['host']}:{cfg['port']}  "
        f"topic={cfg['topic']}  interval={cfg['interval']}s"
    )
    logger.info("Press Ctrl+C to stop")

    try:
        while True:
            n = link.publish_all()
            if n:
                logger.debug(f"Published {n} device(s)")
            time.sleep(cfg["interval"])
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        link.stop()
        cloud.disconnect()
        datasource.disconnect()
        logger.info("Link shutdown complete")


if __name__ == "__main__":
    run_link()