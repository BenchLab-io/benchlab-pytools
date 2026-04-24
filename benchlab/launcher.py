"""BENCHLAB PyTools v2 – Tool Launcher.

Provides helpers to build a standard args namespace from environment
variables and to launch one or many consumer tools, either in-process
or in spawned terminal windows.
"""

import curses
import importlib
import inspect
import logging
import os
import subprocess
import sys
import time
import traceback
import types as _types
from typing import List

from .tools import CONSUMER_TOOLS, ensure_tool_dependencies
from .sources import cleanup_all_services

logger = logging.getLogger("benchlab.launcher")


# ──────────────────────────────────────────────────────────────
# Shared Helpers
# ──────────────────────────────────────────────────────────────

def _build_args_namespace() -> _types.SimpleNamespace:
    """Build a standard args namespace from current environment variables."""
    return _types.SimpleNamespace(
        source=os.environ.get("BENCHLAB_DATA_SOURCE", "direct"),
        interval=float(os.environ.get("POLL_INTERVAL", "1.0")),
        api_url=os.environ.get("BENCHLAB_API_URL", "http://127.0.0.1:8000"),
        api_port=int(os.environ.get("API_PORT", "8000")),
        mqtt_broker=os.environ.get("MQTT_BROKER", "localhost"),
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
    )


# ──────────────────────────────────────────────────────────────
# Single-tool Launch (in-process, blocking)
# ──────────────────────────────────────────────────────────────

def launch_single_tool(tool_id: str) -> None:
    """Launch a single tool in-process. Blocks until the tool exits."""
    tool = CONSUMER_TOOLS[tool_id]
    print(f"Starting {tool['name']}...")
    print("Press Ctrl+C to stop.")

    args = _build_args_namespace()

    try:
        ensure_tool_dependencies(tool_id)

        module = importlib.import_module(tool["module"])
        func = getattr(module, tool["function"])

        if tool_id == "tui":
            curses.wrapper(lambda stdscr: func(stdscr, None, args))
        elif tool_id == "xeneon":
            from benchlab.xeneon.xeneon_main import run_xeneon
            run_xeneon(args)
        else:
            sig = inspect.signature(func)
            if sig.parameters:
                func(args)
            else:
                logger.warning(
                    f"{tool['name']}: {tool['module']}.{tool['function']} takes no args. "
                    "Update it to accept an args parameter."
                )
                func()

    except KeyboardInterrupt:
        logger.info(f"{tool['name']} stopped.")
    except Exception as e:
        logger.error(f"{tool['name']} failed: {e}")
        traceback.print_exc()


# ──────────────────────────────────────────────────────────────
# Multi-tool Launch (spawned terminal windows)
# ──────────────────────────────────────────────────────────────

def _spawn_tool_in_terminal(tool_id: str, args: _types.SimpleNamespace) -> subprocess.Popen:
    """Spawn a tool in a new terminal window and return the Popen handle."""
    tool = CONSUMER_TOOLS[tool_id]
    tool_flag = tool["flag"]
    term_cfg = tool.get("terminal", {})

    cmd = [
        sys.executable, "-m", "benchlab",
        tool_flag,
        "--source", args.source,
        "--api-url", args.api_url,
        "--api-port", str(args.api_port),
        "--mqtt-broker", args.mqtt_broker,
        "--mqtt-port", str(args.mqtt_port),
    ]

    env = os.environ.copy()

    if os.name == "nt":
        python_cmd = subprocess.list2cmdline(cmd)
        if term_cfg:
            cols = term_cfg.get("cols", 120)
            rows = term_cfg.get("rows", 50)
            setup = f"mode con cols={cols} lines={rows} && "
        else:
            setup = ""
        full_cmd = f"{setup}{python_cmd}"
        return subprocess.Popen(
            ["cmd", "/c", "start", f"BENCHLAB {tool['name']}", "cmd", "/k", full_cmd],
            shell=False,
        )

    for term in ("x-terminal-emulator", "gnome-terminal", "xterm"):
        try:
            return subprocess.Popen([term, "--"] + cmd, env=env)
        except FileNotFoundError:
            continue

    # Fallback: run in current terminal (no new window)
    return subprocess.Popen(cmd, env=env)


def launch_tools_concurrent(tool_ids: List[str]) -> None:
    """Spawn each tool in its own terminal window, then wait until interrupted."""
    args = _build_args_namespace()
    processes: dict = {}

    for tid in tool_ids:
        tool = CONSUMER_TOOLS[tid]
        logger.info(f"Launching {tool['name']} in terminal...")
        processes[tid] = _spawn_tool_in_terminal(tid, args)

    logger.info("All tools launched in terminals. Press Ctrl+C to stop launcher.")

    try:
        while True:
            time.sleep(0.5)
    except (KeyboardInterrupt, EOFError):
        logger.info("Stopping all tools...")
    finally:
        for proc in processes.values():
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass

        time.sleep(1)

        for proc in processes.values():
            try:
                if proc and proc.poll() is None:
                    proc.kill()
            except Exception:
                pass

        cleanup_all_services()

    logger.info("Done.")