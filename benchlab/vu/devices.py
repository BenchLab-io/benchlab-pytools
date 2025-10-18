# benchlab/vu/devices.py

import os
import json
import time
import requests
import logging

from benchlab.core.serial_io import get_benchlab_ports, open_serial_connection, read_uid

DUMMY_UID = "0000000000000000"
DUMMY_BENCHLAB = {"port": None, "uid": DUMMY_UID}
DUMMY_DIAL = (DUMMY_UID, "No Dial")
_already_provisioned_once = False

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "vu_server.config")

# --- Load VU server config ---
try:
    with open(CONFIG_PATH, "r") as f:
        VU_CONFIG = json.load(f)
except Exception as e:
    logger.error(f"Failed to load VU server config: {e}")
    VU_CONFIG = {
        "vu_server_url": "http://localhost:5340",
        "api_key": ""
    }

VU_SERVER_URL = VU_CONFIG.get("vu_server_url", "http://localhost:5340")
API_KEY = VU_CONFIG.get("api_key", "")

def get_benchlab_devices():
    devices = []
    ports = get_benchlab_ports()
    for port_info in ports:
        port = port_info["port"] 
        uid = "unknown"
        try:
            ser = open_serial_connection(port)
            if ser:
                uid = read_uid(ser)
                ser.close()
        except Exception:
            pass
        devices.append({
            "port": port,
            "uid": uid,
            "name": f"Benchlab {port}"
        })
    return devices

# --- VU API functions ---
def get_vu_dials(vu_server_url=VU_SERVER_URL, api_key=API_KEY):
    """
    Fetch VU dials from the server.
    Returns a list of tuples: (uid, dial_name).
    This function does NOT provision new dials; it only fetches the current list.
    """
    try:
        # Fetch the current dial list from the hub
        response = requests.get(
            f"{vu_server_url}/api/v0/dial/list",
            params={"key": api_key},
            timeout=2.0
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
    """
    Ask the VU hub to scan and provision new dials.
    Returns True if request succeeded, False otherwise.
    """
    try:
        response = requests.get(
            f"{vu_server_url}/api/v0/dial/provision",
            params={"admin_key": api_key},
            timeout=5.0
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "ok":
            logger.info("VU dial provisioning completed successfully.")
            return True
        else:
            logger.error(f"Provisioning failed: {data.get('message', 'Unknown')}")
            return False
    except requests.RequestException as e:
        logger.error(f"Provisioning request failed: {e}")
        return False


def provision_missing_vu_dials(vu_server_url=VU_SERVER_URL, api_key=API_KEY, dry_run=False, max_wait=1.0):
    """
    Detect physically connected dials that are not yet provisioned on the VU hub
    and provision them.
    Returns a list of newly provisioned UIDs.
    
    max_wait: maximum seconds to wait for hub to register new dials
    """
    physical_devices = get_benchlab_devices()
    vu_dials = get_vu_dials(vu_server_url, api_key)
    vu_uids = {uid for uid, _ in vu_dials}

    unprovisioned = [dev for dev in physical_devices if dev["uid"] not in vu_uids]

    if not unprovisioned:
        logger.info("All physical dials are already provisioned.")
        return []

    logger.info(f"Found {len(unprovisioned)} unprovisioned dials: {[d['uid'] for d in unprovisioned]}")

    if dry_run:
        return [d["uid"] for d in unprovisioned]

    if not provision_vu_dials(vu_server_url, api_key):
        logger.error("Provisioning failed, new dials may not appear.")
        return []

    # --- Poll for new UIDs instead of sleeping ---
    start = time.time()
    newly_provisioned = []
    while time.time() - start < max_wait:
        updated_dials = get_vu_dials(vu_server_url, api_key)
        updated_uids = {uid for uid, _ in updated_dials}
        newly_provisioned = [d["uid"] for d in unprovisioned if d["uid"] in updated_uids]
        if newly_provisioned:
            break
        time.sleep(0.1)

    logger.info(f"Successfully provisioned dials: {newly_provisioned}")
    return newly_provisioned

def vu_server_check(vu_server_url=VU_SERVER_URL, api_key=API_KEY, timeout=0.5):
    try:
        response = requests.get(
            f"{vu_server_url}/api/v0/dial/list",
            params={"key": api_key},
            timeout=timeout
        )
        return response.status_code == 200
    except requests.RequestException:
        return False
