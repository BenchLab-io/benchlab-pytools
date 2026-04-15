"""
Xeneon Dashboard Main Module (iframe + touchscreen enabled)

- Supports direct / mqtt / fastapi proxy modes
- Safe iframe embedding
- Optimized for 2560x720 kiosk touchscreen displays
"""

import asyncio
import logging
import threading
import types
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("benchlab.xeneon")

app = FastAPI(title="Xeneon Dashboard", version="0.1.0")

# ---------------------------
# CORS
# ---------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ---------------------------
# IFRAME + SECURITY HEADERS
# ---------------------------
class IFrameMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        # Allow iframe embedding (tighten in production if needed)
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *"

        return response


app.add_middleware(IFrameMiddleware)

# ---------------------------
# Paths
# ---------------------------
static_dir = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"

app.mount("/xeneon/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

# ---------------------------
# Globals
# ---------------------------
_args: Optional[types.SimpleNamespace] = None
_datasource_manager = None
_http_client: Optional[httpx.AsyncClient] = None
_telemetry_lock = threading.Lock()


# ---------------------------
# Startup / Shutdown
# ---------------------------
@app.on_event("startup")
async def startup_event():
    global _datasource_manager, _http_client

    source = _args.source if _args else "direct"

    if source == "fastapi":
        api_url = getattr(_args, "api_url", "http://127.0.0.1:8000")
        logger.info(f"Xeneon: proxy mode -> {api_url}")
        _http_client = httpx.AsyncClient(base_url=api_url, timeout=10.0)

    else:
        from benchlab.core.datasource_manager import DataSourceManager

        logger.info(f"Xeneon: datasource mode -> {source}")

        ds_kwargs = {}
        if source == "mqtt" and _args:
            ds_kwargs["broker"] = getattr(_args, "mqtt_broker", "localhost")
            ds_kwargs["port"] = getattr(_args, "mqtt_port", 1883)

        _datasource_manager = DataSourceManager(source_type=source, **ds_kwargs)

        if not _datasource_manager.connect():
            logger.error("Xeneon: failed to connect DataSourceManager")
            _datasource_manager = None
        else:
            logger.info("Xeneon: DataSourceManager connected")

    logger.info("Xeneon ready at /xeneon")


@app.on_event("shutdown")
async def shutdown_event():
    if _http_client:
        await _http_client.aclose()
    if _datasource_manager:
        _datasource_manager.disconnect()


# ---------------------------
# Helpers
# ---------------------------
def _dsm_list_devices() -> list:
    if not _datasource_manager:
        return []
    try:
        return [
            {"uid": uid, "port": info.get("port", "?"), **info}
            for uid, info in _datasource_manager.list_devices().items()
        ]
    except Exception as e:
        logger.error(f"list_devices: {e}")
        return []


def _dsm_get_telemetry(uid: str) -> dict:
    if not _datasource_manager:
        return {}
    try:
        with _telemetry_lock:
            _datasource_manager.select_device(uid)
            snap = _datasource_manager.snapshot()
        return snap.get("sensor_data") or snap.get("all_telemetry", {}).get(uid, {})
    except Exception as e:
        logger.error(f"get_telemetry({uid}): {e}")
        return {}


def _dsm_get_info(uid: str) -> dict:
    if not _datasource_manager:
        return {}
    try:
        with _telemetry_lock:
            snap = _datasource_manager.snapshot()
        return {"UID": uid, **snap.get("all_devices", {}).get(uid, {})}
    except Exception as e:
        logger.error(f"get_info({uid}): {e}")
        return {"UID": uid}


# ---------------------------
# UI ROUTES
# ---------------------------
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(
        '<html><head><meta http-equiv="refresh" content="0; url=/xeneon/dashboard"></head></html>'
    )


@app.get("/xeneon/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "embed": False, "kiosk": False},
    )


# ✅ IFRAME EMBED + TOUCH MODE
@app.get("/xeneon/embed", response_class=HTMLResponse)
async def dashboard_embed(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "embed": True, "kiosk": True},
    )


# ---------------------------
# HEALTH
# ---------------------------
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "xeneon-dashboard",
        "version": "0.1.0",
    }


# ---------------------------
# CONFIG
# ---------------------------
@app.get("/config")
async def config():
    return {
        "title": "Xeneon Dashboard",
        "version": "0.1.0",
        "refresh_interval": 1000,
        "touch_enabled": True,
        "theme": {
            "primary": "#2ecc71",
            "danger": "#e74c3c",
            "warning": "#f1c40f",
            "info": "#3498db",
            "muted": "#34495e",
        },
        "tabs": [
            {"id": "fleet", "name": "Fleet", "icon": "📡"},
            {"id": "device", "name": "Device", "icon": "🖥️"},
            {"id": "system", "name": "System", "icon": "⚡"},
            {"id": "voltage", "name": "Voltage", "icon": "🔋"},
            {"id": "temperature", "name": "Temperature", "icon": "🌡️"},
            {"id": "fans", "name": "Fans", "icon": "🌀"},
        ],
    }


# ---------------------------
# API
# ---------------------------
@app.get("/api/devices")
async def api_devices():
    if _datasource_manager:
        return _dsm_list_devices()
    return []


@app.get("/api/device/{uid}/telemetry")
async def api_telemetry(uid: str):
    if _datasource_manager:
        return _dsm_get_telemetry(uid)
    return {"error": "no data"}


@app.get("/api/device/{uid}/info")
async def api_device_info(uid: str):
    if _datasource_manager:
        info = _dsm_get_info(uid)
        if not info:
            raise HTTPException(status_code=404, detail="Device not found")
        return info
    return {"error": "no data"}


# ---------------------------
# WEBSOCKET STREAM
# ---------------------------
@app.websocket("/api/device/{uid}/stream")
async def ws_stream(uid: str, ws: WebSocket):
    await ws.accept()

    try:
        while True:
            if uid == "all":
                payload = {}
                for d in _dsm_list_devices():
                    payload.update(_dsm_get_telemetry(d["uid"]))
            else:
                payload = _dsm_get_telemetry(uid)

            await ws.send_json(payload)
            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WS error: {e}")


# ---------------------------
# FAVICON
# ---------------------------
@app.get("/favicon.ico")
async def favicon():
    p = Path(__file__).parent / "favicon.ico"
    if p.exists():
        return FileResponse(p)
    raise HTTPException(status_code=404)


# ---------------------------
# RUN
# ---------------------------
def run_xeneon(args=None):
    global _args

    if args is None:
        args = types.SimpleNamespace(
            source="direct",
            api_url="http://127.0.0.1:8000",
            api_port=8000,
            mqtt_broker="localhost",
            mqtt_port=1883,
            interval=1.0,
        )

    _args = args

    import uvicorn

    print("Starting Xeneon Dashboard...")
    print("Dashboard: http://localhost:8001/xeneon/dashboard")
    print("Embed:     http://localhost:8001/xeneon/embed")
    print("Press Ctrl+C to stop")
    print("-" * 50)

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")


if __name__ == "__main__":
    run_xeneon()