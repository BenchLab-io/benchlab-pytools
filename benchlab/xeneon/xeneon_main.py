"""
Xeneon Dashboard Main Module

For direct/mqtt sources: serves telemetry directly from DataSourceManager.
For fastapi source: proxies API calls to the already-running telemetry server.

This means the dashboard never starts a second telemetry server and never
tries to open a serial port that's already held by another component.
"""

import asyncio
import logging
import threading
import time
import types
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("benchlab.xeneon")

app = FastAPI(title="Xeneon Dashboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])

static_dir    = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"
app.mount("/xeneon/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

_args: Optional[types.SimpleNamespace] = None
_datasource_manager = None
_http_client: Optional[httpx.AsyncClient] = None
_telemetry_lock = threading.Lock()


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
            ds_kwargs["port"]   = getattr(_args, "mqtt_port", 1883)
        _datasource_manager = DataSourceManager(source_type=source, **ds_kwargs)
        if not _datasource_manager.connect():
            logger.error("Xeneon: failed to connect DataSourceManager")
            _datasource_manager = None
        else:
            logger.info("Xeneon: DataSourceManager connected")

    logger.info("Xeneon Dashboard ready at http://localhost:8001/xeneon/dashboard")


@app.on_event("shutdown")
async def shutdown_event():
    if _http_client:
        await _http_client.aclose()
    if _datasource_manager:
        _datasource_manager.disconnect()


def _dsm_list_devices() -> list:
    if not _datasource_manager:
        return []
    try:
        return [{"uid": uid, "port": info.get("port", "?"), **info}
                for uid, info in _datasource_manager.list_devices().items()]
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


async def _proxy_get(path: str, **params):
    if not _http_client:
        return None
    try:
        r = await _http_client.get(path, params=params or None)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Proxy GET {path}: {e}")
        return None


async def _proxy_post(path: str):
    if not _http_client:
        return None
    try:
        r = await _http_client.post(path)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Proxy POST {path}: {e}")
        return None


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse('<html><head><meta http-equiv="refresh" content="0; url=/xeneon/dashboard"></head></html>')


@app.get("/xeneon/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/xeneon", response_class=HTMLResponse)
async def dashboard_embed(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "xeneon-dashboard", "version": "0.1.0"}


@app.get("/config")
async def config():
    return {
        "title": "Xeneon Dashboard", "version": "0.1.0", "refresh_interval": 1000,
        "theme": {"primary": "#2ecc71", "danger": "#e74c3c", "warning": "#f1c40f",
                  "info": "#3498db", "muted": "#34495e"},
        "tabs": [
            {"id": "fleet", "name": "Fleet", "icon": "📡"},
            {"id": "device", "name": "Device", "icon": "🖥️"},
            {"id": "system", "name": "System", "icon": "⚡"},
            {"id": "voltage", "name": "Voltage", "icon": "🔋"},
            {"id": "temperature", "name": "Temperature", "icon": "🌡️"},
            {"id": "fans", "name": "Fans", "icon": "🌀"},
        ],
    }


@app.get("/api/devices")
async def api_devices():
    if _datasource_manager:
        return _dsm_list_devices()
    return await _proxy_get("/devices") or []


@app.post("/api/scan")
async def api_scan():
    """Return current known devices without touching the serial port.
    The JS calls this on every poll — for direct/mqtt we never re-scan."""
    if _datasource_manager:
        devices = _dsm_list_devices()
        return {"scan_time": "", "total_devices": len(devices),
                "new_devices": [], "disconnected_devices": [], "devices": devices}
    return await _proxy_post("/scan") or {"total_devices": 0, "devices": []}


@app.get("/api/device/{uid}/info")
async def api_device_info(uid: str):
    if _datasource_manager:
        info = _dsm_get_info(uid)
        if not info:
            raise HTTPException(status_code=404, detail=f"Device {uid} not found")
        return info
    data = await _proxy_get(f"/device/{uid}/info")
    if data is None:
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")
    return data


@app.get("/api/device/{uid}/telemetry")
async def api_telemetry(uid: str):
    if _datasource_manager:
        return _dsm_get_telemetry(uid) or {"error": "no data"}
    return await _proxy_get(f"/device/{uid}/telemetry") or {"error": "no data"}


@app.get("/api/device/{uid}/telemetry/{sensor}")
async def api_sensor(uid: str, sensor: str):
    if _datasource_manager:
        telem = _dsm_get_telemetry(uid)
        if sensor not in telem:
            raise HTTPException(status_code=404, detail=f"Sensor {sensor} not found")
        return {sensor: telem[sensor]}
    return await _proxy_get(f"/device/{uid}/telemetry/{sensor}") or {"error": "no data"}


@app.get("/api/device/{uid}/history")
async def api_history(uid: str, limit: int = 100):
    if _datasource_manager:
        return {"count": 0, "data": [], "total_available": 0}
    return await _proxy_get(f"/device/{uid}/history", limit=limit) or {"count": 0, "data": []}


@app.get("/api/device/{uid}/sensors")
async def api_sensors(uid: str):
    if _datasource_manager:
        telem = _dsm_get_telemetry(uid)
        return [k for k in telem if k.lower() != "timestamp"]
    return await _proxy_get(f"/device/{uid}/sensors") or []


@app.get("/api/device/{uid}/status")
async def api_status(uid: str):
    if _datasource_manager:
        devices = {d["uid"]: d for d in _dsm_list_devices()}
        if uid not in devices:
            raise HTTPException(status_code=404, detail=f"Device {uid} not found")
        return {"uid": uid, "connected": True, **devices[uid]}
    data = await _proxy_get(f"/device/{uid}/status")
    if data is None:
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")
    return data


@app.websocket("/api/device/{uid}/stream")
async def ws_stream(uid: str, ws: WebSocket):
    await ws.accept()
    logger.info(f"[{uid}] WebSocket client connected")
    source = _args.source if _args else "direct"

    if source == "fastapi":
        api_url = getattr(_args, "api_url", "http://127.0.0.1:8000")
        ws_url  = api_url.replace("http://", "ws://") + f"/device/{uid}/stream"
        try:
            import websockets
            import websockets.exceptions
            async with websockets.connect(ws_url) as remote:
                async def fwd_to_client():
                    try:
                        async for msg in remote:
                            await ws.send_text(msg)
                    except (websockets.exceptions.ConnectionClosedError,
                            websockets.exceptions.ConnectionClosedOK,
                            WebSocketDisconnect):
                        pass

                async def fwd_to_remote():
                    try:
                        while True:
                            await remote.send(await ws.receive_text())
                    except (WebSocketDisconnect,
                            websockets.exceptions.ConnectionClosedError,
                            websockets.exceptions.ConnectionClosedOK):
                        pass

                done, pending = await asyncio.wait(
                    [asyncio.create_task(fwd_to_client()),
                     asyncio.create_task(fwd_to_remote())],
                    return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
        except ImportError:
            logger.error("websockets not installed — run: pip install websockets")
            await ws.close(code=1011)
        except (websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK):
            pass  # Remote closed — normal on shutdown
        except Exception as e:
            logger.debug(f"[{uid}] WS proxy closed: {e}")
    else:
        import json
        try:
            while True:
                if uid == "all":
                    payload = {}
                    for d in _dsm_list_devices():
                        payload.update(_dsm_get_telemetry(d["uid"]))
                else:
                    payload = _dsm_get_telemetry(uid)
                if payload:
                    await ws.send_text(json.dumps(payload))
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"[{uid}] WS push error: {e}")
        finally:
            logger.info(f"[{uid}] WebSocket closed")


@app.get("/favicon.ico")
async def favicon():
    p = Path(__file__).parent / "favicon.ico"
    if p.exists():
        return FileResponse(p)
    raise HTTPException(status_code=404)


def run_xeneon(args=None):
    global _args
    if args is None:
        args = types.SimpleNamespace(source="direct", api_url="http://127.0.0.1:8000",
                                     api_port=8000, mqtt_broker="localhost", mqtt_port=1883, interval=1.0)
    _args = args
    import uvicorn
    print("Starting Xeneon Dashboard...")
    print("Dashboard: http://localhost:8001/xeneon/dashboard")
    print("Press Ctrl+C to stop")
    print("-" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")


if __name__ == "__main__":
    run_xeneon()