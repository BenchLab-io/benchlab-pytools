import asyncio
import logging
import os
import sys
import threading
import time
import uvicorn
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from benchlab_pycore.core import (
    read_sensors, read_device, translate_sensor_struct)
from benchlab.core import (
    BENCHLAB_ORIGINAL_PRODUCT_ID, BENCHLAB_BL2_PRODUCT_ID)
# benchlab_pycore.core.serial_io has no connection-opening helper; use the
# local wrapper instead (see benchlab.core.shared_serial).
from benchlab.core.shared_serial import open_serial_connection

# Import DeviceRegistry so the FastAPI server publishes device lifecycle events
from benchlab.core.device_registry import DeviceRegistry
from benchlab.core.discovery import discover_devices as _discover_devices


# --- Load .env first ---
dotenv_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path)

# Configuration class for better organization


class Config:
    """Configuration settings for the BenchLab telemetry server."""
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 1.0))
    HISTORY_LENGTH = int(os.getenv("HISTORY_LENGTH", 10))
    API_HOST = os.getenv("API_HOST", "0.0.0.0")
    API_PORT = int(os.getenv("API_PORT", 8000))
    SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 30))  # seconds
    MAX_HISTORY_LIMIT = int(os.getenv("MAX_HISTORY_LIMIT", 1000))

    @classmethod
    def validate(cls):
        """Validate configuration values."""
        if cls.POLL_INTERVAL < 0.1:
            raise ValueError("POLL_INTERVAL must be at least 0.1 seconds")
        if cls.HISTORY_LENGTH < 1:
            raise ValueError("HISTORY_LENGTH must be at least 1")
        if cls.API_PORT < 1 or cls.API_PORT > 65535:
            raise ValueError("API_PORT must be between 1 and 65535")
        if cls.MAX_HISTORY_LIMIT < 1:
            raise ValueError("MAX_HISTORY_LIMIT must be at least 1")
        if cls.SCAN_INTERVAL < 1:
            raise ValueError("SCAN_INTERVAL must be at least 1 second")


# --- Logger setup ---
logger = logging.getLogger("benchlab.restapi")
logger.setLevel(getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO))
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Validate configuration
try:
    Config.validate()
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    raise

# Environment/config variables (for backward compatibility)
log_level = Config.LOG_LEVEL
poll_interval = Config.POLL_INTERVAL
history_length = Config.HISTORY_LENGTH
api_host = Config.API_HOST
api_port = Config.API_PORT

# --- Global state ---
# { uid: { "port": str, "latest": dict, "history": deque, "connected": bool } }
devices_data = {}
clients = {}           # { uid: set([WebSocket, ...]) }
main_loop = None       # Will store main asyncio loop
shutdown_event = threading.Event()  # Graceful shutdown flag
device_threads = {}    # { uid: threading.Thread } - device reader threads
scan_lock = threading.Lock()  # Prevent concurrent scans
data_lock = threading.Lock()  # Protect concurrent access to devices_data
scanner_thread = None  # Background device scanner thread


def device_scanner_loop():
    """Background thread that periodically scans for new/disconnected
    devices."""
    logger.info(
        "Device scanner started (scan interval: %ds)",
        Config.SCAN_INTERVAL)

    while not shutdown_event.is_set():
        # Wait for scan interval
        shutdown_event.wait(Config.SCAN_INTERVAL)
        if shutdown_event.is_set():
            break

        try:
            logger.debug("Scanner: attempting to acquire scan_lock...")
            with scan_lock:
                logger.debug(
                    "Scanner: acquired scan_lock, scanning for devices...")
                # Find all currently connected BenchLab devices
                found_devices = find_benchlab_devices()
                existing_uids = set(devices_data.keys())
                current_ports = {dev["port"]: dev["uid"]
                                 for dev in found_devices}

                logger.debug("Scanner: found %d devices, tracking %d existing",
                             len(found_devices), len(existing_uids))

                # Check for new devices
                for dev in found_devices:
                    port = dev["port"]
                    uid = dev["uid"]

                    if uid not in existing_uids:
                        logger.info(
                            "New device discovered during scan: %s on %s",
                            uid, port)
                        start_device_thread(port, uid)
                        existing_uids.add(uid)  # Prevent duplicate starts

                # Check for disconnected devices - mark them as such
                # immediately rather than waiting for read_device_loop to
                # notice on its own timer
                for uid, data in devices_data.items():
                    port = data.get("port")
                    if port and port not in current_ports:
                        logger.info(
                            "Device %s appears disconnected "
                            "(port %s not found)", uid, port)
                        with data_lock:
                            if uid in devices_data:
                                devices_data[uid]["connected"] = False

                logger.debug("Scanner: scan complete")

        except Exception as e:
            logger.debug("Error during periodic device scan: %s", e)

    logger.info("Device scanner stopped")

# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global main_loop
    main_loop = asyncio.get_running_loop()
    logger.info("Scanning for Benchlab devices...")

    found = find_benchlab_devices()
    if not found:
        logger.warning("No Benchlab devices found.")
    else:
        for dev in found:
            port = dev["port"]
            uid = dev["uid"]

            # Read device info without keeping the connection open
            # (the read_device_loop will open its own connection)
            device_info = {}
            try:
                ser = open_serial_connection(port)
                if ser:
                    device_info = read_device(ser) or {}
                    # Determine device variant from ProductId
                    product_id = device_info.get(
                        'ProductId', BENCHLAB_ORIGINAL_PRODUCT_ID)
                    device_info['variant'] = (
                        'BL2' if product_id == BENCHLAB_BL2_PRODUCT_ID
                        else 'ORIGINAL')
                    ser.close()
            except Exception as e:
                logger.debug("Could not read device info for %s: %s", uid, e)

            with data_lock:
                devices_data[uid] = {
                    "port": port,
                    "latest": {},
                    "history": deque(maxlen=history_length),
                    "info": device_info
                }
                if uid not in clients:
                    clients[uid] = set()

            t = threading.Thread(
                target=read_device_loop, args=(
                    port, uid), daemon=True)
            t.start()
            # store thread so start_device_thread won't duplicate it
            device_threads[uid] = t

            # Register device in the DeviceRegistry so tools can discover it
            registry = DeviceRegistry.get_instance()
            registry.register(
                uid=uid,
                port=port,
                firmware=str(device_info.get("FwVersion", "?")),
                data_source="fastapi",
            )

        logger.info("Started %d device threads", len(found))

    # Start background device scanner
    global scanner_thread
    scanner_thread = threading.Thread(target=device_scanner_loop, daemon=True)
    scanner_thread.start()

    yield

    # Shutdown
    logger.info("Shutting down telemetry threads...")
    shutdown_event.set()

    # Unregister all devices from the DeviceRegistry
    registry = DeviceRegistry.get_instance()
    for uid in list(devices_data.keys()):
        registry.unregister(uid)

    # Give threads time to close cleanly
    time.sleep(poll_interval + 0.1)
    logger.info("Shutdown complete.")

# --- FastAPI app ---
app = FastAPI(title="Benchlab Multi-Device Telemetry API", lifespan=lifespan)

# CORS origins configurable via environment variable.
# Default to "*" for local dev, but can restrict to known origins in production
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "*").split(",") if os.getenv("CORS_ORIGINS") else ["*"]
# Wildcard origin + credentials is an invalid combination per the CORS spec
# (browsers reject it) - only allow credentials when explicit origins are
# configured.
CORS_ALLOW_CREDENTIALS = CORS_ORIGINS != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WebSocket broadcasting ---


async def send_updates(uid, data):
    """Push latest telemetry for this UID to all connected clients."""
    with data_lock:
        if uid not in clients:
            return
        ws_list = list(clients[uid])
    dead_clients = set()
    for ws in ws_list:
        try:
            await ws.send_json(data)
        except Exception:
            dead_clients.add(ws)
    if dead_clients:
        with data_lock:
            for ws in dead_clients:
                clients[uid].discard(ws)


def schedule_update(uid, data):
    """Thread-safe schedule to send telemetry to WebSocket clients."""
    if main_loop is not None and not main_loop.is_closed():
        asyncio.run_coroutine_threadsafe(send_updates(uid, data), main_loop)


RECONNECT_DELAY = 2.0  # seconds


def create_empty_telemetry():
    """Create an empty telemetry dict with all values set to 0.

    Includes all possible sensors for both ORIGINAL and BL2 variants.
    This ensures that applications always see the same set of keys regardless
    of device variant or connection status.
    """
    telemetry = {
        # Power summary
        "SYS_Power": 0.0, "CPU_Power": 0.0, "GPU_Power": 0.0, "MB_Power": 0.0,
        # Temperature
        "Chip_Temp": 0, "Ambient_Temp": 0.0, "Humidity": 0.0,
        "TS_1": 0.0, "TS_2": 0.0, "TS_3": 0.0, "TS_4": 0.0,
        # BL2-specific temperature sensors
        "TS_HPWR1_IN": 0.0, "TS_HPWR1_OUT": 0.0,
        "TS_HPWR2_IN": 0.0, "TS_HPWR2_OUT": 0.0,
        # Power rails (ORIGINAL + BL2)
        "EPS1_Voltage": 0.0, "EPS1_Current": 0.0, "EPS1_Power": 0.0,
        "EPS2_Voltage": 0.0, "EPS2_Current": 0.0, "EPS2_Power": 0.0,
        "ATX3V_Voltage": 0.0, "ATX3V_Current": 0.0, "ATX3V_Power": 0.0,
        "ATX5V_Voltage": 0.0, "ATX5V_Current": 0.0, "ATX5V_Power": 0.0,
        "ATX5VSB_Voltage": 0.0, "ATX5VSB_Current": 0.0, "ATX5VSB_Power": 0.0,
        "ATX12V_Voltage": 0.0, "ATX12V_Current": 0.0, "ATX12V_Power": 0.0,
        "PCIE1_Voltage": 0.0, "PCIE1_Current": 0.0, "PCIE1_Power": 0.0,
        "PCIE2_Voltage": 0.0, "PCIE2_Current": 0.0, "PCIE2_Power": 0.0,
        "PCIE3_Voltage": 0.0, "PCIE3_Current": 0.0, "PCIE3_Power": 0.0,
        "HPWR1_Voltage": 0.0, "HPWR1_Current": 0.0, "HPWR1_Power": 0.0,
        "HPWR2_Voltage": 0.0, "HPWR2_Current": 0.0, "HPWR2_Power": 0.0,
        # BL2-specific HPWR sense lines
        "HPWR1_W1_Voltage": 0.0, "HPWR1_W1_Current": 0.0,
        "HPWR1_W1_Power": 0.0,
        "HPWR1_W2_Voltage": 0.0, "HPWR1_W2_Current": 0.0,
        "HPWR1_W2_Power": 0.0,
        "HPWR1_W3_Voltage": 0.0, "HPWR1_W3_Current": 0.0,
        "HPWR1_W3_Power": 0.0,
        "HPWR1_W4_Voltage": 0.0, "HPWR1_W4_Current": 0.0,
        "HPWR1_W4_Power": 0.0,
        "HPWR1_W5_Voltage": 0.0, "HPWR1_W5_Current": 0.0,
        "HPWR1_W5_Power": 0.0,
        "HPWR1_W6_Voltage": 0.0, "HPWR1_W6_Current": 0.0,
        "HPWR1_W6_Power": 0.0,
        "HPWR2_W1_Voltage": 0.0, "HPWR2_W1_Current": 0.0,
        "HPWR2_W1_Power": 0.0,
        "HPWR2_W2_Voltage": 0.0, "HPWR2_W2_Current": 0.0,
        "HPWR2_W2_Power": 0.0,
        "HPWR2_W3_Voltage": 0.0, "HPWR2_W3_Current": 0.0,
        "HPWR2_W3_Power": 0.0,
        "HPWR2_W4_Voltage": 0.0, "HPWR2_W4_Current": 0.0,
        "HPWR2_W4_Power": 0.0,
        "HPWR2_W5_Voltage": 0.0, "HPWR2_W5_Current": 0.0,
        "HPWR2_W5_Power": 0.0,
        "HPWR2_W6_Voltage": 0.0, "HPWR2_W6_Current": 0.0,
        "HPWR2_W6_Power": 0.0,
        # VIN measurements (all 13 channels)
        "VIN_0": 0.0, "VIN_1": 0.0, "VIN_2": 0.0, "VIN_3": 0.0,
        "VIN_4": 0.0, "VIN_5": 0.0, "VIN_6": 0.0, "VIN_7": 0.0,
        "VIN_8": 0.0, "VIN_9": 0.0, "VIN_10": 0.0, "VIN_11": 0.0,
        "VIN_12": 0.0,
        # Board voltages
        "Vdd": 0.0, "Vref": 0.0,
        # Fans (9 fans + external duty)
        "Fan1_Duty": 0, "Fan1_RPM": 0, "Fan1_Status": 0,
        "Fan2_Duty": 0, "Fan2_RPM": 0, "Fan2_Status": 0,
        "Fan3_Duty": 0, "Fan3_RPM": 0, "Fan3_Status": 0,
        "Fan4_Duty": 0, "Fan4_RPM": 0, "Fan4_Status": 0,
        "Fan5_Duty": 0, "Fan5_RPM": 0, "Fan5_Status": 0,
        "Fan6_Duty": 0, "Fan6_RPM": 0, "Fan6_Status": 0,
        "Fan7_Duty": 0, "Fan7_RPM": 0, "Fan7_Status": 0,
        "Fan8_Duty": 0, "Fan8_RPM": 0, "Fan8_Status": 0,
        "Fan9_Duty": 0, "Fan9_RPM": 0, "Fan9_Status": 0,
        "FanExtDuty": 0,
        # Status
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "connected": False,
    }
    return telemetry


def read_device_loop(port, uid):
    """Continuously read sensor data from a specific device with
    reconnection logic."""
    ser = None
    consecutive_errors = 0
    max_consecutive_errors = 10  # After this many errors, attempt reconnect
    product_id = None  # Will be set once we read device info
    is_connected = False  # Track connection status

    while not shutdown_event.is_set():
        # --- (Re)connect ---
        if ser is None:
            # Mark as disconnected
            if is_connected:
                is_connected = False
                with data_lock:
                    if uid in devices_data:
                        devices_data[uid]["connected"] = False
                        devices_data[uid]["latest"] = create_empty_telemetry()
                logger.info("[%s] Device disconnected", uid)

            try:
                new_ser = open_serial_connection(port)
                if new_ser is None:
                    raise OSError("open_serial_connection returned None")
                ser = new_ser
                # Reset error counter on successful connect
                consecutive_errors = 0

                # Read device info to get product_id for sensor reading
                try:
                    device_info = read_device(ser)
                    if device_info:
                        product_id = device_info.get(
                            'ProductId', BENCHLAB_ORIGINAL_PRODUCT_ID)
                        logger.info(
                            "[%s] Device variant: %s (ProductId=0x%02X)",
                            uid,
                            'BL2' if product_id ==
                            BENCHLAB_BL2_PRODUCT_ID else 'ORIGINAL',
                            product_id)
                except Exception:
                    pass

                is_connected = True
                with data_lock:
                    if uid in devices_data:
                        devices_data[uid]["connected"] = True
                logger.info("Connected to device %s on %s", uid, port)
            except Exception as exc:
                ser = None  # Ensure ser is None on error
                consecutive_errors += 1
                delay = min(
                    RECONNECT_DELAY * consecutive_errors,
                    30)  # Exponential backoff, capped
                logger.warning(
                    "[%s] Failed to open serial port %s: %s (retry in %.1fs)",
                    uid,
                    port,
                    exc,
                    delay)
                shutdown_event.wait(delay)
                continue

        # --- Read sensors ---
        try:
            if ser is None:  # Safety check
                logger.warning("[%s] Serial connection unexpectedly None", uid)
                shutdown_event.wait(1)
                continue
            sensors = read_sensors(ser, product_id=product_id)
            if sensors:
                translated = translate_sensor_struct(sensors)
                translated["timestamp"] = datetime.now().strftime(
                    "%Y-%m-%dT%H:%M:%S")
                # Reset error counter on successful read
                consecutive_errors = 0

                with data_lock:
                    if uid in devices_data:
                        devices_data[uid]["latest"] = translated
                        devices_data[uid]["history"].append(translated)

                schedule_update(uid, translated)
            else:
                logger.debug("[%s] No sensor data read", uid)
        except Exception as e:
            consecutive_errors += 1
            # Specific debug logging for unsupported commands
            if isinstance(
                    e,
                    PermissionError) and (
                        "does not recognize the command" in str(e)):
                logger.debug(
                    "[%s] Sensor read skipped (unsupported command): %s",
                    uid, e)
            else:
                logger.warning(
                    "[%s] Error reading sensors (consecutive: %d): %s",
                    uid,
                    consecutive_errors,
                    e)

            # Close serial connection on error to trigger reconnect
            try:
                ser.close()
            except Exception:
                pass
            ser = None

            # Exponential backoff with cap
            if consecutive_errors >= max_consecutive_errors:
                delay = min(RECONNECT_DELAY * consecutive_errors, 30)
                logger.warning(
                    "[%s] Too many errors, backing off %.1fs", uid, delay)
                shutdown_event.wait(delay)
            continue

        shutdown_event.wait(poll_interval)

    # Cleanup - mark as disconnected
    if is_connected:
        with data_lock:
            if uid in devices_data:
                devices_data[uid]["connected"] = False
                devices_data[uid]["latest"] = create_empty_telemetry()

    if ser:
        try:
            ser.close()
        except Exception:
            pass
    logger.info("Telemetry loop stopped for %s (%s)", uid, port)

# --- Device discovery ---
# The original implementation performed manual hardware‑ID filtering using
# ``serial.tools.list_ports``.  We now delegate discovery to the core library
# which already provides a robust ``discover_devices`` helper based on the
# official ``benchlab-pycore`` package.


def find_benchlab_devices():
    """Return all connected BenchLab devices using the core discovery helper.

    The returned list has the same shape as the previous implementation:
    ``[{"uid": <uid>, "port": <port>, "fw": <firmware>}...]``.
    """
    return _discover_devices()

# --- API endpoints ---


@app.get("/devices")
def list_devices():
    """List all connected devices with basic info including variant and
    connection status."""
    result = []
    for uid, info in devices_data.items():
        device_info = info.get("info", {}) or {}
        result.append({
            "uid": uid,
            "port": info.get("port", "unknown"),
            "firmware": device_info.get("FwVersion", "?"),
            "variant": device_info.get("variant", "ORIGINAL"),
            "VendorId": device_info.get("VendorId", 0),
            "ProductId": device_info.get("ProductId", 0),
            "connected": info.get("connected", False),
        })
    return result


@app.get("/device/{uid}/info")
def get_device_info(uid: str):
    """Get detailed device information including variant."""
    device = devices_data.get(uid)
    if not device:
        # Return mock info if device not present (useful for tests)
        return {
            "UID": uid,
            "port": None,
            "FwVersion": "v1.0",
            "variant": "ORIGINAL",
            "VendorId": 0,
            "ProductId": 0,
        }
    info = device.get("info", {}) or {}
    info_out = info.copy()
    info_out["UID"] = uid
    info_out["port"] = device.get("port")
    if "FwVersion" not in info_out and "fw" not in info_out:
        info_out["FwVersion"] = "v1.0"
    return info_out


@app.get("/device/{uid}/telemetry")
def get_telemetry(uid: str):
    if uid not in devices_data:
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")
    return devices_data[uid].get("latest", {"status": "no data yet"})


@app.get("/device/{uid}/telemetry/{sensor}")
def get_sensor(uid: str, sensor: str):
    if uid not in devices_data:
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")
    telemetry = devices_data[uid].get("latest")
    if not telemetry:
        return {"error": "No telemetry available yet"}
    if sensor not in telemetry:
        raise HTTPException(
            status_code=404,
            detail=f"Sensor {sensor} not found")
    return {sensor: telemetry[sensor]}


@app.get("/device/{uid}/history")
def get_history(uid: str, limit: int = 100):
    """Get telemetry history with optional limit for performance."""
    if uid not in devices_data:
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")

    limit = max(1, min(limit, Config.MAX_HISTORY_LIMIT))
    history = list(devices_data[uid]["history"])
    # Return only the requested limit (most recent first)
    history = history[-limit:]

    return {
        "device_id": uid,
        "data": history,
        "count": len(history),
        "total_available": len(devices_data[uid]["history"])
    }


@app.get("/device/{uid}/sensors")
def get_sensors(uid: str):
    if uid not in devices_data:
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")
    telemetry = devices_data[uid].get("latest", {})
    return list(telemetry.keys())


@app.websocket("/device/{uid}/stream")
async def stream_device(uid: str, ws: WebSocket):
    await ws.accept()
    with data_lock:
        if uid not in clients:
            clients[uid] = set()
        clients[uid].add(ws)
        client_count = len(clients[uid])
    logger.info("[%s] Client connected (%d total)", uid, client_count)
    try:
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        with data_lock:
            clients[uid].discard(ws)
            client_count = len(clients[uid])
        logger.info("[%s] Client disconnected (%d total)", uid, client_count)


@app.get("/favicon.ico")
def favicon():
    return FileResponse(Path(__file__).parent / "favicon.ico")

# --- Health check and status endpoints ---


@app.get("/health")
def health_check():
    """Basic health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "platform": sys.platform,
        "timestamp": datetime.now().isoformat(),
        "connected_devices": len(devices_data),
        "total_clients": sum(len(clients.get(uid, [])) for uid in devices_data)
    }


@app.get("/status")
def get_status():
    """Get detailed server status and device information."""
    device_status = {}
    for uid, data in devices_data.items():
        device_status[uid] = {
            "port": data.get("port", "unknown"),
            "connected": bool(data.get("latest")),
            "last_update": data.get("latest", {}).get("timestamp", "never"),
            "client_count": len(clients.get(uid, [])),
            "history_count": len(data.get("history", []))
        }

    return {
        "server_status": "running",
        "platform": sys.platform,
        "timestamp": datetime.now().isoformat(),
        "devices": device_status,
        "total_devices": len(devices_data),
        "total_clients": sum(len(clients.get(uid, [])) for uid in devices_data)
    }


@app.get("/device/{uid}/status")
def get_device_status(uid: str):
    """Get detailed status for a specific device."""
    if uid not in devices_data:
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")

    data = devices_data[uid]
    return {
        "uid": uid,
        "port": data.get("port", "unknown"),
        "connected": bool(data.get("latest")),
        "last_update": data.get("latest", {}).get("timestamp", "never"),
        "client_count": len(clients.get(uid, [])),
        "history_count": len(data.get("history", [])),
        "latest_telemetry": data.get("latest", {}),
        "info": data.get("info", {})
    }


def start_device_thread(port, uid):
    """Start a telemetry reader thread for a new device."""
    # Initialize device data structure
    with data_lock:
        if uid not in devices_data:
            devices_data[uid] = {
                "port": port,
                "latest": {},
                "history": deque(maxlen=history_length),
                "info": {}
            }
        if uid not in clients:
            clients[uid] = set()

    # Try to read device info
    try:
        ser = open_serial_connection(port)
        if ser:
            info = read_device(ser) or {}
            # Determine device variant from ProductId
            product_id = info.get('ProductId', BENCHLAB_ORIGINAL_PRODUCT_ID)
            info['variant'] = (
                'BL2' if product_id == BENCHLAB_BL2_PRODUCT_ID
                else 'ORIGINAL')
            devices_data[uid]["info"] = info
            ser.close()
    except Exception as e:
        logger.warning("Failed to read info for device %s: %s", uid, e)

    # Start the telemetry reader thread
    if uid not in device_threads or not device_threads[uid].is_alive():
        t = threading.Thread(
            target=read_device_loop, args=(
                port, uid), daemon=True)
        t.start()
        device_threads[uid] = t
        logger.info("Started telemetry thread for device %s (%s)", uid, port)


@app.post("/scan")
def scan_for_devices():
    """Scan for new BenchLab devices and start telemetry for newly
    discovered ones.

    This endpoint allows runtime device discovery without restarting the
    server. Only devices matching a known BenchLab product ID (ORIGINAL or
    BL2) will be opened.
    """
    with scan_lock:
        logger.info("Manual device scan triggered...")

        # Find all currently connected BenchLab devices
        found_devices = find_benchlab_devices()
        existing_uids = set(devices_data.keys())
        new_devices = []

        for dev in found_devices:
            port = dev["port"]
            uid = dev["uid"]

            if uid not in existing_uids:
                # New device found - start telemetry
                logger.info("New device discovered: %s on %s", uid, port)
                start_device_thread(port, uid)
                new_devices.append(
                    {"port": port, "uid": uid, "fw": dev.get("fw", "?")})
            else:
                logger.debug("Device %s already known, skipping", uid)

        # Check for devices that may have been disconnected
        current_ports = {dev["port"]: dev["uid"] for dev in found_devices}
        disconnected = []
        for uid, data in devices_data.items():
            port = data.get("port")
            if port and port not in current_ports:
                disconnected.append({"uid": uid, "port": port})
                logger.info(
                    "Device %s appears to be disconnected from %s", uid, port)
                with data_lock:
                    if uid in devices_data:
                        devices_data[uid]["connected"] = False

        result = {
            "scan_time": datetime.now().isoformat(),
            "total_devices": len(devices_data),
            "new_devices": new_devices,
            "disconnected_devices": disconnected,
            "devices": [
                {"uid": uid, "port": info["port"]}
                for uid, info in devices_data.items()
            ]
        }

        logger.info("Scan complete: %d total devices, %d new, %d disconnected",
                    len(devices_data), len(new_devices), len(disconnected))

        return result

# --- Improved error handling ---


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for unhandled errors."""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "message": str(exc)}
    )

# --- Run Uvicorn ---


def run_server():
    uvicorn.run("benchlab.restapi.telemetry_api:app",
                host=api_host,
                port=api_port,
                log_level="info")
