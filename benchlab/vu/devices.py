# benchlab/vu/devices.py

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "vu_server.config")

DUMMY_UID  = "0000000000000000"
DUMMY_DIAL = (DUMMY_UID, "No Dial")

try:
    with open(CONFIG_PATH, "r") as f:
        VU_CONFIG = json.load(f)
except Exception as e:
    logger.error(f"Failed to load VU server config: {e}")
    VU_CONFIG = {"vu_server_url": "http://localhost:5340", "api_key": ""}

VU_SERVER_URL = VU_CONFIG.get("vu_server_url", "http://localhost:5340")
API_KEY       = VU_CONFIG.get("api_key", "")


def get_benchlab_devices(datasource=None) -> list:
    """Return list of {port, uid, name} dicts via datasource (no serial access)."""
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
        return [(d.get("uid", DUMMY_UID), d.get("dial_name", "No Dial")) for d in data]
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
    vu_uids  = {uid for uid, _ in vu_dials}
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
        newly_provisioned = [d["uid"] for d in unprovisioned if d["uid"] in updated_uids]
        if newly_provisioned:
            break
        time.sleep(0.1)

    logger.info(f"Successfully provisioned: {newly_provisioned}")
    return newly_provisioned


def vu_server_check(vu_server_url=VU_SERVER_URL, api_key=API_KEY, timeout=0.5):
    try:
        r = requests.get(f"{vu_server_url}/api/v0/dial/list",
                         params={"key": api_key}, timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False