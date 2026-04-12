#!/usr/bin/env python3
"""Benchlab PyTools launcher.

This script provides a command‑line entry point for the Benchlab suite. It
detects the current platform, ensures required Python packages are installed,
and dispatches to the appropriate sub‑tool based on the supplied flag.

The original implementation was functional but lacked type hints, docstrings,
and a few usability improvements. The changes in this patch add documentation,
type annotations, a ``--debug`` flag to increase log verbosity, better handling
of unknown flags, and deduplication of requirement installations.
"""

import logging
import os
import platform
import subprocess
import sys
from typing import List, Tuple


# --- Enforce Python version ---
REQUIRED_MAJOR = 3
REQUIRED_MINOR = 10  # Minimum required (BENCH-LAUNCH-1: was 13, too restrictive)

if sys.version_info < (REQUIRED_MAJOR, REQUIRED_MINOR):
    sys.stderr.write(
        f"ERROR: BENCHLAB PyTools requires Python {REQUIRED_MAJOR}.{REQUIRED_MINOR} or higher, "
        f"but you are running Python {sys.version_info.major}.{sys.version_info.minor}\n"
    )
    sys.exit(1)


# --- Logger setup ---
logger = logging.getLogger("benchlab.launcher")
# Default to INFO; can be overridden with a "--debug" flag before any other processing.
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Enable debug logging if the user passed "--debug" as the first argument.
if len(sys.argv) > 1 and sys.argv[1] in ("--debug", "-debug"):
    logger.setLevel(logging.DEBUG)
    logger.debug("Debug logging enabled via command‑line flag.")


# --- Utilities ---
def clear_screen() -> None:
    """Clear the terminal screen.

    Uses ``cls`` on Windows and ``clear`` on POSIX systems. Any ``KeyboardInterrupt``
    raised while the command runs is ignored so the caller can continue gracefully.
    """
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except KeyboardInterrupt:
        pass


def prompt_yes_no(msg: str, default: bool = True) -> bool:
    """Prompt the user for a yes/no answer.

    Args:
        msg: The question to display.
        default: The default value returned when the user simply presses ``Enter``.

    Returns:
        ``True`` for a yes answer, ``False`` for no.
    """
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
def install_pytools_requirements() -> None:
    """Install top‑level Benchlab PyTools requirements if they are missing.

    The function checks the project's ``requirements.txt`` and installs any
    missing packages. It is called after all helper functions are defined to
    avoid forward‑reference errors.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(base_dir, "requirements.txt")
    if not os.path.isfile(req_file):
        logger.warning(f"No PyTools requirements.txt found at {req_file}")
        return
    ok, missing = requirements_satisfied(req_file)
    if ok:
        logger.info("PyTools requirements already satisfied.")
        return
    logger.info("Installing missing PyTools requirements...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", req_file]
        )
    except subprocess.CalledProcessError:
        logger.error("PyTools dependency installation failed.")
        sys.exit(1)


# --- Ensure packaging is available ---
try:
    from importlib import metadata
    from packaging.requirements import Requirement
    from packaging.version import Version
    from packaging.markers import Marker
except ModuleNotFoundError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "packaging"])
    from importlib import metadata
    from packaging.requirements import Requirement
    from packaging.version import Version
    from packaging.markers import Marker


# --- Dependency helpers ---
def requirements_satisfied(req_file: str) -> Tuple[bool, List[str]]:
    """Check whether all requirements in *req_file* are satisfied.

    Returns a tuple ``(ok, missing)`` where ``ok`` is ``True`` when every
    requirement is present and ``missing`` is a list of human‑readable strings
    describing the unsatisfied entries.
    """
    missing: List[str] = []
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

        # Skip if marker doesn't match current environment
        if req.marker is not None:
            marker = Marker(str(req.marker))
            if not marker.evaluate():
                continue

        try:
            installed_version = Version(metadata.version(req.name))
            if req.specifier and not req.specifier.contains(installed_version, prereleases=True):
                missing.append(f"{req} (installed: {installed_version})")
        except metadata.PackageNotFoundError:
            missing.append(str(req))

    return not missing, missing


def install_requirements_file(req_file: str, label: str) -> bool:
    """Prompt the user to install missing dependencies for *label*.

    Returns ``True`` when the requirements are satisfied (either already or
    after a successful installation), ``False`` otherwise.
    """
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
    from benchlab.main import get_parser, launch_mode, interactive_loop, main as benchlab_main
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
    "Xeneon": {
        "flag": "-xeneon",
        "reqs": ["xeneon"],
        "desc": "Web dashboard",
        "info": "Launch Xeneon web dashboard for telemetry monitoring.",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"]
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
def install_requirements(mods: List[str]) -> None:
    """Install requirements for the selected *mods*.

    The function respects platform/architecture constraints and avoids installing
    the same requirement file multiple times by tracking already‑processed tags.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    benchlab_dir = os.path.join(base_dir, "benchlab")
    processed_tags: set = set()
    for m in mods:
        if not mode_supported(m):
            logger.info(f"Skipping {m} (unsupported on this platform).")
            continue
        for tag in MODES[m]["reqs"]:
            if tag in processed_tags:
                continue
            processed_tags.add(tag)
            req_file = os.path.join(benchlab_dir, tag, "requirements.txt")
            if not os.path.isfile(req_file):
                logger.warning(f"{m}: no requirements.txt found for {tag}")
                continue
            if not install_requirements_file(req_file, m):
                logger.warning(f"{m}: dependencies missing, feature may not work.")


# --- Interactive launcher (v2) ---
def interactive_menu() -> None:
    """Delegate to the PyTools v2 interactive launcher.

    The screen is cleared before and after the interactive loop to keep the
    terminal tidy. ``KeyboardInterrupt`` is caught to exit gracefully.
    """
    try:
        clear_screen()
        print_banner()
        interactive_loop()
    except KeyboardInterrupt:
        logger.info("User interrupted launcher.")
        sys.exit(0)
    finally:
        clear_screen()



# --- Main ---
if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            interactive_menu()

        elif sys.argv[1].lower() in ("-info", "--info"):
            show_info()

        elif sys.argv[1].startswith("-"):
            flag = sys.argv[1].lower()
            matched_mode = None

            for name, cfg in MODES.items():
                if cfg["flag"] == flag:
                    matched_mode = name
                    break

            if matched_mode:
                install_requirements([matched_mode])
            else:
                logger.warning(f"Unknown mode flag: {flag}")

            benchlab_main()

        else:
            # Fallback (should rarely happen)
            benchlab_main()

    except Exception as e:
        logger.error(f"[BENCHLAB PYTOOLS ERROR] {e}", exc_info=True)
        sys.exit(1)
