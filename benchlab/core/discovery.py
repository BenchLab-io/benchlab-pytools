"""Device discovery utilities using the benchlab-pycore library.

All serial‑port discovery is now performed through the official
``benchlab_pycore`` helpers.  This module provides a single public function
``discover_devices`` that returns a list of dictionaries containing the UID,
port and firmware version of each connected BenchLab device.
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any

# benchlab-pycore helpers
from benchlab_pycore.core import get_benchlab_ports
from benchlab_pycore.core.serial_io import open_serial_connection
from benchlab_pycore.core import read_device, read_uid

# Re‑use the retry decorator defined in ``benchlab.core.retry``
from .retry import retry, RetryPolicy

logger = logging.getLogger("benchlab.core.discovery")


@retry(RetryPolicy(max_retries=3, backoff_factor=2.0, base_delay=0.5, allowed_exceptions=(Exception,)))
def discover_devices() -> List[Dict[str, Any]]:
    """Return a list of connected BenchLab devices.

    Each entry is a mapping with the keys ``uid``, ``port`` and ``fw`` (firmware).
    The function:
    1. Calls :func:`benchlab_pycore.core.get_benchlab_ports` to obtain candidate
       ports that match the known hardware ID.
    2. Opens each port, reads the UID and firmware version, then closes the
       connection.
    3. Logs discovery details at INFO level and any failures at DEBUG level.
    """
    devices: List[Dict[str, Any]] = []
    logger.info("Scanning for BenchLab devices via benchlab-pycore")

    ports = get_benchlab_ports()
    for port_info in ports:
        port = port_info.get("port")
        if not port:
            continue
        try:
            ser = open_serial_connection(port)
            if ser is None:
                logger.debug("Could not open serial port %s", port)
                continue
            uid = read_uid(ser)
            info = read_device(ser) or {}
            ser.close()
            if uid:
                fw = info.get("FwVersion", "?")
                devices.append({"uid": uid, "port": port, "fw": fw})
                logger.info("Discovered BenchLab device UID=%s on %s (FW=%s)", uid, port, fw)
            else:
                logger.debug("No UID read from port %s", port)
        except Exception as exc:  # pragma: no cover – defensive logging
            logger.debug("Error probing port %s: %s", port, exc)

    if not devices:
        logger.info("No BenchLab devices found via benchlab-pycore discovery")
    return devices
