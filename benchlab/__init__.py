"""
BENCHLAB Telemetry Package
"""

import os as _os
import sys as _sys

# Ensure the repo root (containing bootstrap.py) is importable. Centralized
# here so menu.py/tools.py don't each need their own sys.path.insert hack.
_repo_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _repo_root not in _sys.path:
    _sys.path.insert(0, _repo_root)

__version__ = "3.0.0"
