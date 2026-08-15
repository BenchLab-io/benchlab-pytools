"""BENCHLAB PyTools v2 – Interactive Menu Preferences.

Persists the last-used tool(s)/source/connection params between runs of
the interactive menu, so a repeat launch can default to "press Enter to
repeat" instead of re-asking everything from scratch.

Stored as plain JSON in the repo root (gitignored) — not meant to be
portable or synced, just a local convenience.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("benchlab.launcher")

PREFS_FILE = Path(__file__).resolve().parent.parent / ".benchlab_prefs.json"

_DEFAULTS: Dict[str, Any] = {
    "last_tool_ids": [],
    "last_source": None,
    "source_params": {},   # source_type -> {param: value}
}


def load_prefs() -> Dict[str, Any]:
    """Load persisted menu preferences, tolerating a missing/corrupt file."""
    if not PREFS_FILE.exists():
        return dict(_DEFAULTS)
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged
    except (OSError, json.JSONDecodeError) as e:
        logger.debug(f"Could not load menu preferences ({e}) — starting fresh")
        return dict(_DEFAULTS)


def save_prefs(prefs: Dict[str, Any]) -> None:
    """Persist menu preferences. Failure to save is non-fatal."""
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except OSError as e:
        logger.debug(f"Could not save menu preferences: {e}")


def record_launch(tool_ids: List[str],
                  source: str,
                  source_params: Optional[Dict[str,
                                               Any]] = None) -> None:
    """Record a successful tool/source selection for next run's defaults."""
    prefs = load_prefs()
    prefs["last_tool_ids"] = list(tool_ids)
    prefs["last_source"] = source
    if source_params:
        prefs.setdefault("source_params", {})[source] = source_params
    save_prefs(prefs)


def get_source_params(source: str) -> Dict[str, Any]:
    """Return remembered connection params for a given source type, if any."""
    prefs = load_prefs()
    return prefs.get("source_params", {}).get(source, {})
