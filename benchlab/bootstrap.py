#!/usr/bin/env python3
"""Shared bootstrap utilities for fast startup and dependency management."""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Set

# -----------------------------
# Constants
# -----------------------------

REQUIRED_MAJOR = 3
REQUIRED_MINOR = 10

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform.startswith("darwin")

BASE_DIR = Path(__file__).resolve().parent

# Global caches - single source of truth for entire application
_REQ_CACHE: Dict[str, Tuple[bool, List[str]]] = {}
_INSTALLED_REQ_FILES: Set[str] = set()

logger = logging.getLogger("benchlab.bootstrap")


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
# Dependency handling
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
            lines = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
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
            if req.specifier and not req.specifier.contains(
                    installed, prereleases=True):
                missing.append(f"{req} (installed {installed})")
        except metadata.PackageNotFoundError:
            missing.append(str(req))

    result = (not missing, missing)
    _REQ_CACHE[req_file] = result
    return result


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
        _REQ_CACHE.pop(req_file, None)  # invalidate stale result
        _INSTALLED_REQ_FILES.add(req_file)
        return True
    except subprocess.CalledProcessError:
        logger.error(f"{label}: install failed")
        return False


def install_core_requirements():
    req_file = BASE_DIR / "requirements.txt"

    if not req_file.exists():
        return

    install_requirements_file(str(req_file), "CORE")
