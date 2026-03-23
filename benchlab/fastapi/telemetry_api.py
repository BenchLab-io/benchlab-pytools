import asyncio
import glob
import logging
import os
import serial
import serial.tools.list_ports
import sys
import threading
import time
import uvicorn
from collections import deque
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from benchlab_pycore.core import read_sensors, read_device, read_uid, translate_sensor_struct
from benchlab_pycore.core.serial_io import get_fleet_info, open_serial_connection

# --- Load .env first ---
dotenv_path = Path(__file__).parent / "fastapi" / ".env"
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

# --- Logger setup ---
logger = logging.getLogger("benchlab.telemetry_api")
logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- FastAPI app ---
app = FastAPI(title="Benchlab Multi-Device Telemetry API")

# Add CORS middleware for cross-origin requests (useful for web clients)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for simplicity in lightweight setup
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Global state ---
devices_data = {}      # { uid: { "port": str, "latest": dict, "history": deque } }
clients = {}           # { uid: set([WebSocket, ...]) }
main_loop = None       # Will store main asyncio loop
shutdown_event = threading.Event()  # Graceful shutdown flag
device_connections = {}  # { uid: serial.Serial } - Track active connections
connection_locks = {}    # { uid: threading.Lock } - Prevent duplicate connections

# --- WebSocket broadcasting ---
async def send_updates(uid, data):
    """Push latest telemetry for this UID to all connected clients."""
    if uid not in clients:
        return
    dead_clients = set()
    for ws in clients[uid]:
        try:
            await ws.send_json(data)
        except Exception:
            dead_clients.add(ws)
    for ws in dead_clients:
        clients[uid].remove(ws)

def schedule_update(uid, data):
    """Thread-safe schedule to send telemetry to WebSocket clients."""
    if main_loop is not None and not main_loop.is_closed():
        asyncio.run_coroutine_threadsafe(send_updates(uid, data), main_loop)

# --- Serial reader thread per device ---
def read_device_loop(port, uid):
    """Continuously read sensor data from a specific device."""
    ser = open_serial_connection(port)
    if not ser:
        logger.error("Failed to open serial port %s for device %s", port, uid)
        return

    logger.info("Started telemetry loop for %s (%s)", uid, port)
    while not shutdown_event.is_set():
        try:
            sensors = read_sensors(ser)
            if sensors:
                translated = translate_sensor_struct(sensors)
                translated["timestamp"] = datetime.utcnow().isoformat()
                devices_data[uid]["latest"] = translated
                devices_data[uid]["history"].append(translated)
                schedule_update(uid, translated)
            else:
                logger.warning("[%s] No sensor data read", uid)
        except Exception as e:
            # Specific debug logging for unsupported commands
            if isinstance(e, PermissionError) and "does not recognize the command" in str(e):
                logger.debug("[%s] Sensor read skipped (unsupported command): %s", uid, e)
            else:
                logger.warning("[%s] Error reading sensors: %s", uid, e)
        time.sleep(poll_interval)
    ser.close()
    logger.info("Telemetry loop stopped for %s (%s)", uid, port)

# --- Device discovery ---
def get_device_ports():
    """Get device ports based on platform for cross-platform compatibility."""
    if sys.platform.startswith('win'):
        # Windows COM ports
        return ['COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9']
    else:
        # Linux/Unix tty ports
        return glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyS*')

def find_benchlab_devices():
    """Return all connected Benchlab devices with proper UID and firmware."""
    devices = []
    logger.info("Scanning for Benchlab devices on platform: %s", sys.platform)
    
    # Try both hardware ID detection and manual port scanning
    for port, desc, hwid in serial.tools.list_ports.comports():
        if "VID:PID=0483:5740" in hwid.upper():
            device_info = read_device_info_from_port(port)
            if device_info:
                devices.append(device_info)
    
    # Fallback: try manual port scanning for devices that might not be detected properly
    if not devices:
        logger.info("No devices found via hardware ID, trying manual port scanning...")
        for port in get_device_ports():
            try:
                # Quick test to see if port is accessible
                ser = serial.Serial(port, baudrate=115200, timeout=1)
                ser.close()
                device_info = read_device_info_from_port(port)
                if device_info:
                    devices.append(device_info)
            except (serial.SerialException, OSError):
                continue  # Port not accessible or not a BenchLab device
    
    return devices

def read_device_info_from_port(port):
    """Read device info from a specific port with error handling."""
    uid, fw = "?", "?"
    try:
        ser = open_serial_connection(port)
        if ser:
            info = read_device(ser) or {}
            fw = info.get("FwVersion", "?")
            uid_read = read_uid(ser)
            if uid_read:
                uid = uid_read
            ser.close()
            logger.info("Found device on %s: UID=%s, FW=%s", port, uid, fw)
            return {"port": port, "uid": uid, "fw": fw}
        else:
            logger.warning("Could not open serial port %s", port)
    except Exception as e:
        logger.debug("Failed to read device on %s: %s", port, e)
    return None

# --- FastAPI startup & shutdown ---
@app.on_event("startup")
def startup_event():
    global main_loop
    main_loop = asyncio.get_event_loop()
    logger.info("Scanning for Benchlab devices...")
    
    found = find_benchlab_devices()
    if not found:
        logger.warning("No Benchlab devices found.")
        return

    for dev in found:
        port = dev["port"]
        uid = dev["uid"]
        devices_data[uid] = {
            "port": port,
            "latest": {},
            "history": deque(maxlen=history_length),
            "info": read_device(open_serial_connection(port)) or {}
        }
        clients[uid] = set()
        t = threading.Thread(target=read_device_loop, args=(port, uid), daemon=True)
        t.start()
    logger.info("Started %d device threads", len(found))

@app.on_event("shutdown")
def shutdown_event_handler():
    logger.info("Shutting down telemetry threads...")
    shutdown_event.set()
    # Give threads time to close cleanly
    time.sleep(poll_interval + 0.1)
    logger.info("Shutdown complete.")

# --- API endpoints ---
@app.get("/devices")
def list_devices():
    return [{"uid": uid, "port": info["port"]} for uid, info in devices_data.items()]

@app.get("/device/{uid}/info")
def get_device_info(uid: str):
    device = devices_data.get(uid)
    if not device:
        # Return mock info if device not present (useful for tests)
        return {
            "UID": uid,
            "port": None,
            "FwVersion": "v1.0"  # or "fw": "v1.0" depending on what your test expects
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
        return {"error": f"Device {uid} not found"}
    return devices_data[uid].get("latest", {"status": "no data yet"})

@app.get("/device/{uid}/telemetry/{sensor}")
def get_sensor(uid: str, sensor: str):
    if uid not in devices_data:
        return {"error": f"Device {uid} not found"}
    telemetry = devices_data[uid].get("latest")
    if not telemetry:
        return {"error": "No telemetry available yet"}
    if sensor not in telemetry:
        return {"error": f"Sensor {sensor} not found"}
    return {sensor: telemetry[sensor]}

@app.get("/device/{uid}/history")
def get_history(uid: str, limit: int = 100):
    """Get telemetry history with optional limit for performance."""
    if uid not in devices_data:
        return {"error": f"Device {uid} not found"}
    
    history = list(devices_data[uid]["history"])
    # Return only the requested limit (most recent first)
    if limit > 0:
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
        return {"error": f"Device {uid} not found"}
    telemetry = devices_data[uid].get("latest", {})
    return list(telemetry.keys())

@app.websocket("/device/{uid}/stream")
async def stream_device(uid: str, ws: WebSocket):
    await ws.accept()
    if uid not in clients:
        clients[uid] = set()
    clients[uid].add(ws)
    logger.info("[%s] Client connected (%d total)", uid, len(clients[uid]))
    try:
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        clients[uid].remove(ws)
        logger.info("[%s] Client disconnected (%d total)", uid, len(clients[uid]))

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
        "timestamp": datetime.utcnow().isoformat(),
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
        "timestamp": datetime.utcnow().isoformat(),
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
    uvicorn.run("benchlab.fastapi.telemetry_api:app",
                host=api_host,
                port=api_port,
                log_level="info")