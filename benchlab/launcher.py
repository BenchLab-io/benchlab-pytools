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
        service_url=os.environ.get(
            "BENCHLAB_SERVICE_URL", "http://localhost:8585"),
    )


def _monitor_process(tool_name: str, proc: subprocess.Popen) -> None:
    """Read stderr from a child process and log it to the parent terminal."""
    for line in proc.stderr:
        line = line.decode(errors="replace").rstrip()
        if line:
            logger.error(f"[{tool_name}] {line}")


def _terminate_spawned_process(proc: subprocess.Popen, force: bool) -> None:
    """Terminate a spawned terminal-window process (and its children).

    os.killpg/os.getpgid don't exist on Windows, so this mirrors
    ProcessManager's platform branch: taskkill /T kills the whole
    process tree rooted at the terminal emulator's PID on Windows,
    while POSIX uses the process group created via preexec_fn=os.setsid.
    """
    try:
        if not proc or proc.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.killpg(os.getpgid(proc.pid), sig)
    except Exception:
        pass


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
                    f"{tool['name']}: {tool['module']}."
                    f"{tool['function']} takes no args. "
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
    # Linux terminal candidates
    linux_candidates = [
        "ptyxis",
        "kitty",
        "alacritty",
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "x-terminal-emulator",
        "xterm",
    ]

    # Windows terminal candidates
    windows_candidates = [
        "wt",           # Windows Terminal (default on Windows 11)
        "WindowsTerminal",
        "powershell",   # PowerShell (fallback)
        "cmd",          # Command Prompt (fallback)
    ]

    user_term = os.environ.get("TERMINAL")
    if user_term and shutil.which(user_term):
        return user_term

    # Check platform-specific candidates first
    if sys.platform == "win32":
        for term in windows_candidates:
            if shutil.which(term):
                return term
        # Also check Linux terminals that might be installed on Windows (e.g.,
        # via WSL or standalone)
        for term in linux_candidates:
            if shutil.which(term):
                return term
    else:
        for term in linux_candidates:
            if shutil.which(term):
                return term

    return None


def _spawn_tool_in_terminal(
        tool_id: str,
        args: _types.SimpleNamespace) -> subprocess.Popen:
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
            [
                term, "--title", title, "--command",
                f"bash -lc '{shlex.join(cmd)}; exec bash'",
            ],
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

    # --- Windows Terminal (wt) ---
    if term == "wt":
        # Windows Terminal inherits window size from default profile
        # settings. The actual size depends on the user's Windows Terminal
        # profile configuration.
        # For guaranteed sizing, users should configure their default profile
        # with adequate rows/columns in settings.json (profiles.defaults)
        wt_cmd = ["wt",
                  "new-tab",
                  "--title",
                  title,
                  sys.executable,
                  "-m",
                  "benchlab",
                  tool["flag"],
                  "--source",
                  args.source,
                  "--api-url",
                  args.api_url,
                  "--api-port",
                  str(args.api_port),
                  "--mqtt-broker",
                  args.mqtt_broker,
                  "--mqtt-port",
                  str(args.mqtt_port),
                  "--service-url",
                  args.service_url]
        return subprocess.Popen(wt_cmd, env=env, stderr=subprocess.PIPE)

    # --- PowerShell ---
    if term == "powershell":
        # Set explicit window size (120x40) to ensure TUI has enough space
        ps_script = (
            f'$title = "{title}"; '
            f'$command = "{shlex.join(cmd)}"; '
            f'$Host.UI.RawUI.WindowTitle = $title; '
            f'$Host.UI.RawUI.BufferSize = New-Object '
            f'System.Management.Automation.Host.Size(120, 9999); '
            f'$Host.UI.RawUI.WindowSize = New-Object '
            f'System.Management.Automation.Host.Size(120, 40); '
            f'Write-Host "Starting BENCHLAB tool..."; '
            f'Invoke-Expression $command'
        )
        return subprocess.Popen(
            ["powershell", "-NoExit", "-Command", ps_script],
            env=env,
            stderr=subprocess.PIPE,
        )

    # --- Command Prompt (cmd) ---
    if term == "cmd":
        return subprocess.Popen(
            ["start", title, "cmd", "/k", shlex.join(cmd)],
            env=env,
            shell=True,
            stderr=subprocess.PIPE,
        )

    # --- Generic fallback (xterm / x-terminal-emulator) ---
    return subprocess.Popen(
        [term, "-T", title, "-e", *cmd],
        env=env,
        preexec_fn=os.setsid,
        stderr=subprocess.PIPE,
    )


def launch_tools_concurrent(
        tool_ids: List[str],
        source_ready_delay: float = 2.0) -> None:
    """Spawn each tool in its own terminal window, then wait until interrupted.

    Args:
        tool_ids: List of tool IDs to launch.
        source_ready_delay: Time to wait after source is ready before
            launching tools (default: 2.0s).
    """
    args = _build_args_namespace()
    processes: dict = {}
    monitors: list = []

    # Wait for the source to be fully ready before spawning any tools
    if source_ready_delay > 0:
        logger.info(
            f"Waiting {source_ready_delay}s for data source to stabilize "
            "before launching tools...")
        time.sleep(source_ready_delay)

    for idx, tid in enumerate(tool_ids):
        tool = CONSUMER_TOOLS[tid]
        logger.info(f"Launching {tool['name']} in terminal...")
        proc = _spawn_tool_in_terminal(tid, args)

        # Only consider it a failure if the process exited with a non-zero
        # code. Some terminal launchers (like Windows Terminal 'wt') exit
        # immediately with code 0 after successfully launching the
        # terminal window
        if proc.poll() is not None and proc.returncode != 0:
            logger.error(
                f"{tool['name']} terminal failed to launch "
                f"(exit code {proc.returncode})")
            continue

        # Graceful launch: wait between tool spawns to avoid overwhelming
        # the system. First tool launches immediately after source delay,
        # subsequent tools wait 1 second each
        if idx < len(tool_ids) - 1:  # Don't sleep after the last tool
            time.sleep(1.0)

        processes[tid] = proc

        t = threading.Thread(
            target=_monitor_process,
            args=(tool["name"], proc),
            daemon=True,
        )
        t.start()
        monitors.append(t)

    logger.info(
        "All tools launched in terminals. Press Ctrl+C to stop launcher.")

    try:
        while True:
            time.sleep(0.5)
    except (KeyboardInterrupt, EOFError):
        logger.info("Stopping all tools...")
    finally:
        logger.info("Stopping all tools...")

        for proc in processes.values():
            _terminate_spawned_process(proc, force=False)

        time.sleep(1)

        for proc in processes.values():
            _terminate_spawned_process(proc, force=True)

        cleanup_all_services()

    logger.info("Done.")
