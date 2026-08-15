"""Integration tests: every CONSUMER_TOOLS tool × every data source.

Each test in the matrix:
  1. Discovers a real BenchLab device (skips if none found).
  2. Spins up the requested data source via check_and_setup_source().
  3. Builds a standard args namespace (same as _launch_single_tool does).
  4. Runs the tool's func(args) in a daemon thread for TOOL_TIMEOUT seconds.
  5. Passes if no exception was raised during that window; fails otherwise.

Special cases
-------------
- tui      : curses.wrapper requires a real terminal and blocks forever.
             We bypass curses and call tui_main(stdscr=None, _, args) directly
             inside the thread. tui_main must tolerate stdscr=None for one tick
             before the timeout kills it.

Run with::

    pytest tests/test_tool_sources.py -m integration -s -v

A BenchLab device must be physically connected. FastAPI and MQTT sources also
require the serial port to be free (they start their own reader processes).
Override broker defaults via env vars: MQTT_BROKER / MQTT_PORT.
"""

import importlib
import logging
import os
import threading
import time
import types
import traceback
from typing import Optional

try:
    import curses
    HAS_CURSES = True
except ImportError:
    curses = None       # type: ignore[assignment]
    HAS_CURSES = False

import pytest

from benchlab.core.discovery import discover_devices
from benchlab.main import CONSUMER_TOOLS, check_and_setup_source
from benchlab.sources import cleanup_all_services as _cleanup_all_services

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_TIMEOUT = 8    # seconds to let a tool run before considering it passing
SOURCE_TIMEOUT = 40   # seconds to wait for fastapi/mqtt source to be ready
SOURCES = ["direct", "fastapi", "mqtt"]

SEP = "-" * 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _info(msg: str) -> None:
    print(f"     {msg}")


def _build_args(source: str, device: dict) -> types.SimpleNamespace:
    """Build the standard args namespace a tool func expects."""
    return types.SimpleNamespace(
        source=source,
        interval=float(os.environ.get("POLL_INTERVAL", "1.0")),
        api_url=os.environ.get("BENCHLAB_API_URL", "http://127.0.0.1:8000"),
        api_port=int(os.environ.get("API_PORT", "8000")),
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
        mqtt_broker=os.environ.get("MQTT_BROKER", "localhost"),
        # Pass device info so tools can locate the device without re-discovery
        device_uid=device["uid"],
        device_port=device["port"],
    )


def _setup_source(source: str, device: dict) -> None:
    """Set BENCHLAB_DATA_SOURCE and start infrastructure for the source.

    Raises pytest.fail() if the source cannot be made ready.
    """
    os.environ["BENCHLAB_DATA_SOURCE"] = source

    if source == "direct":
        ready = check_and_setup_source("direct")
    elif source == "fastapi":
        port = int(os.environ.get("API_PORT", "8000"))
        os.environ["API_PORT"] = str(port)
        # check_and_setup_source already waits for /devices to return a device,
        # but if the previous test just released the serial port the FastAPI
        # startup scan may have missed it. Once the server is up, hammer /scan
        # until a device appears.
        ready = False
        deadline = time.time() + SOURCE_TIMEOUT
        while time.time() < deadline:
            ready = check_and_setup_source("fastapi", port=port)
            if ready:
                break
            # Server is up but has no devices — force a rescan
            try:
                import urllib.request
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}/scan",
                        method="POST"),
                    timeout=3,
                )
                _info("Triggered FastAPI /scan — waiting for device...")
            except Exception:
                _info("FastAPI not yet reachable, retrying...")
            time.sleep(2)
    elif source == "mqtt":
        broker = os.environ.get("MQTT_BROKER", "localhost")
        mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        os.environ["MQTT_BROKER"] = broker
        os.environ["MQTT_PORT"] = str(mqtt_port)
        ready = check_and_setup_source(
            "mqtt", broker=broker, mqtt_port=mqtt_port)
    else:
        pytest.fail(f"Unknown source: {source}")

    if not ready:
        pytest.fail(
            f"Could not set up '{source}' data source within "
            f"{SOURCE_TIMEOUT}s. Check that the device is connected "
            "and no other process holds the port."
        )

    _ok(f"Source '{source}' ready")


def _run_tool_in_thread(
    tool_id: str,
    args: types.SimpleNamespace,
    timeout: int = TOOL_TIMEOUT,
) -> Optional[Exception]:
    """Run the tool's func(args) in a daemon thread for *timeout* seconds.

    Returns the exception if one was raised before the timeout, else None.
    Treats logged PermissionError / port access failures as test failures
    so tools that swallow serial errors don't produce false positives.
    """
    tool = CONSUMER_TOOLS[tool_id]
    module = importlib.import_module(tool["module"])
    func = getattr(module, tool["function"])

    error: list[Exception] = []
    logged_errors: list[str] = []

    # Intercept logging.error/critical to catch swallowed failures
    # (e.g. "could not open port COM4: PermissionError")
    _orig_error = logging.error
    _orig_critical = logging.critical

    def _capture(msg, *a, **kw):
        logged_errors.append(str(msg) % a if a else str(msg))

    logging.error = lambda msg, * \
        a, **kw: (_capture(msg, *a), _orig_error(msg, *a, **kw))
    logging.critical = lambda msg, * \
        a, **kw: (_capture(msg, *a), _orig_critical(msg, *a, **kw))

    def _target():
        try:
            if tool_id == "tui":
                pytest.skip(
                    "tui requires a real terminal; cannot "
                    "integration-test in pytest")
            else:
                func(args)
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            error.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    logging.error = _orig_error
    logging.critical = _orig_critical

    if error:
        return error[0]

    # Surface fatal port/permission errors that tools log but don't raise
    fatal_keywords = (
        "PermissionError",
        "Access is denied",
        "could not open port")
    for msg in logged_errors:
        if any(kw in msg for kw in fatal_keywords):
            return PermissionError(f"Tool logged a fatal error: {msg}")

    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def device():
    """Discover and return the first connected BenchLab device
    (session-scoped)."""
    _section("Device Discovery")
    devices = discover_devices()
    if not devices:
        pytest.skip(
            "No BenchLab device found – skipping tool integration tests")
    dev = devices[0]
    _ok(
        f"Found device: uid={
            dev['uid']}  port={
            dev['port']}  fw={
                dev.get(
                    'fw',
                    '?')}")
    return dev


@pytest.fixture(autouse=True)
def cleanup_services():
    """Tear down any infrastructure services started by a test."""
    # Ensure interactive prompts are never reached during tests
    os.environ["BENCHLAB_AUTO_SELECT"] = "true"
    yield
    _cleanup_all_services()
    time.sleep(3.0)   # enough for serial port to release AND fastapi to rescan


# ---------------------------------------------------------------------------
# Parametrised integration test matrix
# ---------------------------------------------------------------------------

def _tool_source_id(val):
    """Readable pytest ID for tool_id / source parametrise values."""
    return val   # already strings


@pytest.mark.integration
@pytest.mark.parametrize("source", SOURCES, ids=_tool_source_id)
@pytest.mark.parametrize("tool_id",
                         list(CONSUMER_TOOLS.keys()),
                         ids=_tool_source_id)
def test_tool_with_source(tool_id: str, source: str, device: dict) -> None:
    """<tool_id> starts and runs for TOOL_TIMEOUT seconds using <source>."""
    tool_name = CONSUMER_TOOLS[tool_id]["name"]
    _section(f"{tool_name} × {source}")

    # Import check — skip cleanly if the tool's module isn't installed.
    tool = CONSUMER_TOOLS[tool_id]
    try:
        importlib.import_module(tool["module"])
    except ModuleNotFoundError as exc:
        pytest.skip(f"{tool_name} module not available: {exc}")

    # Set up the data source.
    _info(f"Setting up source: {source}")
    _setup_source(source, device)

    # Build the args namespace the tool will receive.
    args = _build_args(source, device)
    _info(f"args.source={args.source}  api_url={args.api_url}  "
          f"mqtt_broker={args.mqtt_broker}:{args.mqtt_port}")

    # Run the tool.
    _info(f"Running {tool_name} for {TOOL_TIMEOUT}s ...")
    exc = _run_tool_in_thread(tool_id, args, timeout=TOOL_TIMEOUT)

    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        pytest.fail(
            f"{tool_name} raised {type(exc).__name__} "
            f"with source '{source}': {exc}")

    _ok(f"{tool_name} ran for {TOOL_TIMEOUT}s with source '{source}' "
        "without error")
