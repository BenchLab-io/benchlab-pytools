"""BENCHLAB PyTools v2 – Tool Definitions & Dependency Helpers.

Defines the CONSUMER_TOOLS registry, LAUNCH_PROFILES, and helpers for
locating tool modules and installing their dependencies.
"""

import importlib
import importlib.util
import logging
from pathlib import Path

from .bootstrap import install_requirements_file

logger = logging.getLogger("benchlab.launcher")


# ──────────────────────────────────────────────────────────────
# Tool Definitions (consumer tools only)
# ──────────────────────────────────────────────────────────────

CONSUMER_TOOLS = {
    "config": {
        "name": "Config Tool",
        "description": "Import/export device configuration",
        "flag": "-config",
        "module": "benchlab.config.config_tool",
        "function": "main",
        "requirements": "requirements.txt",
        "supported_sources": ["direct", "named_pipe"],
    },
    "csv_log": {
        "name": "CSV Logger",
        "description": "Log device telemetry to CSV files",
        "flag": "-logfleet",
        "module": "benchlab.csv_log.csv_logger_enhanced",
        "function": "run_enhanced_csv_logger",
        "requirements": "requirements.txt",
    },
    "graph": {
        "name": "DearPyGui Graph",
        "description": "Interactive sensor graphing widget",
        "flag": "-graph",
        "module": "benchlab.graph.runner",
        "function": "run_graph_mode",
        "requirements": "requirements.txt",
    },
    "hwinfo": {
        "name": "HWiNFO Export",
        "description": "Export sensors to HWiNFO64 custom sensors",
        "flag": "-hwinfo",
        "module": "benchlab.hwinfo.hwinfo_export",
        "function": "export_all_devices",
        "requirements": "requirements.txt",
    },
    "link": {
        "name": "Link",
        "description": "Publish telemetry to BENCHLAB SaaS",
        "flag": "-link",
        "module": "benchlab.link.link_main",
        "function": "run_link",
        "requirements": "requirements.txt",
    },
    "tui": {
        "name": "TUI",
        "description": "Interactive terminal user interface",
        "flag": "-tui",
        "module": "benchlab.tui.tui_main",
        "function": "tui_main",
        "requirements": "requirements.txt",
        "terminal": {"cols": 220, "rows": 70},
    },
    "vu": {
        "name": "VU Dials",
        "description": "Analog-style VU meter dials",
        "flag": "-vu",
        "module": "benchlab.vu.vu_updater",
        "function": "run_updater",
        "requirements": "requirements.txt",
    },
    "vuconfig": {
        "name": "VU Config",
        "description": "VU meter configuration interface",
        "flag": "-vuconfig",
        "module": "benchlab.vu.vu_tui",
        "function": "launch_vu_config",
        "requirements": "requirements.txt",
    },
    "wigidash": {
        "name": "WigiDash",
        "description": "Display telemetry on G.SKILL WigiDash device",
        "flag": "-wigidash",
        "module": "benchlab.wigidash.wigidash_manager",
        "function": "main",
        "requirements": "requirements.txt",
    },
}


# ──────────────────────────────────────────────────────────────
# Launch Profiles
# ──────────────────────────────────────────────────────────────

LAUNCH_PROFILES = {
    "gskill_ctex26": {
        "tools": ["tui", "vu", "wigidash"],
        "source": "fastapi_custom",
    },
}

# ──────────────────────────────────────────────────────────────
# Tool Dependency Helpers
# ──────────────────────────────────────────────────────────────


def get_module_dir(module_name: str) -> Path:
    """Return the directory containing the given module."""
    spec = importlib.util.find_spec(module_name)
    if not spec or not spec.origin:
        raise ImportError(f"Cannot locate module: {module_name}")
    return Path(spec.origin).parent


def ensure_tool_dependencies(tool_id: str) -> None:
    """Install a tool's requirements file if present."""
    tool = CONSUMER_TOOLS[tool_id]
    req = tool.get("requirements")
    if not req:
        return
    module_dir = get_module_dir(tool["module"])
    req_file = module_dir / req
    if req_file.exists():
        install_requirements_file(str(req_file), tool["name"])


def ensure_profile_dependencies(profile_id: str) -> None:
    """Install dependencies for all tools in a profile."""
    profile = LAUNCH_PROFILES.get(profile_id)
    if not profile:
        raise ValueError(f"Unknown profile: {profile_id}")

    for tool_id in profile["tools"]:
        if tool_id in CONSUMER_TOOLS:
            try:
                ensure_tool_dependencies(tool_id)
            except Exception as e:
                logger.warning(
                    f"Failed to install dependencies for {tool_id}: {e}")
