# vu_updater.py

import json
import logging
import os
import signal
import threading
import time
import types
import zlib
from pathlib import Path

import requests

from benchlab.core.datasource_manager import DataSourceManager
from benchlab.vu.sensors import get_sensor_value
from benchlab.vu.vu_logo_gen import generate_sensor_logo
from benchlab.vu.vu_server_manager import (
    start_vu_server, check_vu_server, forward_logs, terminate_vu_server
)

BASE_DIR = Path(__file__).parent

VU_SERVER_CONFIG = BASE_DIR / "vu_server.config"
VU_DIAL_CONFIG = BASE_DIR / "vu_dial.config"
STANDARD_LOGO = BASE_DIR / "assets" / "bl_logo_144x200.png"

VU_DIAL_LAST_MTIME = 0
previous_dial_cfg = {}
shutdown_event = threading.Event()

# --- Logger ---
logger = logging.getLogger("vu_updater")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)
logger.addHandler(_ch)
_fh = logging.FileHandler(BASE_DIR / "vu_updater.log", mode="a")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_fmt)
logger.addHandler(_fh)


def handle_sigint(signum, frame):
    print("\nCtrl+C pressed. Initiating graceful shutdown...")
    shutdown_event.set()


signal.signal(signal.SIGINT, handle_sigint)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_crc32(path: Path) -> str:
    crc = 0
    with path.open("rb") as f:
        while chunk := f.read(65536):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def load_json(path, default=None):
    if Path(path).exists():
        with open(path, "r") as f:
            return json.load(f)
    return default if default is not None else {}


def reload_dial_config():
    global VU_DIAL_LAST_MTIME
    if not VU_DIAL_CONFIG.exists():
        return []
    mtime = VU_DIAL_CONFIG.stat().st_mtime
    if mtime == VU_DIAL_LAST_MTIME:
        return []
    VU_DIAL_LAST_MTIME = mtime
    try:
        new_cfg = load_json(VU_DIAL_CONFIG, default=[])
    except Exception as e:
        logger.error(f"Failed to load vu_dial.config: {e}")
        return []
    changed = []
    for mapping in new_cfg:
        uid = mapping.get("dial_uid")
        if not uid:
            continue
        if mapping != previous_dial_cfg.get(uid, {}):
            changed.append(mapping)
        previous_dial_cfg[uid] = mapping.copy()
    if changed:
        logger.info(f"Detected {len(changed)} dial config changes")
    return changed


# ---------------------------------------------------------------------------
# VU API client
# ---------------------------------------------------------------------------

class VUClient:
    def __init__(self, server_url, api_key):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key

    def _get(self, path, **params):
        params["key"] = self.api_key
        r = requests.get(f"{self.server_url}{path}", params=params, timeout=10)
        r.raise_for_status()
        return r

    def update_dial(self, dial_uid, value):
        try:
            self._get(f"/api/v0/dial/{dial_uid}/set", value=value)
        except requests.RequestException as e:
            logger.error(f"Failed to update {dial_uid}: {e}")

    def update_backlight(self, dial_uid, rgb):
        try:
            self._get(f"/api/v0/dial/{dial_uid}/backlight",
                      red=rgb[0], green=rgb[1], blue=rgb[2])
        except requests.RequestException as e:
            logger.error(f"Failed to update backlight for {dial_uid}: {e}")

    def update_name(self, dial_uid, name):
        try:
            self._get(f"/api/v0/dial/{dial_uid}/name", name=name)
        except requests.RequestException as e:
            logger.error(f"Failed to update name {dial_uid}: {e}")

    def upload_logo(self, dial_uid, logo_path, force=False):
        if not Path(logo_path).exists():
            logger.warning(f"Logo file not found: {logo_path}")
            return
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/image/set"
            with open(logo_path, "rb") as f:
                r = requests.post(
                    url, files={
                        "imgfile": f}, params={
                        "key": self.api_key, "force": int(force)}, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to upload logo {dial_uid}: {e}")

    def update_dial_easing(self, dial_uid, period, step):
        try:
            self._get(
                f"/api/v0/dial/{dial_uid}/easing/dial",
                period=period,
                step=step)
        except requests.RequestException as e:
            logger.error(f"Failed to set dial easing for {dial_uid}: {e}")

    def update_backlight_easing(self, dial_uid, period, step):
        try:
            self._get(
                f"/api/v0/dial/{dial_uid}/easing/backlight",
                period=period,
                step=step)
        except requests.RequestException as e:
            logger.error(f"Failed to set backlight easing for {dial_uid}: {e}")

    def get_dial_image_crc(self, dial_uid: str) -> str:
        try:
            headers = (
                {"Authorization": f"Bearer {self.api_key}"}
                if self.api_key else {})
            r = requests.get(
                f"{self.server_url}/api/v0/dial/{dial_uid}/image/crc",
                headers=headers, timeout=10)
            r.raise_for_status()
            return r.json().get("crc", "").upper()
        except requests.RequestException as e:
            logger.warning(f"Failed to get CRC for {dial_uid}: {e}")
            return ""


# ---------------------------------------------------------------------------
# Main updater
# ---------------------------------------------------------------------------

class BenchlabVUUpdater:
    def __init__(self, server_config: dict, dial_config: list,
                 datasource: DataSourceManager):
        self.client = VUClient(
            server_config.get("vu_server_url", "http://localhost:5340"),
            server_config.get("api_key", ""),
        )
        self.interval = server_config.get("update_interval_sec", 1)
        self.mappings = dial_config if isinstance(dial_config, list) else []
        self.datasource = datasource
        self.uploaded_dials: set = set()
        self.standard_logo_path = Path(
            __file__).parent / server_config.get("logo_file", "")

        if not self.mappings:
            logger.warning(
                "No dial mappings found in vu_dial.config — run "
                "-vuconfig to configure dials. Waiting for config...")

        # uid → latest telemetry snapshot dict
        self._snapshots: dict = {}
        self._snapshot_lock = threading.Lock()

        # Start background polling thread
        self._poller = threading.Thread(target=self._poll_loop, daemon=True,
                                        name="VUTelemetryPoller")
        self._poller.start()

    def _poll_loop(self):
        """Background thread: keeps _snapshots up to date via
        DataSourceManager."""
        # Discover all UIDs we care about from the mappings
        while not shutdown_event.is_set():
            try:
                raw = self.datasource.list_devices()
                if isinstance(raw, dict):
                    uids = list(raw.keys())
                else:
                    uids = [d.get("uid") for d in raw if d.get("uid")]

                for uid in uids:
                    try:
                        self.datasource.select_device(uid)
                        snap = self.datasource.snapshot()
                        data = (snap.get("sensor_data")
                                or snap.get("all_telemetry", {}).get(uid)
                                or {})
                        if data:
                            with self._snapshot_lock:
                                self._snapshots[uid] = data
                    except Exception as e:
                        logger.debug(f"Poll error for {uid}: {e}")
            except Exception as e:
                logger.warning(f"Device list error: {e}")

            shutdown_event.wait(self.interval)

    def _get_telemetry(self, uid: str) -> dict:
        with self._snapshot_lock:
            return self._snapshots.get(uid, {})

    def normalize_value(self, val, min_val, max_val):
        if val is None or max_val == min_val:
            return 0
        return max(0, min(100, (val - min_val) / (max_val - min_val) * 100))

    def setup_dial(self, mapping, max_attempts=3):
        dial_uid = mapping.get("dial_uid")
        if not dial_uid or dial_uid in self.uploaded_dials:
            return

        template_path = Path(__file__).parent / "assets/bl_dial_144x200.png"
        logo_uploaded = False
        for attempt in range(1, max_attempts + 1):
            try:
                logo_file = generate_sensor_logo(
                    template_path, mapping["sensor"],
                    mapping.get("min", 0), mapping.get("max", 100),
                    benchlab_port=mapping.get("benchlab_port"),
                )
                self.client.upload_logo(dial_uid, logo_file)
                logo_uploaded = True
                break
            except Exception as e:
                logger.warning(
                    f"[{attempt}] Dynamic logo failed for {dial_uid}: {e}")
                time.sleep(0.2)

        if not logo_uploaded and self.standard_logo_path.exists():
            try:
                self.client.upload_logo(
                    dial_uid, self.standard_logo_path, force=True)
                logo_uploaded = True
            except Exception as e:
                logger.error(
                    f"Standard logo upload failed for {dial_uid}: {e}")

        backlight = mapping.get("backlight", [0, 0, 0])
        for attempt in range(1, max_attempts + 1):
            try:
                self.client.update_backlight(dial_uid, backlight)
                break
            except Exception as e:
                logger.warning(
                    f"[{attempt}] Failed to set backlight for {dial_uid}: {e}")
                time.sleep(0.2)

        for attempt in range(1, max_attempts + 1):
            try:
                self.client.update_dial_easing(
                    dial_uid, *mapping.get("easing_dial", [50, 5]))
                self.client.update_backlight_easing(
                    dial_uid, *mapping.get("easing_backlight", [50, 5]))
                break
            except Exception as e:
                logger.warning(
                    f"[{attempt}] Failed to set easing for {dial_uid}: {e}")
                time.sleep(0.2)

        name = mapping.get("dial_name")
        if name:
            for attempt in range(1, max_attempts + 1):
                try:
                    self.client.update_name(dial_uid, name)
                    break
                except Exception as e:
                    logger.warning(
                        f"[{attempt}] Failed to set name for {dial_uid}: {e}")
                    time.sleep(0.2)

        self.uploaded_dials.add(dial_uid)
        logger.info(f"Setup complete for dial {dial_uid}")

    def apply_config_changes(self, changed_mappings):
        for mapping in changed_mappings:
            dial_uid = mapping.get("dial_uid")
            if not dial_uid:
                continue
            logger.info(f"Applying changes for {dial_uid}")
            try:
                template_path = Path(__file__).parent / \
                    "assets/bl_dial_144x200.png"
                logo_file = generate_sensor_logo(
                    template_path, mapping["sensor"],
                    mapping.get("min", 0), mapping.get("max", 100),
                    benchlab_port=mapping.get("benchlab_port"),
                )
                self.client.upload_logo(dial_uid, logo_file, force=True)
            except Exception as e:
                logger.warning(f"Failed to update logo for {dial_uid}: {e}")
                if self.standard_logo_path.exists():
                    self.client.upload_logo(
                        dial_uid, self.standard_logo_path, force=True)

            if mapping.get("dial_name"):
                try:
                    self.client.update_name(dial_uid, mapping["dial_name"])
                except Exception as e:
                    logger.warning(
                        f"Failed to update name for {dial_uid}: {e}")

            try:
                self.client.update_backlight(
                    dial_uid, mapping.get("backlight", [0, 0, 0]))
            except Exception as e:
                logger.warning(
                    f"Failed to update backlight for {dial_uid}: {e}")

            try:
                self.client.update_dial_easing(
                    dial_uid, *mapping.get("easing_dial", [50, 5]))
                self.client.update_backlight_easing(
                    dial_uid, *mapping.get("easing_backlight", [50, 5]))
            except Exception as e:
                logger.warning(f"Failed to update easing for {dial_uid}: {e}")

    def poll_and_update(self):
        """Push latest telemetry values to VU dials."""
        # Build uid lookup by benchlab_port
        try:
            raw = self.datasource.list_devices()
            if isinstance(raw, dict):
                port_to_uid = {
                    info.get("port"): uid for uid,
                    info in raw.items()}
            else:
                port_to_uid = {d.get("port"): d.get("uid") for d in raw}
        except Exception as e:
            logger.warning(f"Could not resolve port→uid map: {e}")
            port_to_uid = {}

        for mapping in self.mappings:
            port = mapping.get("benchlab_port")
            if not port or port in (None, "", "Unknown"):
                continue

            uid = port_to_uid.get(port)
            if not uid:
                continue

            telemetry = self._get_telemetry(uid)
            if not telemetry:
                continue

            self.setup_dial(mapping)

            sensor_key = mapping.get("sensor")
            dial_uid = mapping.get("dial_uid")
            if not sensor_key or not dial_uid:
                continue

            value = get_sensor_value(telemetry, sensor_key)
            value = self.normalize_value(
                value, mapping.get(
                    "min", 0), mapping.get(
                    "max", 100))
            logger.info(f"{port} -> {sensor_key} = {value:.1f} -> {dial_uid}")
            self.client.update_dial(dial_uid, value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_updater(args=None):
    """Run the VU dial updater.

    Parameters
    ----------
    args:
        Standard benchlab args namespace. If None, reads from env vars.
    """
    if args is None:
        args = types.SimpleNamespace(
            source=os.environ.get(
                "BENCHLAB_DATA_SOURCE",
                "direct"),
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
            interval=1.0,
        )

    logger.info("Launching the BENCHLAB VU Server & Dials")
    time.sleep(1)
    logger.info("Review & update configuration using -vuconfig")
    time.sleep(1)
    logger.info("Checking for VU server...")

    server_cfg = load_json(VU_SERVER_CONFIG, default={})
    dial_cfg = load_json(VU_DIAL_CONFIG, default=[])
    server_url = server_cfg.get("vu_server_url", "http://localhost:5340")
    api_key = server_cfg.get("api_key", "")

    server_proc = None
    if check_vu_server(server_url, api_key):
        logger.info(f"VU server already running at {server_url}")
    else:
        server_proc = start_vu_server()
        if server_proc:
            logger.info(f"Started local VU server at {server_url}")
            threading.Thread(
                target=forward_logs, args=(
                    server_proc,), daemon=True).start()
        else:
            logger.error(
                "Failed to start local VU server — dial updates "
                "will not work.")

    time.sleep(1)

    # Connect datasource
    ds_kwargs = {}
    if args.source in ("fastapi", "fastapi_custom"):
        ds_kwargs["base_url"] = args.api_url
    elif args.source == "mqtt":
        ds_kwargs["broker"] = args.mqtt_broker
        ds_kwargs["port"] = args.mqtt_port

    datasource = DataSourceManager(source_type=args.source, **ds_kwargs)
    if not datasource.connect():
        logger.error(f"Failed to connect to {args.source} datasource")
        return

    updater = BenchlabVUUpdater(server_cfg, dial_cfg, datasource)
    logger.info(f"Starting VU dial updater (interval: {updater.interval}s)")
    time.sleep(1)

    try:
        while not shutdown_event.is_set():
            changed_mappings = reload_dial_config()
            if changed_mappings:
                updater.apply_config_changes(changed_mappings)
            updater.poll_and_update()
            time.sleep(updater.interval)
    finally:
        logger.info("Updater stopping — resetting dials...")
        for m in updater.mappings:
            dial_uid = m.get("dial_uid")
            if dial_uid:
                try:
                    updater.client.update_dial(dial_uid, 0)
                    updater.client.update_backlight(dial_uid, [0, 0, 0])
                except Exception as e:
                    logger.error(f"Failed to reset {dial_uid}: {e}")

        standard_logo = updater.standard_logo_path
        if standard_logo.exists():
            standard_crc = file_crc32(standard_logo)
            for idx, m in enumerate(updater.mappings, 1):
                dial_uid = m.get("dial_uid")
                if not dial_uid:
                    continue
                logger.info(
                    f"[{idx}/{len(updater.mappings)}] Restoring logo "
                    f"on {dial_uid}")
                try:
                    updater.client.upload_logo(
                        dial_uid, standard_logo, force=True)
                except Exception as e:
                    logger.warning(
                        f"Failed to restore logo for {dial_uid}: {e}")
                    continue
                start = time.time()
                while time.time() - start < 1:
                    try:
                        if updater.client.get_dial_image_crc(
                                dial_uid) == standard_crc:
                            logger.info(f"Logo CRC verified for {dial_uid}")
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)
                else:
                    logger.warning(f"Timeout verifying logo for {dial_uid}")

        datasource.disconnect()

        if server_proc:
            logger.info("Terminating VU server subprocess...")
            terminate_vu_server(server_proc)

        logger.info("Shutdown complete.")


if __name__ == "__main__":
    run_updater()
