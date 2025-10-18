import subprocess
import threading
import time
import requests
import yaml
import json
import signal
import logging
import shutil
from pathlib import Path
import sys
import platform
import os

# -----------------------------------------------------------------------------
# Platform setup
# -----------------------------------------------------------------------------
IS_WINDOWS = platform.system() == "Windows"
PYTHON_CMD = "python" if IS_WINDOWS else "python3"
CREATIONFLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
PREEXEC_FN = None if IS_WINDOWS else lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
VU_SERVER_DIR = BASE_DIR / "VU-Server"
VU_SERVER_CONFIG = BASE_DIR / "vu_server.config"
SERVER_YAML_CONFIG = VU_SERVER_DIR / "config.yaml"
STANDARD_LOGO = BASE_DIR / "assets" / "bl_logo_144x200.png"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("vu_server_manager")

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def check_vu_server(server_url: str, api_key: str = "") -> bool:
    """Return True if the VU server responds successfully."""
    try:
        headers = {"X-Master-Key": api_key} if api_key else {}
        r = requests.get(f"{server_url}/api/v0/dial/list", headers=headers, timeout=1)
        return r.status_code in (200, 403)
    except requests.RequestException:
        return False

def forward_logs(proc: subprocess.Popen):
    """Forward VU server stdout to the main logger."""
    if not proc.stdout:
        logger.warning("No stdout to forward from VU server.")
        return
    for line in proc.stdout:
        if line := line.rstrip():
            logger.info(f"[VU SERVER] {line}")

# -----------------------------------------------------------------------------
# Server startup
# -----------------------------------------------------------------------------
def start_vu_server() -> subprocess.Popen | None:
    """
    Ensure a VU server is running.
    Returns a subprocess.Popen handle if a new server was started, or None if already running.
    """
    logger.info("Checking for existing VU server...")

    # Load previous JSON config
    server_cfg = {}
    if VU_SERVER_CONFIG.exists():
        try:
            server_cfg = json.loads(VU_SERVER_CONFIG.read_text())
        except Exception as e:
            logger.warning(f"Failed to read {VU_SERVER_CONFIG}: {e}")

    url = server_cfg.get("vu_server_url", "http://localhost:5340")
    api_key = server_cfg.get("api_key", "")

    if check_vu_server(url, api_key):
        logger.info(f"VU server already running at {url}")
        return None

    logger.info("No VU server found — starting one...")

    # Load YAML config
    try:
        cfg = yaml.safe_load(SERVER_YAML_CONFIG.read_text())
    except Exception as e:
        logger.error(f"Failed to read {SERVER_YAML_CONFIG}: {e}")
        raise

    host = cfg.get("server", {}).get("hostname", "localhost")
    port = cfg.get("server", {}).get("port", 5340)
    master_key = cfg.get("server", {}).get("master_key", "")

    new_cfg = {
        "vu_server_url": f"http://{host}:{port}",
        "api_key": master_key,
        "logo_file": str(server_cfg.get("logo_file", "assets/bl_logo_144x200.png"))
    }

    try:
        VU_SERVER_CONFIG.write_text(json.dumps(new_cfg, indent=2))
        logger.info(f"Updated {VU_SERVER_CONFIG} with {new_cfg['vu_server_url']}")
    except Exception as e:
        logger.warning(f"Failed to write {VU_SERVER_CONFIG}: {e}")

    # Ensure VU-Server directory exists
    if not VU_SERVER_DIR.exists():
        logger.error(f"Missing server directory: {VU_SERVER_DIR}")
        raise FileNotFoundError(VU_SERVER_DIR)

    # Launch server
    proc = subprocess.Popen(
        [PYTHON_CMD, "-u", str(VU_SERVER_DIR / "server.py")],
        cwd=str(VU_SERVER_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        creationflags=CREATIONFLAGS,
        preexec_fn=os.setsid if not IS_WINDOWS else None,
    )

    threading.Thread(target=forward_logs, args=(proc,), daemon=True).start()

    # Wait for server to become ready
    for _ in range(10):
        time.sleep(1)
        if check_vu_server(new_cfg["vu_server_url"], new_cfg["api_key"]):
            logger.info(f"VU server is now running at {new_cfg['vu_server_url']}")
            break
    else:
        logger.error("Failed to verify VU server after startup.")

    return proc

# -----------------------------------------------------------------------------
# Shutdown
# -----------------------------------------------------------------------------
def terminate_vu_server(proc: subprocess.Popen | None):
    if not proc or proc.poll() is not None:
        return
    logger.info("Terminating auto-started VU server...")
    try:
        if IS_WINDOWS:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        proc.wait(timeout=5)
    except Exception as e:
        logger.warning(f"Failed to terminate VU server cleanly: {e}")
