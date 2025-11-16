# vu_updater.py

import json
import logging
import os
from pathlib import Path
import requests
import threading
import time
import signal
import subprocess
import sys
import yaml
import zlib

from benchlab.core.serial_io import open_serial_connection
from benchlab.vu.sensors import read_sensor_values
from benchlab.vu.vu_logo_gen import generate_sensor_logo
from benchlab.vu.vu_server_manager import start_vu_server, check_vu_server, forward_logs, terminate_vu_server

BASE_DIR = Path(__file__).parent

VU_DIR = BASE_DIR / "vu"
VU_SERVER_DIR = BASE_DIR / "VU-Server"
VU_SERVER_LOG = BASE_DIR / "vu_server_autostart.log"
SERVER_YAML_CONFIG = VU_SERVER_DIR / "config.yaml"

VU_SERVER_CONFIG = BASE_DIR / "vu_server.config"
VU_DIAL_CONFIG = BASE_DIR / "vu_dial.config"
STANDARD_LOGO = VU_DIR / "assets" / "bl_logo_144x200.png"

VU_DIAL_LAST_MTIME = 0
previous_dial_cfg = {}

shutdown_event = threading.Event()

# --- Logger setup ---
logger = logging.getLogger("vu_updater")
logger.setLevel(logging.DEBUG)  # capture everything
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)  # show INFO+ in console
ch.setFormatter(formatter)
logger.addHandler(ch)

# Optional file handler
fh = logging.FileHandler(BASE_DIR / "vu_updater.log", mode="a")
fh.setLevel(logging.DEBUG)  # log everything
fh.setFormatter(formatter)
logger.addHandler(fh)

def handle_sigint(signum, frame):
    print("\nCtrl+C pressed. Initiating graceful shutdown...")
    shutdown_event.set()  # signal the updater to stop
    # Do NOT call sys.exit() here; let main loop handle it.

signal.signal(signal.SIGINT, handle_sigint)

def file_crc32(path: Path) -> str:
    """Compute CRC32 of a file as hex string (like the server expects)."""
    buf_size = 65536
    crc = 0
    with path.open("rb") as f:
        while chunk := f.read(buf_size):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"  # Uppercase hex

def load_json(path, default=None):
    if path.exists():
        with path.open("r") as f:
            return json.load(f)
    return default if default is not None else {}

def reload_dial_config():
    global VU_DIAL_LAST_MTIME, previous_dial_cfg
    if not VU_DIAL_CONFIG.exists():
        return []

    mtime = VU_DIAL_CONFIG.stat().st_mtime
    if mtime != VU_DIAL_LAST_MTIME:
        VU_DIAL_LAST_MTIME = mtime
        try:
            new_cfg = load_json(VU_DIAL_CONFIG, default=[])
        except Exception as e:
            logger.error(f"Failed to load vu_dial.config: {e}")
            return []

        # Detect changes
        changed_mappings = []
        for mapping in new_cfg:
            uid = mapping.get("dial_uid")
            if not uid:
                continue
            prev = previous_dial_cfg.get(uid, {})
            if mapping != prev:
                changed_mappings.append(mapping)
            previous_dial_cfg[uid] = mapping.copy()

        if changed_mappings:
            logger.info(f"Detected {len(changed_mappings)} dial config changes")
        return changed_mappings
    return []

class VUClient:
    def __init__(self, server_url, api_key):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key

    def update_dial(self, dial_uid, value):
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/set"
            params = {"value": value, "key": self.api_key}
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to update {dial_uid}: {e}")

    def update_backlight(self, dial_uid, rgb):
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/backlight"
            params = {"key": self.api_key, "red": rgb[0], "green": rgb[1], "blue": rgb[2]}
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to update backlight for {dial_uid}: {e}")

    def update_name(self, dial_uid, name):
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/name"
            params = {"key": self.api_key, "name": name}
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to update name {dial_uid}: {e}")

    def upload_logo(self, dial_uid, logo_path, force=False):
        if not logo_path.exists():
            logger.warning(f"Logo file not found: {logo_path}")
            return
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/image/set"
            files = {"imgfile": open(logo_path, "rb")}
            params = {"key": self.api_key, "force": int(force)}
            r = requests.post(url, files=files, params=params, timeout=10)
            r.raise_for_status()
            resp = r.json()
            if resp.get("status") == "ok":
                logger.info(f"Logo uploaded to {dial_uid}")
            else:
                logger.warning(f"Logo upload response: {resp}")
        except requests.RequestException as e:
            logger.error(f"Failed to upload logo {dial_uid}: {e}")

    def update_dial_easing(self, dial_uid, period, step):
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/easing/dial"
            params = {"key": self.api_key, "period": period, "step": step}
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to set dial easing for {dial_uid}: {e}")

    def update_backlight_easing(self, dial_uid, period, step):
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/easing/backlight"
            params = {"key": self.api_key, "period": period, "step": step}
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to set backlight easing for {dial_uid}: {e}")

    def get_dial_image_crc(self, dial_uid: str) -> str:
        """Query the server for the dial's image CRC."""
        try:
            url = f"{self.server_url}/api/v0/dial/{dial_uid}/image/crc"
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get("crc", "").upper()
        except requests.RequestException as e:
            logger.warning(f"Failed to get CRC for {dial_uid}: {e}")
            return ""

class BenchlabVUUpdater:
    def __init__(self, server_config, dial_config):
        self.client = VUClient(
            server_config.get("vu_server_url", "http://localhost:5340"),
            server_config.get("api_key", "")
        )
        self.interval = server_config.get("update_interval_sec", 1)
        self.mappings = dial_config if isinstance(dial_config, list) else []
        if not self.mappings:
            raise ValueError("No dial mappings found in vu_dial.config")

        self.devices = {}  # port -> serial connection
        self.device_last_attempt = {}  # port -> last attempt timestamp
        self.uploaded_dials = set()  # track dials already setup

        self.standard_logo_path = Path(__file__).parent / server_config.get("logo_file", "")

    def ensure_connections(self):
        ports = {m.get("benchlab_port") for m in self.mappings if m.get("benchlab_port") not in (None, "", "Unknown")}
        for port in ports:
            if port not in self.devices or self.devices[port] is None:
                last = self.device_last_attempt.get(port, 0)
                if time.time() - last > 2:
                    try:
                        ser = open_serial_connection(port)
                        self.devices[port] = ser
                        logger.info(f"Connected to {port}")
                    except Exception as e:
                        logger.error(f"Failed to connect {port}: {e}")
                        self.devices[port] = None
                    self.device_last_attempt[port] = time.time()

    def normalize_value(self, val, min_val, max_val):
        if val is None or max_val == min_val:
            return 0
        norm = (val - min_val) / (max_val - min_val) * 100
        return max(0, min(100, norm))

    def setup_dial(self, mapping, max_attempts=3):
        """Robust dial setup: logo, backlight, easing, name."""
        dial_uid = mapping.get("dial_uid")
        if not dial_uid:
            logger.warning("Dial mapping missing 'dial_uid', skipping.")
            return

        # If already fully initialized, skip
        if dial_uid in self.uploaded_dials:
            return

        # --- Logo ---
        logo_uploaded = False
        template_path = Path(__file__).parent / "assets/bl_dial_144x200.png"
        for attempt in range(1, max_attempts + 1):
            try:
                logo_file = generate_sensor_logo(
                    template_path,
                    mapping["sensor"],
                    mapping.get("min", 0),
                    mapping.get("max", 100),
                    benchlab_port=mapping.get("benchlab_port")
                )
                self.client.upload_logo(dial_uid, logo_file)
                logger.info(f"Dynamic logo uploaded to {dial_uid}")
                logo_uploaded = True
                break
            except Exception as e:
                logger.warning(f"[Attempt {attempt}] Dynamic logo failed for {dial_uid}: {e}")
                time.sleep(0.2)

        if not logo_uploaded and self.standard_logo_path.exists():
            try:
                self.client.upload_logo(dial_uid, self.standard_logo_path, force=True)
                logger.info(f"Standard logo uploaded to {dial_uid}")
                logo_uploaded = True
            except Exception as e:
                logger.error(f"Standard logo upload failed for {dial_uid}: {e}")

        if not logo_uploaded:
            logger.warning(f"No logo could be uploaded for {dial_uid}")

        # --- Backlight ---
        backlight = mapping.get("backlight", [0, 0, 0])
        for attempt in range(1, max_attempts + 1):
            try:
                self.client.update_backlight(dial_uid, backlight)
                break
            except Exception as e:
                logger.warning(f"[Attempt {attempt}] Failed to set backlight for {dial_uid}: {e}")
                time.sleep(0.2)

        # --- Easing ---
        for attempt in range(1, max_attempts + 1):
            try:
                self.client.update_dial_easing(dial_uid, *mapping.get("easing_dial", [50, 5]))
                self.client.update_backlight_easing(dial_uid, *mapping.get("easing_backlight", [50, 5]))
                break
            except Exception as e:
                logger.warning(f"[Attempt {attempt}] Failed to set easing for {dial_uid}: {e}")
                time.sleep(0.2)

        # --- Name ---
        name = mapping.get("dial_name")
        if name:
            for attempt in range(1, max_attempts + 1):
                try:
                    self.client.update_name(dial_uid, name)
                    break
                except Exception as e:
                    logger.warning(f"[Attempt {attempt}] Failed to set name for {dial_uid}: {e}")
                    time.sleep(0.2)

        # --- Mark as fully initialized ---
        self.uploaded_dials.add(dial_uid)
        logger.info(f"Setup complete for dial {dial_uid}")

    def apply_config_changes(self, changed_mappings):
        for mapping in changed_mappings:
            dial_uid = mapping.get("dial_uid")
            if not dial_uid:
                continue

            logger.info(f"Applying changes for {dial_uid}")

            # Re-generate logo if sensor changed
            try:
                template_path = Path(__file__).parent / "assets/bl_dial_144x200.png"
                logo_file = generate_sensor_logo(
                    template_path,
                    mapping["sensor"],
                    mapping.get("min", 0),
                    mapping.get("max", 100),
                    benchlab_port=mapping.get("benchlab_port")
                )
                self.client.upload_logo(dial_uid, logo_file, force=True)
                logger.info(f"Updated logo for {dial_uid}")
            except Exception as e:
                logger.warning(f"Failed to update logo for {dial_uid}: {e}")
                if self.standard_logo_path.exists():
                    self.client.upload_logo(dial_uid, self.standard_logo_path, force=True)

            # Update name
            name = mapping.get("dial_name")
            if name:
                try:
                    self.client.update_name(dial_uid, name)
                except Exception as e:
                    logger.warning(f"Failed to update name for {dial_uid}: {e}")

            # Update backlight
            backlight = mapping.get("backlight", [0, 0, 0])
            try:
                self.client.update_backlight(dial_uid, backlight)
            except Exception as e:
                logger.warning(f"Failed to update backlight for {dial_uid}: {e}")

            # Easing
            try:
                self.client.update_dial_easing(dial_uid, *mapping.get("easing_dial", [50, 5]))
                self.client.update_backlight_easing(dial_uid, *mapping.get("easing_backlight", [50, 5]))
            except Exception as e:
                logger.warning(f"Failed to update easing for {dial_uid}: {e}")

    def poll_and_update(self):
        for port, ser in self.devices.items():
            if ser is None:
                continue
            try:
                telemetry = read_sensor_values(ser)
            except Exception as e:
                logger.error(f"Failed to read sensors from {port}: {e}")
                try:
                    ser.close()
                except:
                    pass
                self.devices[port] = None
                continue

            for mapping in self.mappings:
                if mapping.get("benchlab_port") not in (port,):
                    continue
                if mapping.get("benchlab_port") in (None, "", "Unknown"):
                    continue  # skip unconfigured

                # --- Setup phase ---
                self.setup_dial(mapping)

                # --- Update phase ---
                sensor_key = mapping.get("sensor")
                dial_uid = mapping.get("dial_uid")
                if not sensor_key or not dial_uid:
                    continue  # skip if not fully configured

                value = telemetry.get(sensor_key)
                value = self.normalize_value(value, mapping.get("min", 0), mapping.get("max", 100))

                logger.info(f"{port} -> {sensor_key} = {value:.1f} -> {dial_uid}")
                self.client.update_dial(dial_uid, value)

def run_updater():
    # Launching the updater
    logger.info("Launching the BENCHLAB VU Server & Dials")
    time.sleep(1)
    logger.info("Review & update configuration using -vuconfig")
    time.sleep(1)
    logger.info("Checking for VU server ... ")

    # Load configs
    server_cfg = load_json(VU_SERVER_CONFIG, default={})
    dial_cfg = load_json(VU_DIAL_CONFIG, default=[])

    # Check if a server is already running
    server_url = server_cfg.get("vu_server_url", "http://localhost:5340")
    api_key = server_cfg.get("api_key", "")

    server_proc = None 
    if check_vu_server(server_url, api_key):
        logger.info(f"VU server already running at {server_url}")
    else:
        # Start our own server
        server_proc = start_vu_server()
        logger.info(f"Started local VU server at {server_url}")
        if server_proc:
            threading.Thread(target=forward_logs, args=(server_proc,), daemon=True).start()

    time.sleep(1)

    # Initialize updater
    updater = BenchlabVUUpdater(server_cfg, dial_cfg)
    updater.ensure_connections()

    logger.info(f"Starting Benchlab VU Dial updater (interval: {updater.interval}s)")

    time.sleep(1)

    try:
        while not shutdown_event.is_set():
            updater.ensure_connections()

            # Check for dial config changes
            changed_mappings = reload_dial_config()
            if changed_mappings:
                updater.apply_config_changes(changed_mappings)

            updater.poll_and_update()
            time.sleep(updater.interval)

    finally:
        logger.info("Updater stopping...")

        # --- Step 1: Reset all dials ---
        logger.info("Resetting dials to 0 and turning off backlight...")
        for m in updater.mappings:
            dial_uid = m.get("dial_uid")
            if not dial_uid:
                continue
            try:
                updater.client.update_dial(dial_uid, 0)
                updater.client.update_backlight(dial_uid, [0, 0, 0])
            except Exception as e:
                logger.error(f"Failed to reset {dial_uid}: {e}")

        # --- Step 2: Restore standard logos with CRC verification ---
        standard_logo = updater.standard_logo_path
        if standard_logo.exists():
            logger.info("Restoring standard logos on all dials...")
            # Precompute CRC32 of standard logo
            with standard_logo.open("rb") as f:
                data = f.read()
                standard_crc = format(zlib.crc32(data) & 0xFFFFFFFF, "08X")

            for idx, m in enumerate(updater.mappings, start=1):
                dial_uid = m.get("dial_uid")
                if not dial_uid:
                    continue

                logger.info(f"[{idx}/{len(updater.mappings)}] Uploading standard logo to {dial_uid}...")
                try:
                    updater.client.upload_logo(dial_uid, standard_logo, force=True)
                except Exception as e:
                    logger.warning(f"Failed to upload standard logo for {dial_uid}: {e}")
                    continue

                # --- Verify CRC with timeout ---
                start_time = time.time()
                timeout = 1  # seconds
                while time.time() - start_time < timeout:
                    try:
                        crc = updater.client.get_dial_image_crc(dial_uid)
                        if crc == standard_crc:
                            logger.info(f"Logo CRC verified for {dial_uid}")
                            break
                    except Exception as e:
                        logger.warning(f"Error checking CRC for {dial_uid}: {e}")
                    time.sleep(0.5)
                else:
                    logger.warning(f"Timeout verifying logo for {dial_uid}")

        # --- Step 3: Close serial connections ---
        logger.info("Closing serial ports...")
        for ser in updater.devices.values():
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass

        # --- Step 4: Terminate VU server subprocess ---
        if server_proc:
            from benchlab.vu.vu_server_manager import terminate_vu_server
            logger.info("Terminating VU server subprocess...")
            terminate_vu_server(server_proc)

        logger.info("Shutdown complete.")

if __name__ == "__main__":
    run_updater()