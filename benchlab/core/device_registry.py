"""
Device Registry for BENCHLAB

Single source of truth for device lifecycle.  Tracks which devices are
available, which data source owns them, and emits connection/disconnection
events so tools can subscribe without re-discovering devices on their own.

Usage:
    reg = DeviceRegistry.get_instance()

    # Register a device (called by whichever owns the serial bus)
    reg.register(uid="2C003D001457435735363620", port="COM4",
                 firmware="0x01234567")

    # Subscribe to lifecycle events
    def on_connect(info):
        print(f"Device connected: {info.uid} on {info.port}")

    reg.on_connect(on_connect)
    reg.on_disconnect(lambda info: print(f"Device gone: {info.uid}"))

    # Query current devices
    devices = reg.get_devices()  # list[DeviceInfo]

    # Unregister when a device disappears
    reg.unregister("2C003D001457435735363620")
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("benchlab.core.device_registry")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DeviceInfo:
    """Snapshot of a single device's state."""

    uid: str
    port: str
    firmware: str = ""
    data_source: str = "direct"  # "direct" | "fastapi" | "mqtt"
    connected_at: float = field(default_factory=time.time)
    last_telemetry: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "uid": self.uid,
            "port": self.port,
            "firmware": self.firmware,
            "data_source": self.data_source,
            "connected_at": self.connected_at,
            "last_telemetry": self.last_telemetry,
        }
        return d


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class DeviceRegistry:
    """Thread-safe singleton that manages device discovery + lifecycle events.

    Exactly **one** component should call :py:meth:`register` /
    :py:meth:`unregister` — typically whichever component owns the serial
    port (FastAPI server, MQTT publisher, or DirectDataSource).

    Tools only **observe** the registry rather than scanning on their own.
    """

    _instance: Optional["DeviceRegistry"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._devices: Dict[str, DeviceInfo] = {}
        self._mutex = threading.Lock()

        self._on_connect: List[Callable[[DeviceInfo], None]] = []
        self._on_disconnect: List[Callable[[DeviceInfo], None]] = []

    # -- singleton helpers ---------------------------------------------------

    @classmethod
    def get_instance(cls) -> "DeviceRegistry":
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
                inst.clear()
                cls._instance = None

    # -- mutators (serial owner only) ----------------------------------------

    def register(
        self,
        uid: str,
        port: str,
        firmware: str = "",
        data_source: str = "direct",
    ) -> DeviceInfo:
        """Register (or update) a device in the registry.

        Emits *on_connect* callbacks if the device was not already present.
        """
        info = DeviceInfo(
            uid=uid,
            port=port,
            firmware=firmware,
            data_source=data_source,
        )
        with self._mutex:
            is_new = uid not in self._devices
            self._devices[uid] = info
        if is_new:
            logger.info(
                "Device registered: %s on %s (source=%s)",
                uid,
                port,
                data_source)
            self._emit_connect(info)

    def unregister(self, uid: str) -> None:
        """Remove a device and emit *on_disconnect* callbacks."""
        with self._mutex:
            info = self._devices.pop(uid, None)
        if info is not None:
            logger.info("Device unregistered: %s", uid)
            self._emit_disconnect(info)

    def update_telemetry(self, uid: str) -> None:
        """Mark ``last_telemetry`` for *uid*.  No-op if uid unknown."""
        now = time.time()
        with self._mutex:
            info = self._devices.get(uid)
            if info is not None:
                info.last_telemetry = now

    def clear(self) -> None:
        """Remove all devices (useful for testing / resets)."""
        with self._mutex:
            devices = list(self._devices.values())
            self._devices.clear()
        for dev in devices:
            self._emit_disconnect(dev)

    # -- observers (tools call these) ----------------------------------------

    def get_devices(self) -> List[DeviceInfo]:
        """Return a snapshot of currently registered devices."""
        with self._mutex:
            return list(self._devices.values())

    def get_device(self, uid: str) -> Optional[DeviceInfo]:
        with self._mutex:
            return self._devices.get(uid)

    def has_device(self, uid: str) -> bool:
        with self._mutex:
            return uid in self._devices

    # -- event subscription --------------------------------------------------

    def on_connect(self, callback: Callable[[DeviceInfo], None]) -> None:
        """Register *callback* to be called whenever a device is added."""
        with self._mutex:
            self._on_connect.append(callback)

    def on_disconnect(self, callback: Callable[[DeviceInfo], None]) -> None:
        """Register *callback* for device removal."""
        with self._mutex:
            self._on_disconnect.append(callback)

    # -- internals -----------------------------------------------------------

    def _emit_connect(self, info: DeviceInfo) -> None:
        for cb in self._on_connect:
            try:
                cb(info)
            except Exception:
                logger.error("on_connect callback raised", exc_info=True)

    def _emit_disconnect(self, info: DeviceInfo) -> None:
        for cb in self._on_disconnect:
            try:
                cb(info)
            except Exception:
                logger.error("on_disconnect callback raised", exc_info=True)
