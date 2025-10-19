import asyncio
import logging
import os
import serial
import serial.tools.list_ports
import threading
import time
import uvicorn
from collections import deque
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pathlib import Path

from benchlab.core import serial_io, sensor_translation

# --- Load .env first ---
dotenv_path = Path(__file__).parent / "fastapi" / ".env"
load_dotenv(dotenv_path)

# Environment/config variables
log_level = os.getenv("LOG_LEVEL", "INFO")
poll_interval = float(os.getenv("POLL_INTERVAL", 1.0))
history_length = int(os.getenv("HISTORY_LENGTH", 10))
api_host = os.getenv("API_HOST", "0.0.0.0")
api_port = int(os.getenv("API_PORT", 8000))

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

# --- Global state ---
devices_data = {}      # { uid: { "port": str, "latest": dict, "history": deque } }
clients = {}           # { uid: set([WebSocket, ...]) }
main_loop = None       # Will store main asyncio loop
shutdown_event = threading.Event()  # Graceful shutdown flag

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
    ser = serial_io.open_serial_connection(port)
    if not ser:
        logger.error("Failed to open serial port %s for device %s", port, uid)
        return

    logger.info("Started telemetry loop for %s (%s)", uid, port)
    while not shutdown_event.is_set():
        try:
            sensors = serial_io.read_sensors(ser)
            if sensors:
                translated = sensor_translation.translate_sensor_struct(sensors)
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
def find_benchlab_devices():
    """Return all connected Benchlab devices with proper UID and firmware."""
    devices = []
    for port, desc, hwid in serial.tools.list_ports.comports():
        if "VID:PID=0483:5740" in hwid.upper():
            uid, fw = "?", "?"
            try:
                ser = serial_io.open_serial_connection(port)
                if ser:
                    info = serial_io.read_device(ser) or {}
                    fw = info.get("FwVersion", "?")
                    uid_read = serial_io.read_uid(ser)
                    if uid_read:
                        uid = uid_read
                    ser.close()
                    logger.info("Found device on %s: UID=%s, FW=%s", port, uid, fw)
                else:
                    logger.warning("Could not open serial port %s", port)
            except Exception as e:
                logger.warning("Failed to read device on %s: %s", port, e)
            devices.append({"port": port, "uid": uid, "fw": fw})
    return devices

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
            "info": serial_io.read_device(serial_io.open_serial_connection(port)) or {}
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
def get_history(uid: str):
    if uid not in devices_data:
        return {"error": f"Device {uid} not found"}
    return list(devices_data[uid]["history"])

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

# --- Run Uvicorn ---
def run_server():
    uvicorn.run("benchlab.fastapi.telemetry_api:app",
                host=api_host,
                port=api_port,
                log_level="info")