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
import shlex
import shutil
import signal
import subprocess
import sys
import threading
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
        service_url=os.environ.get("BENCHLAB_SERVICE_URL", "http://localhost:8585"),
    )


def _monitor_process(tool_name: str, proc: subprocess.Popen) -> None:
    """Read stderr from a child process and log it to the parent terminal."""
    for line in proc.stderr:
        line = line.decode(errors="replace").rstrip()
        if line:
            logger.error(f"[{tool_name}] {line}")


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

def _detect_terminal() -> str | None:
    candidates = [
        "ptyxis",
        "kitty",
        "alacritty",
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "x-terminal-emulator",
        "xterm",
    ]

    user_term = os.environ.get("TERMINAL")
    if user_term and shutil.which(user_term):
        return user_term

    for term in candidates:
        if shutil.which(term):
            return term

    return None


def _spawn_tool_in_terminal(tool_id: str, args: _types.SimpleNamespace) -> subprocess.Popen:
    """Spawn tool in a new isolated terminal window (Linux-first, robust)."""

    tool = CONSUMER_TOOLS[tool_id]
    cmd = [
        sys.executable, "-m", "benchlab",
        tool["flag"],
        "--source", args.source,
        "--api-url", args.api_url,
        "--api-port", str(args.api_port),
        "--mqtt-broker", args.mqtt_broker,
        "--mqtt-port", str(args.mqtt_port),
        "--service-url", args.service_url,
    ]

    env = os.environ.copy()
    term = _detect_terminal()
    if not term:
        raise RuntimeError("No valid terminal emulator found")

    title = f"BENCHLAB - {tool['name']}"

    # --- Ptyxis ---
    if term == "ptyxis":
        return subprocess.Popen(
            [term, "-s", "-T", title, "-x", shlex.join(cmd)],
            env=env,
            preexec_fn=os.setsid,
            stderr=subprocess.PIPE,
        )

    # --- GNOME Terminal ---
    if term == "gnome-terminal":
        return subprocess.Popen(
            [term, "--title", title, "--", *cmd],
            env=env,
            preexec_fn=os.setsid,
            stderr=subprocess.PIPE,
        )

    # --- KDE Konsole ---
    if term == "konsole":
        return subprocess.Popen(
            [term, "--new-tab", "-p", f"tabtitle={title}", "-e", *cmd],
            env=env,
            preexec_fn=os.setsid,
            stderr=subprocess.PIPE,
        )

    # --- XFCE Terminal ---
    if term == "xfce4-terminal":
        return subprocess.Popen(
            [term, "--title", title, "--command", f"bash -lc '{shlex.join(cmd)}; exec bash'"],
            env=env,
            preexec_fn=os.setsid,
            shell=False,
            stderr=subprocess.PIPE,
        )

    # --- Kitty ---
    if term == "kitty":
        return subprocess.Popen(
            [term, "--title", title, *cmd],
            env=env,
            preexec_fn=os.setsid,
            stderr=subprocess.PIPE,
        )

    # --- Alacritty ---
    if term == "alacritty":
        return subprocess.Popen(
            [term, "--title", title, "-e", *cmd],
            env=env,
            preexec_fn=os.setsid,
            stderr=subprocess.PIPE,
        )

    # --- Generic fallback (xterm / x-terminal-emulator) ---
    return subprocess.Popen(
        [term, "-T", title, "-e", *cmd],
        env=env,
        preexec_fn=os.setsid,
        stderr=subprocess.PIPE,
    )


def launch_tools_concurrent(tool_ids: List[str]) -> None:
    """Spawn each tool in its own terminal window, then wait until interrupted."""
    args = _build_args_namespace()
    processes: dict = {}
    monitors: list = []

    for tid in tool_ids:
        tool = CONSUMER_TOOLS[tid]
        logger.info(f"Launching {tool['name']} in terminal...")
        proc = _spawn_tool_in_terminal(tid, args)

        time.sleep(0.5)
        if proc.poll() is not None:
            logger.error(f"{tool['name']} terminal failed to launch (exit code {proc.returncode})")
            continue

        processes[tid] = proc

        t = threading.Thread(
            target=_monitor_process,
            args=(tool["name"], proc),
            daemon=True,
        )
        t.start()
        monitors.append(t)

    logger.info("All tools launched in terminals. Press Ctrl+C to stop launcher.")

    try:
        while True:
            time.sleep(0.5)
    except (KeyboardInterrupt, EOFError):
        logger.info("Stopping all tools...")
    finally:
        logger.info("Stopping all tools...")

        for proc in processes.values():
            try:
                if proc and proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass

        time.sleep(1)

        for proc in processes.values():
            try:
                if proc and proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass

        cleanup_all_services()

    logger.info("Done.")