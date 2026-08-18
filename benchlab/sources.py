"""BENCHLAB PyTools v2 – Data Source Management.

Handles detection, startup, and teardown of the five supported
telemetry sources: direct serial, FastAPI REST server, MQTT,
named pipe (C# service), and service HTTP API (C# service).
"""

import json
import logging
import os
import socket
import sys
import urllib.request

from benchlab.core.process_manager import ProcessManager
from benchlab.core.device_registry import DeviceRegistry

logger = logging.getLogger("benchlab.launcher")


# ──────────────────────────────────────────────────────────────
# Low-level Network Helpers
# ──────────────────────────────────────────────────────────────

def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


def check_mqtt_running(host: str = "localhost", port: int = 1883) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────
# FastAPI Source
# ──────────────────────────────────────────────────────────────

def _fastapi_health(host: str, port: int) -> bool:
    """Return True if the FastAPI server is responding to HTTP requests."""
    try:
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                return data.get("status") == "healthy"
    except Exception as e:
        logger.debug(f"FastAPI health check failed: {e}")
    return False


def _fastapi_devices_available(host: str, port: int) -> bool:
    """Return True if the FastAPI server has devices available."""
    try:
        url = f"http://{host}:{port}/devices"
        with urllib.request.urlopen(url, timeout=3) as resp:
            devices = json.loads(resp.read().decode())
            return isinstance(devices, list) and len(devices) > 0
    except Exception:
        return False


def _trigger_fastapi_scan(host: str, port: int) -> None:
    """POST /scan to an already-running FastAPI server to force
    device discovery."""
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/scan", method="POST", data=b""
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


def start_fastapi_source(port: int = 8000) -> bool:
    """Start the FastAPI server as a telemetry source via ProcessManager."""
    pm = ProcessManager.get_instance()
    if pm.is_running("fastapi"):
        return True

    logger.info(f"Starting FastAPI server on port {port}...")

    ok = pm.start_service(
        name="fastapi",
        cmd=[sys.executable, "-m", "benchlab", "-fastapi"],
        health_check=lambda: _fastapi_health("127.0.0.1", port),
        timeout=20,
    )

    if ok:
        devices_available = _fastapi_devices_available("127.0.0.1", port)
        if devices_available:
            logger.info(f"FastAPI server ready on port {port} with device(s)")
        else:
            logger.warning(
                f"FastAPI server ready on port {port} but no devices detected")
    else:
        svc = pm.get_service("fastapi")
        if svc and (svc.stderr_log or svc.stdout_log):
            logger.error("FastAPI failed to start. Server log:")
            for line in (svc.stderr_log or svc.stdout_log).splitlines()[-15:]:
                print(f"    > {line}")
    return ok


# ──────────────────────────────────────────────────────────────
# MQTT Source
# ──────────────────────────────────────────────────────────────

def _mqtt_device_check(broker: str) -> bool:
    """Return True if the MQTT datasource can discover any devices."""
    try:
        from benchlab.core.datasource import MQTTDataSource
        ds = MQTTDataSource(broker=broker, timeout=3)
        if ds.connect():
            devices = ds.list_devices()
            ds.disconnect()
            return len(devices) > 0
    except Exception:
        pass
    return False


def start_mqtt_broker(port: int = 1883) -> bool:
    """Start an embedded amqtt broker if no external broker is detected."""
    if check_mqtt_running("localhost", port):
        return True

    logger.info(f"Starting embedded MQTT broker on port {port}...")
    pm = ProcessManager.get_instance()
    if pm.is_running("mqtt_broker"):
        return True

    broker_script = f'''
import asyncio
from amqtt.broker import Broker

config = {{
    "listeners": {{"default": {{"type": "tcp", "bind": "0.0.0.0:{port}"}}}},
    "auth": {{"allow-anonymous": True}},
    "topic-check": {{"enabled": False}},
}}

async def main():
    broker = Broker(config)
    await broker.start()
    while True:
        await asyncio.sleep(1)

asyncio.run(main())
'''

    def _broker_port_check():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                return sock.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False

    ok = pm.start_service(
        name="mqtt_broker",
        cmd=[sys.executable, "-c", broker_script],
        health_check=_broker_port_check,
        timeout=15,
    )

    if ok:
        logger.info(f"Embedded MQTT broker ready on port {port}")
    else:
        logger.error("Embedded MQTT broker failed to start")
    return ok


def start_mqtt_source(broker: str = "localhost", port: int = 1883) -> bool:
    """Start the MQTT publisher as a telemetry source via ProcessManager."""
    pm = ProcessManager.get_instance()
    if pm.is_running("mqtt_publisher"):
        return True

    logger.info(f"Starting MQTT publisher to {broker}:{port}...")

    ok = pm.start_service(
        name="mqtt_publisher",
        cmd=[sys.executable, "-m", "benchlab", "-mqtt", broker],
        health_check=lambda: _mqtt_device_check(broker),
        timeout=20,
    )

    if ok:
        logger.info("MQTT publisher ready with device(s)")
    else:
        svc = pm.get_service("mqtt_publisher")
        if svc and (svc.stderr_log or svc.stdout_log):
            logger.error("MQTT publisher failed to start. Server log:")
            for line in (svc.stderr_log or svc.stdout_log).splitlines()[-15:]:
                print(f"    > {line}")
    return ok


# ──────────────────────────────────────────────────────────────
# Named Pipe Source (C# BenchLab Windows service)
# ──────────────────────────────────────────────────────────────

def _named_pipe_available() -> bool:
    """Return True if the BenchlabDiscovery pipe exists (service is
    running)."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import win32pipe
        # WaitNamedPipe with a very short timeout just checks existence
        win32pipe.WaitNamedPipe(r"\\.\pipe\BenchlabDiscovery", 1000)
        return True
    except Exception:
        return False


def check_named_pipe_service() -> bool:
    """Check if the C# BenchLab named pipe service is running.

    Returns True if the discovery pipe is present and responsive.
    Does NOT attempt to start the service — call this to decide
    whether to offer named_pipe as a source option.
    """
    if not sys.platform.startswith("win"):
        logger.debug("Named pipe source is Windows-only")
        return False

    if not _named_pipe_available():
        logger.debug(
            "BenchlabDiscovery pipe not found — C# service not running")
        return False

    # Quick smoke-test: open the pipe and send ListDevices
    handle = None
    try:
        import win32file
        import win32pipe

        path = r"\\.\pipe\BenchlabDiscovery"
        win32pipe.WaitNamedPipe(path, 2000)
        handle = win32file.CreateFile(
            path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None,
        )
        win32file.WriteFile(handle, b"ListDevices\n")
        _, data = win32file.ReadFile(handle, 65536)
        result = json.loads(data.split(b"\n")[0].decode("utf-8"))
        return isinstance(result, list)
    except Exception as e:
        logger.debug(f"Named pipe smoke-test failed: {e}")
        return False
    finally:
        if handle is not None:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────
# Service HTTP Source (C# BenchLab Windows service REST API)
# ──────────────────────────────────────────────────────────────

SERVICE_HTTP_DEFAULT_PORT = 8585


def _service_http_health(host: str = "localhost",
                         port: int = SERVICE_HTTP_DEFAULT_PORT) -> bool:
    """Return True if the C# BenchLab service HTTP API is healthy."""
    try:
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                # C# service returns {"status":"healthy",...}
                return data.get("status") == "healthy"
    except Exception as e:
        logger.debug(f"Service HTTP health check failed: {e}")
    return False


def _service_http_devices_available(
        host: str = "localhost",
        port: int = SERVICE_HTTP_DEFAULT_PORT) -> bool:
    """Return True if the C# service has at least one device."""
    try:
        url = f"http://{host}:{port}/devices"
        with urllib.request.urlopen(url, timeout=3) as resp:
            devices = json.loads(resp.read().decode())
            return isinstance(devices, list) and len(devices) > 0
    except Exception:
        return False


def check_service_http() -> bool:
    """Check if the C# BenchLab service HTTP API is reachable.

    Auto-detects at localhost:8585. Does NOT start the service.
    """
    return _service_http_health()


def _direct_device_available() -> bool:
    """Return True if pycore can see a BENCHLAB device on any serial port."""
    try:
        from benchlab_pycore.core import get_benchlab_ports
        return len(get_benchlab_ports()) > 0
    except Exception as e:
        logger.debug(f"Direct device scan failed: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# Unified Source Setup
# ──────────────────────────────────────────────────────────────

def check_and_setup_source(source_type: str, **kwargs) -> bool:
    """Check if a data source is available and start it if not.

    Sets the relevant environment variables and returns True when the
    source is ready, False if setup failed.

    Supported source_type values:
        'direct'       — direct serial via pycore
        'fastapi'      — Python benchlab FastAPI server
        'mqtt'         — MQTT broker + publisher
        'named_pipe'   — C# BenchLab service named pipes (Windows only)
        'service_http' — C# BenchLab service HTTP API
    """
    if source_type == "direct":
        os.environ["BENCHLAB_DATA_SOURCE"] = "direct"
        if not _direct_device_available():
            logger.warning(
                "No BENCHLAB device detected on any serial port. "
                "Plug in a device or select a different data source."
            )
        return True

    if source_type == "fastapi":
        port = kwargs.get("port", 8000)
        host = kwargs.get("host", "127.0.0.1")
        api_url = f"http://{host}:{port}"
        os.environ["BENCHLAB_API_URL"] = api_url
        os.environ["API_PORT"] = str(port)

        if _fastapi_health(host, port):
            devices_msg = "with device(s)" if _fastapi_devices_available(
                host, port) else "but no devices detected"
            logger.info(f"FastAPI already running at {api_url} {devices_msg}")
            os.environ["BENCHLAB_DATA_SOURCE"] = "fastapi"
            return True

        if _port_in_use(host, port):
            logger.info(
                f"Port {port} is in use — triggering /scan on existing server")
            _trigger_fastapi_scan(host, port)
            if _fastapi_health(host, port):
                devices_msg = "with device(s)" if _fastapi_devices_available(
                    host, port) else "but no devices detected"
                logger.info(f"FastAPI at {api_url} is healthy {devices_msg}")
                os.environ["BENCHLAB_DATA_SOURCE"] = "fastapi"
                return True
            logger.error(
                f"Port {port} is occupied but server is not responding "
                "— cannot start")
            return False

        logger.info(f"FastAPI not detected at {api_url}")
        ok = start_fastapi_source(port)
        if ok:
            devices_msg = "with device(s)" if _fastapi_devices_available(
                host, port) else "but no devices detected"
            logger.info(f"FastAPI server started on port {port} {devices_msg}")
            os.environ["BENCHLAB_DATA_SOURCE"] = "fastapi"
        return ok

    if source_type == "mqtt":
        host = kwargs.get("broker", "localhost")
        mqtt_port = kwargs.get("mqtt_port", 1883)
        os.environ["MQTT_BROKER"] = host
        os.environ["MQTT_PORT"] = str(mqtt_port)

        if not check_mqtt_running(host, mqtt_port):
            logger.info(f"MQTT broker not detected at {host}:{mqtt_port}")
            if not start_mqtt_broker(mqtt_port):
                return False
        else:
            logger.info(f"MQTT broker available at {host}:{mqtt_port}")

        return start_mqtt_source(host, mqtt_port)

    if source_type == "named_pipe":
        if not sys.platform.startswith("win"):
            logger.error(
                "Named pipe source is only available on Windows. "
                "The C# BenchLab service uses Windows named pipes."
            )
            return False

        if not _named_pipe_available():
            logger.error(
                "BenchLab named pipe service not detected.\n"
                "  → Make sure the BenchLab Windows service (BL_Service) "
                "is running.\n"
                "  → You can start it via Windows Services or by "
                "running BL_Service.exe.")
            return False

        logger.info("BenchLab named pipe service detected and ready")
        os.environ["BENCHLAB_DATA_SOURCE"] = "named_pipe"
        return True

    if source_type == "service_http":
        port = kwargs.get("port", SERVICE_HTTP_DEFAULT_PORT)
        host = kwargs.get("host", "localhost")

        if not _service_http_health(host, port):
            logger.error(
                f"BenchLab service HTTP API not detected at "
                f"http://{host}:{port}.\n"
                "  → Make sure the BenchLab Windows service (BL_Service) "
                "is running.\n"
                "  → The service HTTP API listens on port 8585 by default."
            )
            return False

        devices_msg = "with device(s)" if _service_http_devices_available(
            host, port) else "but no devices detected"
        logger.info(
            f"BenchLab service HTTP API ready at "
            f"http://{host}:{port} {devices_msg}")
        os.environ["BENCHLAB_DATA_SOURCE"] = "service_http"
        os.environ["BENCHLAB_SERVICE_URL"] = f"http://{host}:{port}"
        return True

    if source_type == "fastapi_custom":
        base_url = kwargs.get("base_url")
        if not base_url:
            logger.error(
                "fastapi_custom requires a base_url "
                "(e.g., http://192.168.1.100:8000)")
            return False

        # Parse the URL to get host and port
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or 8000

        os.environ["BENCHLAB_API_URL"] = base_url
        os.environ["BENCHLAB_DATA_SOURCE"] = "fastapi_custom"

        # Check if the remote server is healthy
        if _fastapi_health(host, port):
            devices_msg = "with device(s)" if _fastapi_devices_available(
                host, port) else "but no devices detected"
            logger.info(
                f"FastAPI server available at {base_url} {devices_msg}")
            return True
        else:
            logger.error(f"FastAPI server not reachable at {base_url}")
            logger.error(
                "Make sure the remote server is running and accessible "
                "from this machine.")
            return False

    if source_type == "mqtt_custom":
        broker = kwargs.get("broker", "localhost")
        mqtt_port = kwargs.get("mqtt_port", 1883)
        os.environ["MQTT_BROKER"] = broker
        os.environ["MQTT_PORT"] = str(mqtt_port)

        # Check if the custom MQTT broker is reachable
        if not check_mqtt_running(broker, mqtt_port):
            logger.error(f"MQTT broker not reachable at {broker}:{mqtt_port}")
            logger.error(
                "Make sure the MQTT broker is running and accessible.")
            return False

        logger.info(f"MQTT broker available at {broker}:{mqtt_port}")
        os.environ["BENCHLAB_DATA_SOURCE"] = "mqtt_custom"

        # Start the MQTT publisher pointing to the custom broker
        return start_mqtt_source(broker, mqtt_port)

    return False


# ──────────────────────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────────────────────

def cleanup_all_services() -> None:
    """Shut down all infrastructure services started during this session."""
    ProcessManager.get_instance().shutdown_all()
    DeviceRegistry.get_instance().clear()
