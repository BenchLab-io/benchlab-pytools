#!/usr/bin/env python3
"""Benchlab PyTools launcher (optimized fast-start version)."""

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Set

# -----------------------------
# Constants / fast globals
# -----------------------------

REQUIRED_MAJOR = 3
REQUIRED_MINOR = 10

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform.startswith("darwin")

ARCH = platform.machine().lower()
IS_ARM = ARCH.startswith("arm") or ARCH.startswith("aarch")
CURRENT_OS = "windows" if IS_WINDOWS else "linux" if IS_LINUX else "mac"
CURRENT_ARCH = "arm" if IS_ARM else "x86"

BASE_DIR = Path(__file__).resolve().parent
BENCHLAB_DIR = BASE_DIR / "benchlab"

_REQ_CACHE: Dict[str, Tuple[bool, List[str]]] = {}
_INSTALLED_REQ_FILES: Set[str] = set()

benchlab_main = None  # lazy-loaded


# -----------------------------
# Logger (lightweight setup)
# -----------------------------

logger = logging.getLogger("benchlab.launcher")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)


# -----------------------------
# Python version check (fast fail)
# -----------------------------

def check_python_version() -> None:
    if sys.version_info < (REQUIRED_MAJOR, REQUIRED_MINOR):
        sys.stderr.write(
            f"ERROR: Requires Python {REQUIRED_MAJOR}.{REQUIRED_MINOR}+ "
            f"(found {sys.version_info.major}.{sys.version_info.minor})\n"
        )
        sys.exit(1)


# -----------------------------
# Utilities
# -----------------------------

def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def prompt_yes_no(msg: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        choice = input(msg + suffix).strip().lower()
        if not choice:
            return default
        if choice in ("y", "yes"):
            return True
        if choice in ("n", "no"):
            return False


def pip_install(args: List[str]) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install"] + args)


# -----------------------------
# Dependency handling (lazy)
# -----------------------------

def requirements_satisfied(req_file: str) -> Tuple[bool, List[str]]:
    if req_file in _REQ_CACHE:
        return _REQ_CACHE[req_file]

    missing: List[str] = []

    try:
        from importlib import metadata
        from packaging.requirements import Requirement
        from packaging.version import Version
        from packaging.markers import Marker
    except ModuleNotFoundError:
        pip_install(["packaging"])
        from importlib import metadata
        from packaging.requirements import Requirement
        from packaging.version import Version
        from packaging.markers import Marker

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

        if req.marker and not Marker(str(req.marker)).evaluate():
            continue

        try:
            installed = Version(metadata.version(req.name))
            if req.specifier and not req.specifier.contains(installed, prereleases=True):
                missing.append(f"{req} (installed {installed})")
        except metadata.PackageNotFoundError:
            missing.append(str(req))

    result = (not missing, missing)
    _REQ_CACHE[req_file] = result
    return result

def install_core_requirements():
    req_file = BASE_DIR / "requirements.txt"

    if not req_file.exists():
        logger.warning("No global requirements.txt found")
        return

    install_requirements_file(str(req_file), "CORE")


def install_requirements_file(req_file: str, label: str) -> bool:
    if req_file in _INSTALLED_REQ_FILES:
        return True

    ok, missing = requirements_satisfied(req_file)
    if ok:
        return True

    print(f"\n[{label}] Missing dependencies:")
    for m in missing:
        print(f"  - {m}")

    if not prompt_yes_no("Install missing requirements?"):
        return False

    try:
        pip_install(["--disable-pip-version-check", "-r", req_file])
        _INSTALLED_REQ_FILES.add(req_file)
        return True
    except subprocess.CalledProcessError:
        logger.error(f"{label}: install failed")
        return False


# -----------------------------
# Modes config
# -----------------------------

MODES = {
    "CSV": {
        "flag": "-logfleet",
        "reqs": ["csv_log"],
        "desc": "CSV logging",
        "info": "Logs telemetry to CSV files.",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
    },
    "FastAPI": {
        "flag": "-fastapi",
        "reqs": ["fastapi"],
        "desc": "FastAPI server",
        "info": "Telemetry API server.",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
    },
    "Graph": {
        "flag": "-graph",
        "reqs": ["graph"],
        "desc": "GUI graphing",
        "info": "Sensor visualization GUI.",
        "platforms": ["windows", "linux"],
        "architectures": ["x86"],
    },
}


# -----------------------------
# Platform filtering
# -----------------------------

def mode_supported(name: str) -> bool:
    cfg = MODES[name]
    if CURRENT_OS not in cfg.get("platforms", []):
        return False
    if CURRENT_ARCH not in cfg.get("architectures", []):
        return False
    return True


# -----------------------------
# UI
# -----------------------------

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


def show_info():
    clear_screen()
    print_banner()
    print("Modes:\n")

    for i, (name, cfg) in enumerate(MODES.items(), 1):
        status = "" if mode_supported(name) else " (unsupported)"
        print(f"{i}. {name}{status}")
        print(f"   {cfg['desc']}")
        print(f"   {cfg['info']}\n")

    input("Press Enter...")


# -----------------------------
# Lazy benchlab loader
# -----------------------------

def load_benchlab():
    global benchlab_main
    from benchlab.main import main as benchlab_main


# -----------------------------
# Requirement installer per mode
# -----------------------------

def install_mode_requirements(mode_names: List[str]) -> None:
    for m in mode_names:
        if not mode_supported(m):
            continue

        cfg = MODES[m]
        for tag in cfg["reqs"]:
            req_file = BENCHLAB_DIR / tag / "requirements.txt"
            if req_file.exists():
                install_requirements_file(str(req_file), m)


# -----------------------------
# Interactive mode
# -----------------------------

def interactive_menu():
    try:
        clear_screen()
        print_banner()

        load_benchlab()
        benchlab_main()

    except KeyboardInterrupt:
        print("\nExited.")


# -----------------------------
# MAIN ENTRY (FAST START)
# -----------------------------

def main():
    check_python_version()

    install_core_requirements()

    if len(sys.argv) == 1:
        interactive_menu()
        return

    arg = sys.argv[1].lower()

    if arg in ("-info", "--info"):
        show_info()
        return

    if arg.startswith("-"):
        matched = None
        for name, cfg in MODES.items():
            if cfg["flag"] == arg:
                matched = name
                break

        if matched:
            install_mode_requirements([matched])
            load_benchlab()
            benchlab_main()
        else:
            logger.warning(f"Unknown flag: {arg}")
            load_benchlab()
            benchlab_main()
        return

    load_benchlab()
    benchlab_main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)