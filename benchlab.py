#!/usr/bin/env python3
"""Benchlab PyTools launcher."""

import sys
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Minimal fast setup
logger = logging.getLogger("benchlab.launcher")
logger.setLevel(logging.INFO)

# Only add StreamHandler if not running TUI mode
# Check for -tui flag in arguments to avoid stdout interference with curses
if not logger.handlers:
    _has_tui = '-tui' in sys.argv
    if not _has_tui:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)


def main():
    sys.path.insert(0, str(BASE_DIR))

    # Fast fail checks first
    from benchlab.bootstrap import (
        check_python_version, install_core_requirements,
    )
    check_python_version()
    install_core_requirements()

    # Delegate everything to main entry point
    from benchlab.main import main
    main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
