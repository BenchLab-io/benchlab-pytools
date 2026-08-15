"""
Infrastructure Manager for BENCHLAB

Handles starting and stopping of data provider services (FastAPI, MQTT)
that can be shared by multiple tools.
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger("benchlab.core.infrastructure")


class InfrastructureManager:
    """Manages infrastructure services (FastAPI, MQTT) for multi-tool
    scenarios.

    This class is responsible for:
    - Starting FastAPI server as a subprocess when needed
    - Starting MQTT publisher thread when needed
    - Ensuring services are properly shut down
    - Checking if services are already running
    """

    def __init__(
        self,
        fastapi_host: str = "127.0.0.1",
        fastapi_port: int = 8000,
        mqtt_broker: str = "localhost",
        mqtt_port: int = 1883,
    ):
        """Initialize infrastructure manager.

        Args:
            fastapi_host: Host for FastAPI server
            fastapi_port: Port for FastAPI server
            mqtt_broker: MQTT broker hostname
            mqtt_port: MQTT broker port
        """
        self.fastapi_host = fastapi_host
        self.fastapi_port = fastapi_port
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port

        self._fastapi_process: Optional[subprocess.Popen] = None
        self._mqtt_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start_fastapi(self, wait_ready: bool = True,
                      timeout: float = 10.0) -> bool:
        """Start FastAPI server as a subprocess.

        Args:
            wait_ready: Whether to wait for server to be ready
            timeout: Timeout for waiting (seconds)

        Returns:
            True if server started successfully
        """
        with self._lock:
            # Check if already running
            if self._fastapi_process is not None:
                if self._fastapi_process.poll() is None:
                    logger.info("FastAPI server already running")
                    return True

            # Check if port is already in use (server might be running
            # externally)
            if self._is_port_in_use(self.fastapi_host, self.fastapi_port):
                logger.info(
                    f"Port {self.fastapi_port} already in use, "
                    f"assuming FastAPI is running")
                return True

            logger.info(
                f"Starting FastAPI server on {
                    self.fastapi_host}:{
                    self.fastapi_port}")

            try:
                # Start FastAPI server as subprocess
                self._fastapi_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "benchlab.restapi.telemetry_api:app",
                        "--host",
                        self.fastapi_host,
                        "--port",
                        str(self.fastapi_port),
                        "--log-level",
                        "warning",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                if wait_ready:
                    if self._wait_for_port(
                            self.fastapi_host, self.fastapi_port, timeout):
                        logger.info(
                            f"FastAPI server started on port {
                                self.fastapi_port}")
                        return True
                    else:
                        logger.error("FastAPI server failed to start")
                        self.stop_fastapi()
                        return False
                else:
                    # Give it a moment to start
                    time.sleep(0.5)
                    return True

            except Exception as e:
                logger.error(f"Failed to start FastAPI server: {e}")
                return False

    def stop_fastapi(self) -> None:
        """Stop FastAPI server."""
        with self._lock:
            if self._fastapi_process is not None:
                logger.info("Stopping FastAPI server")
                try:
                    # Try graceful shutdown first
                    self._fastapi_process.terminate()
                    try:
                        self._fastapi_process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        # Force kill if not responding
                        self._fastapi_process.kill()
                        self._fastapi_process.wait()
                except Exception as e:
                    logger.warning(f"Error stopping FastAPI server: {e}")
                finally:
                    self._fastapi_process = None

    def is_fastapi_running(self) -> bool:
        """Check if FastAPI server is running.

        Returns:
            True if FastAPI server is running
        """
        # Check if we started it
        if self._fastapi_process is not None:
            if self._fastapi_process.poll() is None:
                return True

        # Check if port is in use (might be running externally)
        return self._is_port_in_use(self.fastapi_host, self.fastapi_port)

    def start_mqtt_publisher(self) -> bool:
        """Start MQTT publisher thread.

        Note: This starts the MQTT publisher that sends data to an external
        MQTT broker. The broker itself must be running separately.

        Returns:
            True if MQTT publisher started successfully
        """
        with self._lock:
            if self._mqtt_thread is not None and self._mqtt_thread.is_alive():
                logger.info("MQTT publisher already running")
                return True

            logger.info(
                f"Starting MQTT publisher to {
                    self.mqtt_broker}:{
                    self.mqtt_port}")

            try:
                self._stop_event.clear()
                self._mqtt_thread = threading.Thread(
                    target=self._mqtt_publisher_loop,
                    daemon=True,
                )
                self._mqtt_thread.start()

                # Give it a moment to connect
                time.sleep(1.0)
                return True

            except Exception as e:
                logger.error(f"Failed to start MQTT publisher: {e}")
                return False

    def stop_mqtt_publisher(self) -> None:
        """Stop MQTT publisher thread."""
        with self._lock:
            if self._mqtt_thread is not None:
                logger.info("Stopping MQTT publisher")
                self._stop_event.set()
                if self._mqtt_thread.is_alive():
                    self._mqtt_thread.join(timeout=5.0)
                self._mqtt_thread = None

    def is_mqtt_running(self) -> bool:
        """Check if MQTT publisher is running.

        Returns:
            True if MQTT publisher thread is alive
        """
        return self._mqtt_thread is not None and self._mqtt_thread.is_alive()

    def start_all(self, tools: list) -> bool:
        """Start all required infrastructure based on tool requirements.

        Args:
            tools: List of tool configurations with 'name' and 'source' keys

        Returns:
            True if all required infrastructure started successfully
        """
        # Determine what's needed
        need_fastapi = any(t.get('source') == 'fastapi' for t in tools)
        need_mqtt = any(t.get('source') == 'mqtt' for t in tools)

        success = True

        if need_fastapi:
            if not self.start_fastapi():
                logger.error("Failed to start FastAPI server")
                success = False

        if need_mqtt:
            if not self.start_mqtt_publisher():
                logger.error("Failed to start MQTT publisher")
                success = False

        return success

    def stop_all(self) -> None:
        """Stop all infrastructure services."""
        self.stop_fastapi()
        self.stop_mqtt_publisher()

    def _is_port_in_use(self, host: str, port: int) -> bool:
        """Check if a port is in use.

        Args:
            host: Host to check
            port: Port to check

        Returns:
            True if port is in use
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.settimeout(1.0)
                result = s.connect_ex((host, port))
                return result == 0
            except Exception:
                return False

    def _wait_for_port(
            self,
            host: str,
            port: int,
            timeout: float = 10.0) -> bool:
        """Wait for a port to become available.

        Args:
            host: Host to check
            port: Port to check
            timeout: Timeout in seconds

        Returns:
            True if port becomes available within timeout
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._is_port_in_use(host, port):
                return True
            time.sleep(0.2)
        return False

    def _mqtt_publisher_loop(self):
        """Background thread that publishes data to MQTT broker.

        This imports and runs the MQTT publisher from benchlab.mqtt.
        """
        try:
            from benchlab.mqtt.mqtt_publisher import run_mqtt_mode

            # Set environment variables for MQTT configuration
            os.environ.setdefault("MQTT_BROKER", self.mqtt_broker)
            os.environ.setdefault("MQTT_PORT", str(self.mqtt_port))

            # Run MQTT mode (it handles its own lifecycle)
            run_mqtt_mode(broker_type=f"{self.mqtt_broker}:{self.mqtt_port}")

        except Exception as e:
            logger.error(f"MQTT publisher thread error: {e}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure cleanup."""
        self.stop_all()
        return False
