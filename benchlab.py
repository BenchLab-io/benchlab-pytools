#!/usr/bin/env python3

import logging
import os
import platform
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


# --- Detect platform ---
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX   = sys.platform.startswith("linux")
IS_MAC     = sys.platform.startswith("darwin")
ARCH       = platform.machine().lower()
IS_ARM     = ARCH.startswith("arm") or ARCH.startswith("aarch")
IS_X86     = not IS_ARM
CURRENT_OS = "windows" if IS_WINDOWS else "linux" if IS_LINUX else "mac"
CURRENT_ARCH = "arm" if IS_ARM else "x86"

logger.info(f"Detected platform: {CURRENT_OS} / {CURRENT_ARCH}")


# --- PyTools requirements installer ---
def install_pytools_requirements():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(base_dir, "requirements.txt")
    if not os.path.isfile(req_file):
        logger.warning(f"No PyTools requirements.txt found at {req_file}")
        return
    logger.info("Checking PyTools requirements...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", req_file]
        )
    except subprocess.CalledProcessError:
        logger.error("PyTools dependency installation failed.")
        sys.exit(1)

install_pytools_requirements()


# --- Ensure packaging is available ---
try:
    from importlib import metadata
    from packaging.requirements import Requirement
    from packaging.version import Version
except ModuleNotFoundError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "packaging"])
    from importlib import metadata
    from packaging.requirements import Requirement
    from packaging.version import Version


# --- Dependency helpers ---
def requirements_satisfied(req_file):
    missing = []
    try:
        with open(req_file, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
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
            if req.specifier and not req.specifier.contains(installed_version, prereleases=True):
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
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", req_file]
        )
        return True
    except subprocess.CalledProcessError:
        logger.error(f"{label}: dependency installation failed.")
        return False


# --- Import benchlab only after PyTools deps ---
try:
    from benchlab.main import get_parser, launch_mode, main as benchlab_main
except ModuleNotFoundError as e:
    logger.error(f"Missing module: {e}. Make sure PyTools requirements are installed.")
    sys.exit(1)


# --- Modes configuration ---
MODES = {
    "CSV": {
        "flag": "-logfleet",
        "reqs": ["csv_log"],
        "desc": "CSV logging",
        "info": "Logs data from one or multiple devices into CSV files for offline analysis."
    },
    "FastAPI": {
        "flag": "-fastapi",
        "reqs": ["fastapi"],
        "desc": "Fast API server",
        "info": "Launches a FastAPI server to access device telemetry."
    },
    "Graph": {
        "flag": "-graph",
        "reqs": ["graph"],
        "desc": "DearPyGui graphing",
        "info": "Monitor a specific sensor using a DearPyGui GUI.",
        "architectures": ["x86", "darwin"]
    },
    "HWiNFO": {
        "flag": "-hwinfo",
        "reqs": ["hwinfo"],
        "desc": "HWiNFO Custom Sensors",
        "info": "Export all BENCHLAB devices to HWiNFO as custom sensors.",
        "platforms": ["windows"],
        "architectures": ["x86"]
    },
    "MQTT": {
        "flag": "-mqtt",
        "reqs": ["mqtt"],
        "desc": "MQTT publisher",
        "info": "Publishes telemetry data to an MQTT broker."
    },
    "VU": {
        "flag": "-vu",
        "reqs": ["vu"],
        "desc": "VU analog dials",
        "info": "Displays analog-style VU dials for monitoring."
    },
    "VU Config": {
        "flag": "-vuconfig",
        "reqs": ["vu"],
        "desc": "VU configuration UI",
        "info": "Interactive configuration interface for VU dials."
    },
    "TUI": {
        "flag": "-tui",
        "reqs": ["tui"],
        "desc": "Interactive terminal UI",
        "info": "Live TUI for monitoring connected devices."
    },
    "WigiDash": {
        "flag": "-wigidash",
        "reqs": ["wigidash"],
        "desc": "WigiDash display support",
        "info": "Displays telemetry on a WigiDash device."
    },
}


# --- Helpers for platform-aware menu ---
def mode_supported(name):
    cfg = MODES[name]
    if "platforms" in cfg and CURRENT_OS not in cfg["platforms"]:
        return False
    if "architectures" in cfg and CURRENT_ARCH not in cfg["architectures"]:
        return False
    return True


def available_modes_for_display():
    """Return all mode names with platform/arch info if unsupported."""
    result = []
    for name in MODES.keys():
        if mode_supported(name):
            result.append(name)
        else:
            result.append(f"{name} [Not available on this platform]")
    return result


def available_modes_for_use():
    """Return only real mode names (strip annotations)"""
    return [name for name in MODES.keys()]


# --- Banner and info ---
def show_info():
    clear_screen()
    print_banner()
    print("=== BENCHLAB PyTools Info ===\n")

    all_modes = list(MODES.keys())
    for i, name in enumerate(available_modes_for_display(), 1):
        mode_key = name.split(" [")[0]
        mode = MODES[mode_key]

        platforms = ", ".join(mode.get("platforms", ["all"]))
        archs = ", ".join(mode.get("architectures", ["all"]))

        # Combine everything in 1-2 lines per mode
        print(f"{i}. {name} — {mode['desc']}")
        print(f"    {mode['info']} (Platforms: {platforms}; Archs: {archs})\n")

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


# --- Installer for selected modes ---
def install_requirements(mods):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    benchlab_dir = os.path.join(base_dir, "benchlab")
    for m in mods:
        if not mode_supported(m):
            logger.info(f"Skipping {m} (unsupported on this platform).")
            continue
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

            modes_list = available_modes_for_display()
            for i, name in enumerate(modes_list, 1):
                print(f"{i}. {name} - {MODES[list(MODES.keys())[i-1]]['desc']}")

            print("\nSelect a feature by number or type 'info':")
            choice = input("> ").strip().lower()

            if choice == "info":
                show_info()
                continue

            try:
                idx = int(choice) - 1
                selected_mode = list(MODES.keys())[idx]
            except (ValueError, IndexError):
                input("Invalid choice. Press Enter to continue...")
                continue

            # Install dependencies for the selected mode
            install_requirements([selected_mode])

            # Launch the mode immediately
            sys.argv = [sys.argv[0], MODES[selected_mode]["flag"]]
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
