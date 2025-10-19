#!/usr/bin/env python3

import sys
import os
import subprocess
import logging
from benchlab.main import get_parser, launch_mode, main

# --- Logger setup ---
logger = logging.getLogger("benchlab.launcher")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Modes configuration ---
MODES = {
    "CSV":  {"flag": "-logfleet", "reqs": ["csv_log"],  "desc": "CSV logging",
             "info": "Logs data from one or multiple devices into CSV files for offline analysis. Supports single device and fleet logging."},
    "FastAPI":   {"flag": "-fastapi", "reqs": ["fastapi"], "desc": "Fast API server",
                  "info": "Launches a FastAPI server to access device telemetry."},
    "MQTT":      {"flag": "-mqtt", "reqs": ["mqtt"], "desc": "MQTT publisher",
                  "info": "Publishes telemetry data to an MQTT broker, allowing integration with external dashboards."},
    "VU":        {"flag": "-vu", "reqs": ["vu"], "desc": "VU analog dials",
                  "info": "Displays analog-style VU dials for visual monitoring of device metrics."},
    "VU Config": {"flag": "-vuconfig", "reqs": ["vu"], "desc": "VU configuration UI",
                  "info": "Interactive configuration interface for customizing VU dials and settings."},
    "TUI":       {"flag": "-tui", "reqs": ["tui"], "desc": "Interactive terminal UI",
                  "info": "Displays a live TUI for monitoring connected devices and telemetry. Supports multiple devices."},
}

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def show_info():
    clear_screen()
    print("=== BENCHLAB PyTools Info ===\n")
    for i, m in enumerate(MODES.keys(), 1):
        print(f"{i}. {m} - {MODES[m]['desc']}")
        print(f"   {MODES[m]['info']}\n")
    input("Press Enter to return to menu or exit...")

def install_requirements(mods):
    """Install requirements.txt from each tool folder for selected mods."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for m in mods:
        for tag in MODES[m]["reqs"]:
            tool_dir = os.path.join(base_dir, "..", tag)  # assumes subfolders like benchlab/csv_log, benchlab/fastapi
            req_file = os.path.join(tool_dir, "requirements.txt")
            if os.path.exists(req_file):
                logger.info(f"Installing requirements for {m} ({req_file})...")
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
                except subprocess.CalledProcessError as e:
                    logger.error(f"Failed to install {req_file}: {e}")
                    sys.exit(1)
            else:
                logger.info(f"No requirements.txt found for {m} at {req_file}, skipping.")

def interactive_menu():
    try:
        while True:
            clear_screen()
            print("=== BENCHLAB PyTools Launcher ===\n")
            for i, m in enumerate(MODES.keys(), 1):
                print(f"{i}. {m} - {MODES[m]['desc']}")
            print("\nSelect features to enable (e.g. 1,3,5 or 'all') or type 'info' for info:")
            choice = input("> ").strip().lower()

            if choice == "info":
                show_info()
                continue
            elif choice == "all":
                selected = list(MODES.keys())
            else:
                selected = []
                for c in choice.split(","):
                    try:
                        idx = int(c.strip()) - 1
                        selected.append(list(MODES.keys())[idx])
                    except (ValueError, IndexError):
                        logger.warning(f"Invalid choice: {c.strip()}")
                if not selected:
                    logger.error("No valid selections. Please try again.")
                    input("Press Enter to continue...")
                    continue

            # Install requirements for selected modules
            install_requirements(selected)

            # Ask which function to start
            clear_screen()
            print("=== BENCHLAB PyTools Launcher ===\n")
            for i, m in enumerate(selected, 1):
                print(f"{i}. {m} - {MODES[m]['desc']}")
            print("\nWhich function do you want to start?")
            c = input("> ").strip()
            try:
                start = selected[int(c) - 1]
            except (ValueError, IndexError):
                logger.error("Invalid choice, please try again.")
                input("Press Enter to continue...")
                continue

            # Rewrite sys.argv and dispatch to normal main logic
            flag = MODES[start]["flag"]
            sys.argv = [sys.argv[0], flag]
            launch_mode()
            break  # exit menu after launching
    except KeyboardInterrupt:
        logger.info("User interrupted the launcher. Exiting...")
        sys.exit(0)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            interactive_menu()
        elif sys.argv[1].lower() in ("-info", "--info"):
            show_info()
        else:
            main()
    except Exception as e:
        logger.error(f"[BENCHLAB PYTOOLS ERROR] {e}", exc_info=True)
        sys.exit(1)
