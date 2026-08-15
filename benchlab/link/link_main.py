# benchlab/link/link_main.py
"""
Benchlab Link — cloud MQTT publisher.

Reads telemetry from any DataSourceManager source (direct / fastapi / mqtt)
and publishes JSON payloads to a remote (cloud) MQTT broker.

Configuration priority (highest first):
  1. args namespace
  2. Environment variables
  3. .env file in the link/ directory
  4. link.config JSON file
  5. Defaults

Environment variables
---------------------
REMOTE_MQTT_HOST      Cloud MQTT broker hostname
REMOTE_MQTT_PORT      Cloud MQTT broker port          (default: 443)
REMOTE_MQTT_USER      MQTT username
REMOTE_MQTT_PASS      MQTT password
REMOTE_MQTT_PATH      WebSocket path                  (default: /mqtt)
REMOTE_MQTT_TRANSPORT Transport: websockets | tcp      (default: websockets)
REMOTE_MQTT_PROTOCOL  Protocol: mqtt.MQTTv5 | mqtt.MQTTv311
                                                (default: mqtt.MQTTv5)
REMOTE_MQTT_QOS       QoS level: 0 | 1 | 2            (default: 1)
REMOTE_MQTT_TLS       Enable TLS: "true" / "false"    (default: true)
CLIENT_UUID           Device UUID for identification
MQTT_POLL_RATE        Poll/publish interval in seconds (default: 2)
LINK_TOPIC_PATTERN    Topic pattern with {uid}/{client_uuid} tokens
                                    (default: benchlab/{uid}/telemetry)
LINK_CLIENT_ID        MQTT client ID
                                (default: benchlab-link-<hostname>)

.env file
---------
Place a .env file in benchlab/link/ with KEY=VALUE pairs.
Copy .env.EXAMPLE as a starting point.

Config file
-----------
Loaded from LINK_CONFIG_PATH env var or benchlab/link/link.config by default.
JSON file with keys matching env var names.
"""

import json
import logging
import os
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
DEFAULT_ENV_PATH = Path(__file__).parent / ".env"
DEFAULT_TOPIC = "benchlab/{uid}/telemetry"
DEFAULT_PORT = 443
DEFAULT_INTERVAL = 2.0
RECONNECT_DELAY = 5.0   # seconds between reconnect attempts


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_env_file(env_path: Optional[Path] = None) -> None:
    """Load .env file into os.environ as fallback.

    Only sets vars not already set.
    """
    path = env_path or DEFAULT_ENV_PATH
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def _load_config(config_path: Optional[Path] = None) -> dict:
    """Load JSON config file, return empty dict on failure."""
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
    """Merge all config sources.

    Priority: args > env vars > .env > config file > defaults.
    """
    _load_env_file()
    file_cfg = _load_config()

    def _get(env_key: str, file_key: str, default):
        if args is not None:
            val = getattr(args, file_key.replace("-", "_"), None)
            if val is not None:
                return val
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val
        if file_key in file_cfg:
            return file_cfg[file_key]
        return default

    hostname = socket.gethostname()
    client_uuid = _get("CLIENT_UUID", "client_uuid", None)

    host = _get("REMOTE_MQTT_HOST", "remote_host", None)
    port = int(_get("REMOTE_MQTT_PORT", "remote_port", DEFAULT_PORT))
    user = _get("REMOTE_MQTT_USER", "remote_user", None)
    password = _get("REMOTE_MQTT_PASS", "remote_pass", None)
    path = _get("REMOTE_MQTT_PATH", "remote_path", "/mqtt")
    transport = _get("REMOTE_MQTT_TRANSPORT", "remote_transport", "websockets")
    protocol = _get("REMOTE_MQTT_PROTOCOL", "remote_protocol", "mqtt.MQTTv5")
    qos = int(_get("REMOTE_MQTT_QOS", "remote_qos", "1"))
    tls_raw = _get("REMOTE_MQTT_TLS", "remote_tls", "true")
    tls = str(tls_raw).lower() not in ("false", "0", "no")

    topic = _get("LINK_TOPIC_PATTERN", "topic_pattern", DEFAULT_TOPIC)
    interval = float(
        _get(
            "MQTT_POLL_RATE",
            "publish_interval",
            DEFAULT_INTERVAL))
    client_id = _get("LINK_CLIENT_ID", "client_id",
                     f"benchlab-link-{client_uuid or hostname}")

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "path": path,
        "transport": transport,
        "protocol": protocol,
        "qos": qos,
        "tls": tls,
        "topic": topic,
        "interval": interval,
        "client_id": client_id,
        "client_uuid": client_uuid,
    }


# ---------------------------------------------------------------------------
# Cloud MQTT client
# ---------------------------------------------------------------------------

class CloudMQTTClient:
    """Thin wrapper around paho-mqtt for publishing to a remote broker."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._connected = False
        self._lock = threading.Lock()
        self._client = self._build_client()

    def _build_client(self) -> mqtt.Client:
        """Build and configure the paho MQTT client from cfg."""
        protocol_str = self.cfg.get("protocol", "mqtt.MQTTv5")
        protocol = mqtt.MQTTv5 if "5" in protocol_str else mqtt.MQTTv311
        transport = self.cfg.get("transport", "websockets")

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.cfg["client_id"],
            transport=transport,
            protocol=protocol,
        )

        if transport == "websockets":
            client.ws_set_options(path=self.cfg.get("path", "/mqtt"))

        if self.cfg.get("user"):
            client.username_pw_set(self.cfg["user"], self.cfg.get("password"))

        if self.cfg.get("tls"):
            client.tls_set()

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect

        return client

    def connect(self) -> bool:
        host = self.cfg.get("host")
        if not host:
            logger.error("REMOTE_MQTT_HOST is not configured — cannot connect")
            return False
        try:
            self._client.connect(host, self.cfg["port"], keepalive=60)
            self._client.loop_start()
            deadline = time.time() + 10
            while not self._connected and time.time() < deadline:
                time.sleep(0.1)
            if not self._connected:
                logger.error(
                    f"Timed out connecting to {host}:{
                        self.cfg['port']}")
                return False
            logger.info(f"Connected to cloud broker {host}:{self.cfg['port']}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to cloud broker: {e}")
            return False

    def reconnect(self) -> bool:
        """Attempt to reconnect using a fresh client."""
        logger.info(
            f"Reconnecting to cloud broker {
                self.cfg['host']}:{
                self.cfg['port']}...")
        try:
            self._client.loop_stop()
        except Exception:
            pass
        self._client = self._build_client()
        return self.connect()

    def disconnect(self):
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass

    def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        if not self._connected:
            logger.warning("Not connected — skipping publish")
            return False
        try:
            result = self._client.publish(
                topic, json.dumps(payload), qos=qos, retain=False
            )
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Publish failed: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _on_connect(
            self,
            client,
            userdata,
            flags,
            reason_code,
            properties=None):
        if reason_code == 0:
            with self._lock:
                self._connected = True
            logger.info("Cloud broker: connection established")
        else:
            logger.error(
                "Cloud broker: connection refused "
                f"(reason_code={reason_code})"
            )

    def _on_disconnect(
            self,
            client,
            userdata,
            flags,
            reason_code,
            properties=None):
        with self._lock:
            self._connected = False
        if reason_code != 0:
            logger.warning(
                "Cloud broker: unexpected disconnect "
                f"(reason_code={reason_code})"
            )


# ---------------------------------------------------------------------------
# Link worker
# ---------------------------------------------------------------------------

class BenchlabLink:
    """Reads telemetry from DataSourceManager and publishes to cloud MQTT."""

    def __init__(self, datasource: DataSourceManager,
                 cloud: CloudMQTTClient, cfg: dict):
        self.datasource = datasource
        self.cloud = cloud
        self.cfg = cfg
        self._stop = threading.Event()
        self._snapshots = {}
        self._snap_lock = threading.Lock()
        self._worker = threading.Thread(
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

    def publish_all(self) -> int:
        """Publish latest snapshot for every known device.

        Returns count published.
        """
        topic_pattern = self.cfg["topic"]
        qos = self.cfg.get("qos", 1)
        client_uuid = self.cfg.get("client_uuid") or ""

        with self._snap_lock:
            snapshots = dict(self._snapshots)

        published = 0
        for uid, data in snapshots.items():
            if not data:
                continue
            topic = topic_pattern.format(uid=uid, client_uuid=client_uuid)
            payload = {"uid": uid, **data}
            if self.cloud.publish(topic, payload, qos=qos):
                published += 1
                logger.debug(f"Published {len(data)} sensors to {topic}")
            else:
                logger.warning(f"Failed to publish for {uid}")

        return published


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_link(args=None):
    """Run the Benchlab Link cloud publisher."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args is None:
        args = types.SimpleNamespace(
            source=os.environ.get(
                "BENCHLAB_DATA_SOURCE",
                "direct"),
            interval=float(
                os.environ.get(
                    "MQTT_POLL_RATE",
                    "2.0")),
            api_url=os.environ.get(
                "BENCHLAB_API_URL",
                "http://127.0.0.1:8000"),
            mqtt_broker=os.environ.get(
                "MQTT_BROKER",
                "localhost"),
            mqtt_port=int(
                os.environ.get(
                    "MQTT_PORT",
                    "1883")),
        )

    cfg = _resolve_config(args)

    if not cfg["host"]:
        logger.error(
            "No remote MQTT host configured.\n"
            "Set REMOTE_MQTT_HOST in environment, .env file, or link.config"
        )
        return

    # Connect local datasource
    source = args.source
    ds_kwargs = {}
    if source in ("fastapi", "fastapi_custom"):
        ds_kwargs["base_url"] = args.api_url
    elif source == "mqtt":
        ds_kwargs["broker"] = args.mqtt_broker
        ds_kwargs["port"] = args.mqtt_port

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
        f"transport={cfg['transport']}  topic={cfg['topic']}  "
        f"interval={cfg['interval']}s  qos={cfg['qos']}"
    )
    logger.info("Press Ctrl+C to stop")

    try:
        while True:
            # Reconnect if dropped
            if not cloud.is_connected:
                logger.warning(
                    "Cloud broker disconnected — retrying in "
                    f"{RECONNECT_DELAY}s"
                )
                time.sleep(RECONNECT_DELAY)
                cloud.reconnect()
                continue

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
