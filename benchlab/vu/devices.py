# benchlab/vu/devices.py

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

DUMMY_UID = "0000000000000000"
DUMMY_DIAL = (DUMMY_UID, "No Dial")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "vu_server.config")
TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "vu_server.config_template")

_DEFAULT_VU_CONFIG = {
    "vu_server_url": "http://localhost:5340",
    "api_key": "",
    "logo_file": ""}


def load_vu_server_config(
        config_path=CONFIG_PATH,
        template_path=TEMPLATE_PATH):
    """Load vu_server.config, creating it from the template (or a hardcoded
    default) if missing. Never raises — any failure at any step falls back
    to _DEFAULT_VU_CONFIG so a malformed config or template can't crash
    import of this module.
    """
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        try:
            if os.path.exists(template_path):
                with open(template_path, "r") as f:
                    cfg = json.load(f)
                logger.info("Created VU server config from template")
            else:
                cfg = dict(_DEFAULT_VU_CONFIG)
                logger.info("Created default VU server config")
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            return cfg
        except Exception as e:
            logger.error(f"Failed to load VU server config template: {e}")
            return dict(_DEFAULT_VU_CONFIG)
    except Exception as e:
        logger.error(f"Failed to load VU server config: {e}")
        return dict(_DEFAULT_VU_CONFIG)


# --- Load VU server config ---
VU_CONFIG = load_vu_server_config()

VU_SERVER_URL = VU_CONFIG.get("vu_server_url", "http://localhost:5340")
API_KEY = VU_CONFIG.get("api_key", "")


def get_benchlab_devices(datasource=None) -> list:
    """Return list of {port, uid, name} dicts via datasource (no serial
    access)."""
    if datasource is None:
        logger.warning("get_benchlab_devices called without a datasource")
        return []
    try:
        raw = datasource.list_devices()
        if isinstance(raw, dict):
            return [{"port": info.get("port", "?"), "uid": uid,
                     "name": f"Benchlab {info.get('port', uid)}"}
                    for uid, info in raw.items()]
        return [{"port": d.get("port", "?"), "uid": d.get("uid", "?"),
                 "name": f"Benchlab {d.get('port', d.get('uid', '?'))}"}
                for d in raw]
    except Exception as e:
        logger.warning(f"list_devices failed: {e}")
        return []


def get_vu_dials(vu_server_url=VU_SERVER_URL, api_key=API_KEY):
    """Fetch VU dials from the server. Returns [(uid, dial_name), ...]."""
    try:
        response = requests.get(
            f"{vu_server_url}/api/v0/dial/list",
            params={"key": api_key},
            timeout=2.0,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        if not data:
            return [DUMMY_DIAL]
        return [(d.get("uid", DUMMY_UID), d.get("dial_name", "No Dial"))
                for d in data]
    except requests.RequestException as e:
        logger.error(f"VU server request failed: {e}")
        return [DUMMY_DIAL]


def provision_vu_dials(vu_server_url=VU_SERVER_URL, api_key=API_KEY):
    """Ask the VU hub to scan and provision new dials."""
    try:
        response = requests.get(
            f"{vu_server_url}/api/v0/dial/provision",
            params={"admin_key": api_key},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "ok":
            logger.info("VU dial provisioning completed successfully.")
            return True
        logger.error(f"Provisioning failed: {data.get('message', 'Unknown')}")
        return False
    except requests.RequestException as e:
        logger.error(f"Provisioning request failed: {e}")
        return False


def provision_missing_vu_dials(datasource, vu_server_url=VU_SERVER_URL,
                               api_key=API_KEY, dry_run=False, max_wait=1.0):
    """Provision dials that are physically connected but not yet on the hub."""
    physical_devices = get_benchlab_devices(datasource)
    vu_dials = get_vu_dials(vu_server_url, api_key)
    vu_uids = {uid for uid, _ in vu_dials}
    unprovisioned = [d for d in physical_devices if d["uid"] not in vu_uids]

    if not unprovisioned:
        logger.info("All physical dials are already provisioned.")
        return []

    logger.info(f"Found {len(unprovisioned)} unprovisioned dials: "
                f"{[d['uid'] for d in unprovisioned]}")

    if dry_run:
        return [d["uid"] for d in unprovisioned]

    if not provision_vu_dials(vu_server_url, api_key):
        logger.error("Provisioning failed, new dials may not appear.")
        return []

    start = time.time()
    newly_provisioned = []
    while time.time() - start < max_wait:
        updated_uids = {uid for uid, _ in get_vu_dials(vu_server_url, api_key)}
        newly_provisioned = [
            d["uid"]
            for d in unprovisioned if d["uid"] in updated_uids]
        if newly_provisioned:
            break
        time.sleep(0.1)

    logger.info(f"Successfully provisioned: {newly_provisioned}")
    return newly_provisioned


def vu_server_check(vu_server_url=VU_SERVER_URL, api_key=API_KEY, timeout=0.5):
    try:
        r = requests.get(f"{vu_server_url}/api/v0/dial/list",
                         params={"key": api_key}, timeout=timeout)
        return r.status_code in (200, 403)
    except requests.RequestException:
        return False
