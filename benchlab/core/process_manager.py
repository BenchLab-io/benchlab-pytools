"""
Process Manager for BENCHLAB

Structured subprocess management for the infrastructure services
(FastAPI server, MQTT publisher, embedded amqtt broker).

Replaces the ad-hoc ``subprocess.Popen`` + env-var PID pattern in main.py
with proper lifecycle management: start, health-check, stop, restart,
and graceful shutdown of all managed processes.

Usage:
    pm = ProcessManager.get_instance()

    # Start a managed service
    pm.start_service(
        name="fastapi",
        cmd=["python", "-m", "benchlab", "fastapi"],
        health_check=lambda: _ping_http("http://127.0.0.1:8000/health"),
        timeout=20,
    )

    # Stop a specific service
    pm.stop_service("fastapi")

    # Stop everything (called on tool exit)
    pm.shutdown_all()
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

logger = logging.getLogger("benchlab.core.process_manager")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ManagedProcess:
    """State for a single managed subprocess."""

    name: str
    cmd: list[str]
    process: Optional[subprocess.Popen] = None
    health_check: Optional[Callable[[], bool]] = None
    stdout_log: str = ""
    stderr_log: str = ""
    started_at: float = 0.0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class ProcessManager:
    """Thread-safe singleton that manages infrastructure service lifecycles."""

    _instance: Optional["ProcessManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._services: Dict[str, ManagedProcess] = {}
        self._mutex = threading.Lock()

    # -- singleton helpers ---------------------------------------------------

    @classmethod
    def get_instance(cls) -> "ProcessManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (primarily for testing)."""
        with cls._lock:
            inst = cls._instance
            if inst is not None:
                inst.shutdown_all()
                cls._instance = None

    # -- service management --------------------------------------------------

    def start_service(
        self,
        name: str,
        cmd: list[str] | str,
        health_check: Optional[Callable[[], bool]] = None,
        timeout: float = 20.0,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> bool:
        """Start a service and wait for it to become healthy.

        Args:
            name: Human-readable service label (must be unique).
            cmd: Command string or list of arguments.
            health_check: Callable returning True when the service is ready.
                          If ``None``, success is determined by the process
                          staying alive for *timeout* seconds.
            timeout: Seconds to wait for health_check to succeed.
            cwd: Working directory for the subprocess.
            env: Environment variables (defaults to os.environ copy).

        Returns:
            ``True`` if the service started and passed the health check.
        """
        with self._mutex:
            if name in self._services:
                existing = self._services[name]
                logger.warning(
                    "Service %s already registered — stopping it first", name)
                self._stop_process(existing)

        if isinstance(cmd, str):
            cmd_list = [cmd]
        else:
            cmd_list = cmd

        mp = ManagedProcess(name=name, cmd=cmd_list, health_check=health_check)

        # Use shell execution when cmd is a single string (e.g. inline Python
        # one-liner)
        use_shell = isinstance(cmd, str)

        log_file_prefix = os.path.join(
            os.path.dirname(
                os.path.abspath(__file__)),
            "..",
            "logs",
            f"svc_{name}")
        os.makedirs(os.path.dirname(log_file_prefix), exist_ok=True)

        stdout_path = f"{log_file_prefix}_stdout.log"
        stderr_path = f"{log_file_prefix}_stderr.log"

        try:
            env = env or os.environ.copy()
            # Open log files and store file handles for later cleanup
            stdout_file = open(stdout_path, "w", encoding="utf-8")
            stderr_file = open(stderr_path, "w", encoding="utf-8")
            mp.process = subprocess.Popen(
                cmd_list,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=cwd,
                env=env,
                shell=use_shell,
                # On Linux, ensure the process doesn't inherit unnecessary file
                # descriptors
                close_fds=True,
            )
            # Store file handles for cleanup when service stops
            mp._stdout_file = stdout_file
            mp._stderr_file = stderr_file
        except Exception as exc:
            logger.error("Failed to start service %s: %s", name, exc)
            return False

        mp.started_at = time.time()
        logger.info("Started service %s (PID %d)", name, mp.process.pid)

        # Wait for health check
        healthy = self._wait_for_health(mp, timeout)
        if not healthy:
            # Capture log content for diagnostics
            try:
                with open(stderr_path, "r", encoding="utf-8") as f:
                    mp.stderr_log = f.read()
                with open(stdout_path, "r", encoding="utf-8") as f:
                    mp.stdout_log = f.read()
            except OSError:
                pass
            logger.warning(
                "Service %s failed health check within %.0fs",
                name,
                timeout)
            with self._mutex:
                self._services[name] = mp
            return False

        with self._mutex:
            self._services[name] = mp
        logger.info("Service %s is healthy", name)
        return True

    def stop_service(self, name: str) -> bool:
        """Stop a specific service (graceful SIGTERM → wait → SIGKILL)."""
        with self._mutex:
            mp = self._services.pop(name, None)
        if mp is None:
            logger.debug("Service %s not found", name)
            return False
        return self._stop_process(mp)

    def get_service(self, name: str) -> Optional[ManagedProcess]:
        with self._mutex:
            return self._services.get(name)

    def list_services(self) -> list[str]:
        with self._mutex:
            return list(self._services.keys())

    def is_running(self, name: str) -> bool:
        mp = self.get_service(name)
        if mp is None or mp.process is None:
            return False
        return mp.process.poll() is None

    def shutdown_all(self) -> None:
        """Stop all managed services.  Safe to call multiple times."""
        with self._mutex:
            names = list(self._services.keys())
        for name in names:
            self.stop_service(name)

    # -- internals -----------------------------------------------------------

    def _wait_for_health(self, mp: ManagedProcess, timeout: float) -> bool:
        """Block until health_check passes or timeout.  Also fails if the
        process dies early."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            # Check if process crashed
            ret = mp.process.poll()
            if ret is not None:
                logger.error(
                    "Service %s exited unexpectedly with code %d",
                    mp.name,
                    ret)
                try:
                    # try to capture the log
                    log_base = os.path.join(
                        os.path.dirname(
                            os.path.abspath(__file__)),
                        "..",
                        "logs",
                        f"svc_{
                            mp.name}")
                    with open(f"{log_base}_stderr.log", "r",
                              encoding="utf-8") as f:
                        mp.stderr_log = f.read()
                except OSError:
                    pass
                return False

            if mp.health_check is None:
                # No health check → consider healthy after 1 second alive
                if time.time() - mp.started_at >= 1.0:
                    return True
            elif mp.health_check():
                return True

            time.sleep(0.5)

        logger.error(
            "Service %s health check timed out after %.0fs",
            mp.name,
            timeout)
        return False

    def _stop_process(
            self,
            mp: ManagedProcess,
            graceful_timeout: float = 5.0) -> bool:
        """Terminate or kill a single ManagedProcess."""
        proc = mp.process
        if proc is None:
            # Clean up any open file handles
            self._close_log_files(mp)
            return True

        if proc.poll() is not None:
            logger.info(
                "Service %s already exited (code %d)",
                mp.name,
                proc.returncode)
            self._close_log_files(mp)
            return True

        # Graceful: SIGTERM / taskkill
        logger.info(
            "Stopping service %s (PID %d) gracefully.",
            mp.name,
            proc.pid)
        self._send_terminate(proc)

        try:
            proc.wait(timeout=graceful_timeout)
            logger.info("Service %s stopped gracefully", mp.name)
            self._close_log_files(mp)
            return True
        except subprocess.TimeoutExpired:
            pass

        # Forceful: SIGKILL / taskkill /F
        logger.warning("Service %s did not stop gracefully — killing", mp.name)
        try:
            self._send_kill(proc)
            proc.wait(timeout=3)
            logger.info("Service %s killed", mp.name)
            self._close_log_files(mp)
            return True
        except Exception:
            logger.error("Failed to kill service %s", mp.name)
            self._close_log_files(mp)
            return False

    @staticmethod
    def _close_log_files(mp: ManagedProcess) -> None:
        """Close any open log file handles to prevent file descriptor leaks."""
        for attr in ('_stdout_file', '_stderr_file'):
            f = getattr(mp, attr, None)
            if f is not None:
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
                finally:
                    setattr(mp, attr, None)

    @staticmethod
    def _send_terminate(proc: subprocess.Popen) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            import signal
            os.kill(proc.pid, signal.SIGTERM)

    @staticmethod
    def _send_kill(proc: subprocess.Popen) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            import signal
            os.kill(proc.pid, signal.SIGKILL)
