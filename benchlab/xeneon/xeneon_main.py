"""
Xeneon Dashboard Main Module (iframe + touchscreen enabled)

- Supports direct / mqtt / fastapi proxy modes
- Safe iframe embedding
- Optimised for 2560×720 kiosk touchscreen displays

Fixes applied:
  - Replaced deprecated @app.on_event("startup"/"shutdown") with lifespan context manager
  - Replaced threading.Lock with asyncio.Lock (safe in async context)
  - Added POST /api/shutdown endpoint (window.close() is blocked by browsers)
  - Background WebSocket data collection moved to asyncio.Task to avoid blocking event loop
"""

import asyncio
import logging
import os
import signal
import types
from contextlib import asynccontextmanager
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

# ---------------------------
# Globals
# ---------------------------
_args: Optional[types.SimpleNamespace] = None
_datasource_manager = None
_http_client: Optional[httpx.AsyncClient] = None
# asyncio.Lock is the correct choice inside async handlers
_telemetry_lock: Optional[asyncio.Lock] = None


# ---------------------------
# Lifespan (replaces deprecated @app.on_event)
# ---------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown in a single context manager."""
    global _datasource_manager, _http_client, _telemetry_lock

    _telemetry_lock = asyncio.Lock()

    source = _args.source if _args else "direct"

    if source == "fastapi":
        # Default to 8001 so we don't collide with an existing server on 8000
        api_url = getattr(_args, "api_url", "http://127.0.0.1:8001")
        logger.info(f"Xeneon: proxy mode -> {api_url}")
        _http_client = httpx.AsyncClient(base_url=api_url, timeout=10.0)

    else:
        from benchlab.core.datasource_manager import DataSourceManager

        logger.info(f"Xeneon: datasource mode -> {source}")

        ds_kwargs = {}
        if source == "mqtt" and _args:
            ds_kwargs["broker"] = getattr(_args, "mqtt_broker", "localhost")
            ds_kwargs["port"]   = getattr(_args, "mqtt_port", 1883)

        _datasource_manager = DataSourceManager(source_type=source, **ds_kwargs)

        if not _datasource_manager.connect():
            logger.error("Xeneon: failed to connect DataSourceManager")
            _datasource_manager = None
        else:
            logger.info("Xeneon: DataSourceManager connected")

    logger.info("Xeneon ready at /xeneon")

    yield  # application runs here

    # --- shutdown ---
    if _http_client:
        await _http_client.aclose()
    if _datasource_manager:
        _datasource_manager.disconnect()


# ---------------------------
# App
# ---------------------------
app = FastAPI(title="Xeneon Dashboard", version="0.1.0", lifespan=lifespan)

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
static_dir    = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"

app.mount("/xeneon/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)


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


async def _dsm_get_telemetry(uid: str) -> dict:
    """Async-safe telemetry fetch using asyncio.Lock."""
    if not _datasource_manager:
        return {}
    try:
        async with _telemetry_lock:
            _datasource_manager.select_device(uid)
            snap = _datasource_manager.snapshot()
        return snap.get("sensor_data") or snap.get("all_telemetry", {}).get(uid, {})
    except Exception as e:
        logger.error(f"get_telemetry({uid}): {e}")
        return {}


async def _dsm_get_info(uid: str) -> dict:
    """Async-safe device info fetch using asyncio.Lock."""
    if not _datasource_manager:
        return {}
    try:
        async with _telemetry_lock:
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


@app.get("/xeneon", response_class=HTMLResponse)
@app.get("/xeneon/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "embed": False, "kiosk": False},
    )


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
            "primary": "#FCE477",
            "danger":  "#e74c3c",
            "warning": "#f1c40f",
            "info":    "#3498db",
            "muted":   "#808080",
        },
        "tabs": [
            {"id": "fleet",       "name": "Fleet",       "icon": "📡"},
            {"id": "device",      "name": "Device",      "icon": "🖥️"},
            {"id": "system",      "name": "System",      "icon": "⚡"},
            {"id": "voltage",     "name": "Voltage",     "icon": "🔋"},
            {"id": "temperature", "name": "Temperature", "icon": "🌡️"},
            {"id": "fans",        "name": "Fans",        "icon": "🌀"},
        ],
    }


# ---------------------------
# API
# ---------------------------
async def _proxy_get(path: str):
    """Forward a GET request to the upstream FastAPI server in proxy mode."""
    if not _http_client:
        raise HTTPException(status_code=503, detail="Proxy client not initialised")
    try:
        resp = await _http_client.get(path)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}")


@app.get("/api/devices")
async def api_devices():
    if _http_client:
        return await _proxy_get("/api/devices")
    if _datasource_manager:
        return _dsm_list_devices()
    return []


@app.get("/api/device/{uid}/telemetry")
async def api_telemetry(uid: str):
    if _http_client:
        return await _proxy_get(f"/api/device/{uid}/telemetry")
    if _datasource_manager:
        return await _dsm_get_telemetry(uid)
    return {"error": "no data"}


@app.get("/api/device/{uid}/info")
async def api_device_info(uid: str):
    if _http_client:
        return await _proxy_get(f"/api/device/{uid}/info")
    if _datasource_manager:
        info = await _dsm_get_info(uid)
        if not info:
            raise HTTPException(status_code=404, detail="Device not found")
        return info
    return {"error": "no data"}


# ---------------------------
# SHUTDOWN ENDPOINT
# window.close() is blocked by browsers; the JS calls this instead.
# ---------------------------
@app.post("/api/shutdown")
async def api_shutdown():
    """Gracefully shut the server down after responding."""
    logger.info("Xeneon: shutdown requested via API")

    async def _stop():
        await asyncio.sleep(0.2)          # give the response time to send
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_stop())
    return {"status": "shutting_down"}


# ---------------------------
# WEBSOCKET STREAM
# ---------------------------
@app.websocket("/api/device/{uid}/stream")
async def ws_stream(uid: str, ws: WebSocket):
    await ws.accept()

    try:
        while True:
            if uid == "all":
                # Collect telemetry for every device concurrently
                devices = _dsm_list_devices()
                results = await asyncio.gather(
                    *[_dsm_get_telemetry(d["uid"]) for d in devices],
                    return_exceptions=True,
                )
                payload: dict = {}
                for result in results:
                    if isinstance(result, dict):
                        payload.update(result)
            else:
                payload = await _dsm_get_telemetry(uid)

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
            api_url="http://127.0.0.1:8001",
            api_port=8001,
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

    port = getattr(_args, "api_port", 8001)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run_xeneon()