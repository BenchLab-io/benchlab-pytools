# benchlab/vu/vu_server_config.py

from pathlib import Path
import json

config_path = Path(__file__).parent / "vu_server.config"

def update_vu_config(dial_uid, dial_name, device, sensor):
    # Load existing config
    if config_path.exists():
        with open(config_path, "r") as f:
            config = json.load(f)
    else:
        config = {"vu_server_url": "http://localhost:5340", "api_key": "", "mappings": [], "update_interval_sec": 1}

    # Remove any mapping with the same dial_uid
    existing = config.get("mappings", [])
    filtered = [m for m in existing if m["dial_uid"] != dial_uid]

    # Add new mapping
    filtered.append({
        "dial_uid": dial_uid,
        "dial_name": dial_name,
        "benchlab_uid": device["uid"],
        "benchlab_port": device["port"],
        "sensor": sensor
    })

    # Update config
    config["mappings"] = filtered

    # Write back to file
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print("[INFO] vu_server.config updated.")
