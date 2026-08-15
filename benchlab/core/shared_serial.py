"""
Shared Serial Connection Manager for BENCHLAB

Provides a singleton-based serial connection pool that prevents
multiple services (FastAPI, MQTT, etc.) from competing for the
same physical serial port. (BUG-1.4)
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

import serial

logger = logging.getLogger("benchlab.core.shared_serial")

# BENCHLAB devices communicate over USB CDC at a fixed 115200 baud.
# benchlab_pycore.core.serial_io never exposed a connection-opening helper
# (its own docs just call serial.Serial(port, 115200, timeout=1) directly) —
# this wraps that same pattern so callers get a consistent
# "None on failure" contract instead of talking to pyserial directly.
_BENCHLAB_BAUDRATE = 115200
_BENCHLAB_TIMEOUT = 1


def open_serial_connection(port: str) -> Optional[serial.Serial]:
    """Open a serial connection to a BENCHLAB device.

    Returns None if the port can't be opened.
    """
    try:
        return serial.Serial(
            port,
            _BENCHLAB_BAUDRATE,
            timeout=_BENCHLAB_TIMEOUT)
    except (serial.SerialException, OSError) as e:
        logger.warning("Failed to open serial port %s: %s", port, e)
        return None


class SharedSerialManager:
    """Thread-safe singleton that manages serial port ownership.

    Use this to ensure only one reader owns a serial port at a time,
    while allowing multiple consumers to receive telemetry data.

    Usage:
        mgr = SharedSerialManager.get_instance()
        conn = mgr.acquire_connection("COM3")
        if conn:
            data = read_sensors(conn.ser)
        mgr.release_connection("COM3")
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        # port -> {ser, ref_count, lock, owner}
        self._connections: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SharedSerialManager":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (primarily for testing)."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._shutdown()
                cls._instance = None

    def acquire_connection(
        self,
        port: str,
        open_func=None,
        timeout: float = 5.0
    ) -> Optional["SharedConnection"]:
        """Acquire a shared serial connection to a port.

        Args:
            port: Serial port identifier (e.g., "COM3", "/dev/ttyUSB0")
            open_func: Callback to open the serial port if not already open.
                       Signature: open_func(port) -> serial_connection
            timeout: How long to wait if port is being opened by another thread

        Returns:
            SharedConnection wrapper, or None if port can't be opened
        """
        if open_func is None:
            open_func = open_serial_connection

        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                # Check if already open
                if port in self._connections:
                    entry = self._connections[port]
                    entry["ref_count"] += 1
                    logger.debug(
                        "Reusing shared connection to %s (ref_count=%d)",
                        port,
                        entry["ref_count"])
                    return SharedConnection(self, port, entry["ser"])

                # Try to claim it
                self._connections[port] = {
                    "ser": None,
                    "ref_count": 1,
                    "lock": threading.Lock(),
                    "owner": threading.current_thread().name,
                }

            # Open outside the main lock to avoid blocking
            try:
                ser = open_func(port)
                if ser:
                    with self._lock:
                        self._connections[port]["ser"] = ser

                    logger.info("Opened new shared connection to %s", port)
                    return SharedConnection(self, port, ser)
                else:
                    with self._lock:
                        del self._connections[port]
                    logger.warning("Failed to open serial port %s", port)
                    return None
            except Exception as e:
                with self._lock:
                    del self._connections[port]
                logger.warning("Exception opening port %s: %s", port, e)
                time.sleep(0.5)
                continue

        logger.error("Timeout acquiring connection to %s", port)
        return None

    def release_connection(self, port: str) -> None:
        """Release a shared serial connection.

        When ref_count reaches 0, the connection is closed.
        """
        with self._lock:
            if port not in self._connections:
                logger.warning(
                    "Attempted to release non-existent connection on %s", port)
                return

            entry = self._connections[port]
            entry["ref_count"] -= 1

            if entry["ref_count"] <= 0:
                # Last user released - close the port
                ser = entry["ser"]
                del self._connections[port]

                if ser:
                    try:
                        ser.close()
                        logger.info("Closed shared connection to %s", port)
                    except Exception:
                        pass

    def is_port_owned(self, port: str) -> bool:
        """Check if a port currently has an open shared connection."""
        with self._lock:
            return port in self._connections

    def get_active_ports(self) -> list:
        """Get list of ports with active shared connections."""
        with self._lock:
            return list(self._connections.keys())

    def _shutdown(self) -> None:
        """Close all connections (called by reset() or cleanup)."""
        with self._lock:
            for port, entry in list(self._connections.items()):
                ser = entry["ser"]
                if ser:
                    try:
                        ser.close()
                    except Exception:
                        pass
            self._connections.clear()


class SharedConnection:
    """Wrapper around a shared serial connection.

    Use `conn.ser` to access the underlying serial connection.
    Call `conn.release()` when done.
    """

    def __init__(self, manager: SharedSerialManager, port: str, ser):
        self._manager = manager
        self._port = port
        self._released = False
        self.ser = ser  # Direct access to the serial connection

    @property
    def port(self) -> str:
        return self._port

    @property
    def is_released(self) -> bool:
        return self._released

    def release(self) -> None:
        """Release this reference to the shared connection."""
        if not self._released:
            self._released = True
            self._manager.release_connection(self._port)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False

    def __del__(self):
        # Safety net - don't leak connections
        if not self._released:
            logger.warning(
                "SharedConnection %s was garbage-collected without release()",
                self._port)
            self.release()
