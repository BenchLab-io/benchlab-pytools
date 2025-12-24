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


# --- Utilities ---
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


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


# --- Core requirements installer (NO packaging imports yet) ---
def install_core_requirements():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(base_dir, "requirements.txt")

    if not os.path.isfile(req_file):
        logger.warning(f"No core requirements.txt found at {req_file}")
        return

    logger.info("Checking core requirements...")

    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                req_file,
            ]
        )
    except subprocess.CalledProcessError:
        logger.error("Core dependency installation failed.")
        sys.exit(1)


# --- Ensure core deps BEFORE importing packaging ---
install_core_requirements()

from importlib import metadata
from packaging.requirements import Requirement
from packaging.version import Version


# --- Dependency helpers ---
def requirements_satisfied(req_file):
    """
    Check whether all requirements in req_file are installed
    and satisfy version constraints.

    Returns:
        (ok: bool, missing: list[str])
    """
    missing = []

    try:
        with open(req_file, "r", encoding="utf-8") as f:
            lines = [
                l.strip()
                for l in f
                if l.strip() and not l.startswith("#")
            ]
    except OSError:
        return True, []

    for line in lines:
        try:
            req = Requirement(line)
        except Exception:
            missing.append(line)
            continue

        try:
            installed_version = Version(metadata.version(req.name))
            if req.specifier and not req.specifier.contains(
                installed_version, prereleases=True
            ):
                missing.append(f"{req} (installed: {installed_version})")
        except metadata.PackageNotFoundError:
            missing.append(str(req))

    return not missing, missing


def install_requirements_file(req_file, label):
    ok, missing = requirements_satisfied(req_file)

    if ok:
        logger.info(f"{label}: requirements already satisfied.")
        return True

    print(f"\n{label} missing dependencies:\n")
    for dep in missing:
        print(f"  - {dep}")

    if not prompt_yes_no("\nInstall missing requirements?", default=True):
        return False

    logger.info(f"Installing {label} requirements...")
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                req_file,
            ]
        )
        return True
    except subprocess.CalledProcessError:
        logger.error(f"{label}: dependency installation failed.")
        return False


# --- Import benchlab only after core deps ---
try:
    from benchlab.main import get_parser, launch_mode, main as benchlab_main
except ModuleNotFoundError as e:
    logger.error(f"Missing module: {e}. Make sure core requirements are installed.")
    sys.exit(1)


# --- Modes configuration ---
MODES = {
    "CSV": {
        "flag": "-logfleet",
        "reqs": ["csv_log"],
        "desc": "CSV logging",
        "info": "Logs data from one or multiple devices into CSV files for offline analysis.",
    },
    "FastAPI": {
        "flag": "-fastapi",
        "reqs": ["fastapi"],
        "desc": "Fast API server",
        "info": "Launches a FastAPI server to access device telemetry.",
    },
    "Graph": {
        "flag": "-graph",
        "reqs": ["graph"],
        "desc": "DearPyGui graphing",
        "info": "Monitor a specific sensor using a graph GUI.",
    },
    "HWiNFO": {
        "flag": "-hwinfo",
        "reqs": ["hwinfo"],
        "desc": "HWiNFO Custom Sensors",
        "info": "Export all BENCHLAB devices to HWiNFO as custom sensors.",
    },
    "MQTT": {
        "flag": "-mqtt",
        "reqs": ["mqtt"],
        "desc": "MQTT publisher",
        "info": "Publishes telemetry data to an MQTT broker.",
    },
    "VU": {
        "flag": "-vu",
        "reqs": ["vu"],
        "desc": "VU analog dials",
        "info": "Displays analog-style VU dials for monitoring.",
    },
    "VU Config": {
        "flag": "-vuconfig",
        "reqs": ["vu"],
        "desc": "VU configuration UI",
        "info": "Interactive configuration interface for VU dials.",
    },
    "TUI": {
        "flag": "-tui",
        "reqs": ["tui"],
        "desc": "Interactive terminal UI",
        "info": "Live TUI for monitoring connected devices.",
    },
    "WigiDash": {
        "flag": "-wigidash",
        "reqs": ["wigidash"],
        "desc": "WigiDash display support",
        "info": "Displays telemetry on a WigiDash device.",
    },
}


def show_info():
    clear_screen()
    print("=== BENCHLAB PyTools Info ===\n")
    for i, m in enumerate(MODES.keys(), 1):
        print(f"{i}. {m} - {MODES[m]['desc']}")
        print(f"   {MODES[m]['info']}\n")
    input("Press Enter to return...")


def print_banner():
    print(r"""
██████╗ ███████╗███╗   ██╗ ██████╗██╗  ██╗██╗      █████╗ ██████╗
██╔══██╗██╔════╝████╗  ██║██╔════╝██║  ██║██║     ██╔══██╗██╔══██╗
██████╔╝█████╗  ██╔██╗ ██║██║     ███████║██║     ███████║██████╔╝
██╔══██╗██╔══╝  ██║╚██╗██║██║     ██╔══██║██║     ██╔══██║██╔══██╗
██████╔╝███████╗██║ ╚████║╚██████╗██║  ██║███████╗██║  ██║██████╔╝
╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═════╝

        ██████╗ ██╗   ██╗████████╗ ██████╗  ██████╗ ██╗     ███████╗
        ██╔══██╗╚██╗ ██╔╝╚══██╔══╝██╔═══██╗██╔═══██╗██║     ██╔════╝
        ██████╔╝ ╚████╔╝    ██║   ██║   ██║██║   ██║██║     ███████╗
        ██╔═══╝   ╚██╔╝     ██║   ██║   ██║██║   ██║██║     ╚════██║
        ██║        ██║      ██║   ╚██████╔╝╚██████╔╝███████╗███████║
        ╚═╝        ╚═╝      ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝╚══════╝
""")


def install_requirements(mods):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    benchlab_dir = os.path.join(base_dir, "benchlab")

    for m in mods:
        for tag in MODES[m]["reqs"]:
            req_file = os.path.join(benchlab_dir, tag, "requirements.txt")

            if not os.path.isfile(req_file):
                logger.warning(f"{m}: no requirements.txt found for {tag}")
                continue

            if not install_requirements_file(req_file, m):
                logger.warning(f"{m}: dependencies missing, feature may not work.")


# --- Interactive launcher ---
def interactive_menu():
    try:
        while True:
            clear_screen()
            print_banner()
            print("=== BENCHLAB PyTools Launcher ===\n")

            for i, m in enumerate(MODES.keys(), 1):
                print(f"{i}. {m} - {MODES[m]['desc']}")

            print("\nSelect features (e.g. 1,3,5 or 'all') or type 'info':")
            choice = input("> ").strip().lower()

            if choice == "info":
                show_info()
                continue

            if choice == "all":
                selected = list(MODES.keys())
            else:
                selected = []
                for c in choice.split(","):
                    try:
                        selected.append(list(MODES.keys())[int(c.strip()) - 1])
                    except (ValueError, IndexError):
                        logger.warning(f"Invalid choice: {c.strip()}")

            if not selected:
                input("No valid selections. Press Enter to continue...")
                continue

            install_requirements(selected)

            clear_screen()
            print("Which function do you want to start?\n")
            for i, m in enumerate(selected, 1):
                print(f"{i}. {m}")

            try:
                start = selected[int(input("> ").strip()) - 1]
            except (ValueError, IndexError):
                input("Invalid choice. Press Enter to continue...")
                continue

            sys.argv = [sys.argv[0], MODES[start]["flag"]]
            launch_mode()
            break

    except KeyboardInterrupt:
        logger.info("User interrupted launcher.")
        sys.exit(0)


# --- Main ---
if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            interactive_menu()
        elif sys.argv[1].lower() in ("-info", "--info"):
            show_info()
        else:
            benchlab_main()
    except Exception as e:
        logger.error(f"[BENCHLAB PYTOOLS ERROR] {e}", exc_info=True)
        sys.exit(1)
