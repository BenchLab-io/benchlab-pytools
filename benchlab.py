#!/usr/bin/env python3

import logging
import os
import subprocess
import sys


# --- Enforce Python version ---
REQUIRED_MAJOR = 3
REQUIRED_MINOR = 13

if sys.version_info[:2] != (REQUIRED_MAJOR, REQUIRED_MINOR):
    sys.stderr.write(
        f"ERROR: BENCHLAB PyTools only supports Python {REQUIRED_MAJOR}.{REQUIRED_MINOR}, "
        f"but you are running Python {sys.version_info.major}.{sys.version_info.minor}\n"
    )
    sys.exit(1)


# --- Logger setup ---
logger = logging.getLogger("benchlab.launcher")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- Prompt to install core requirements ---
def install_core_requirements():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(base_dir, "requirements.txt")
    if os.path.isfile(req_file):
        print(f"\nCore requirements found at:\n  {req_file}")
        choice = input("Install core requirements now? [Y/n]: ").strip().lower()
        if choice in ("", "y", "yes"):
            logger.info("Installing core requirements...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to install core requirements: {e}")
                sys.exit(1)
        else:
            logger.info("User skipped installing core requirements.")
    else:
        logger.warning(f"No requirements.txt found at {req_file}")

# --- Only import benchlab after core requirements ---
install_core_requirements()

try:
    from benchlab.main import get_parser, launch_mode, main
except ModuleNotFoundError as e:
    logger.error(f"Missing module: {e}. Make sure you installed the core requirements first.")
    sys.exit(1)


# --- Modes configuration ---
MODES = {
    "CSV":          {"flag": "-logfleet", "reqs": ["csv_log"],  "desc": "CSV logging",
                        "info": "Logs data from one or multiple devices into CSV files for offline analysis. Supports single device and fleet logging."},
    "FastAPI":      {"flag": "-fastapi", "reqs": ["fastapi"], "desc": "Fast API server",
                        "info": "Launches a FastAPI server to access device telemetry."},
    "Graph":        {"flag": "-graph", "reqs": ["graph"], "desc": "DearPyGui graphing",
                        "info": "Monitor a specific sensor using a graph gui"},
    "HWiNFO":       {"flag": "-hwinfo", "reqs": ["hwinfo"], "desc": "HWiNFO Custom Sensors",
                        "info": "Export all BENCHLAB devices to HWiNFO as custom sensors"},
    "MQTT":         {"flag": "-mqtt", "reqs": ["mqtt"], "desc": "MQTT publisher",
                        "info": "Publishes telemetry data to an MQTT broker, allowing integration with external dashboards."},
    "VU":           {"flag": "-vu", "reqs": ["vu"], "desc": "VU analog dials",
                        "info": "Displays analog-style VU dials for visual monitoring of device metrics."},
    "VU Config":    {"flag": "-vuconfig", "reqs": ["vu"], "desc": "VU configuration UI",
                        "info": "Interactive configuration interface for customizing VU dials and settings."},
    "TUI":          {"flag": "-tui", "reqs": ["tui"], "desc": "Interactive terminal UI",
                        "info": "Displays a live TUI for monitoring connected devices and telemetry. Supports multiple devices."},
    "WigiDash":      {"flag": "-wigidash", "reqs": ["wigidash"], "desc": "WigiDash display support",
                        "info": "Displays telemetry on a WigiDash device. Supports multiple benchlabs and displays."}
}


# --- Utilities ---
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def show_info():
    clear_screen()
    print("=== BENCHLAB PyTools Info ===\n")
    for i, m in enumerate(MODES.keys(), 1):
        print(f"{i}. {m} - {MODES[m]['desc']}")
        print(f"   {MODES[m]['info']}\n")
    input("Press Enter to return to menu or exit...")

def prompt_yes_no(msg, default=True):
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        choice = input(msg + suffix).strip().lower()
        if not choice:
            return default
        if choice in ("y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        print("Please enter Y or N.")


def install_requirements(mods):
    """Install requirements.txt for selected modules, with Y/N prompt and content preview."""
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BENCHLAB_DIR = os.path.join(BASE_DIR, "benchlab")

    for m in mods:
        for tag in MODES[m]["reqs"]:
            tool_dir = os.path.join(BENCHLAB_DIR, tag)
            req_file = os.path.join(tool_dir, "requirements.txt")

            if not os.path.isfile(req_file):
                logger.warning(
                    f"{m}: requirements.txt NOT FOUND\n"
                    f"Expected at: {req_file}"
                )
                continue

            # Print path
            print(f"\n{m} requirements found:")
            print(f"  {req_file}\n")

            # Print contents
            try:
                with open(req_file, "r", encoding="utf-8") as f:
                    contents = f.read().strip()
                    if contents:
                        print("Contents:\n")
                        print(contents)
                        print("\n")
                    else:
                        print("Requirements file is empty.\n")
            except Exception as e:
                logger.warning(f"Could not read {req_file}: {e}")

            # Prompt for installation
            if not prompt_yes_no("Install these requirements?", default=True):
                logger.info(f"{m}: user skipped dependency install.")
                continue

            # Install via pip
            logger.info(f"Installing requirements for {m}...")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-r", req_file]
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"{m}: dependency installation failed.")
                sys.exit(1)


# --- Interactive launcher ---
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


# --- Main ---
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
