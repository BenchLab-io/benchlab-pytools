"""
Xeneon Dashboard Main Module

Web dashboard server that automatically launches the FastAPI telemetry server
and proxies API calls to it. This server does NOT open any serial ports directly -
it starts the telemetry server in a background thread which handles all serial communication.
"""

import logging
import threading
import time
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import httpx

logger = logging.getLogger("benchlab.xeneon")

# Configuration
TELEMETRY_API_HOST = "127.0.0.1"
TELEMETRY_API_PORT = 8000
TELEMETRY_API_URL = f"http://{TELEMETRY_API_HOST}:{TELEMETRY_API_PORT}"

# Create standalone FastAPI app (NOT extending telemetry_api to avoid serial port access)
app = FastAPI(
    title="Xeneon Dashboard",
    description="Web dashboard for BenchLab telemetry",
    version="0.1.0"
)

# Add CORS middleware for iframe embedding
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow embedding from any domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Mount static files for the dashboard
static_dir = Path(__file__).parent / "static"
app.mount("/xeneon/static", StaticFiles(directory=static_dir), name="static")

# Setup templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

# HTTP client for proxying to telemetry API (XENEON-1: will be initialized in startup)
http_client = None

# Background telemetry server
telemetry_server = None
telemetry_thread = None


def run_telemetry_server():
    """Run the FastAPI telemetry server in a background thread"""
    import uvicorn
    from benchlab.fastapi.telemetry_api import app as telemetry_app
    
    config = uvicorn.Config(
        telemetry_app,
        host=TELEMETRY_API_HOST,
        port=TELEMETRY_API_PORT,
        log_level="warning",  # Suppress verbose logs from telemetry server
        use_colors=False
    )
    server = uvicorn.Server(config)
    
    # Run the server in the thread's event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


def wait_for_telemetry_server(timeout=10):
    """Wait for the telemetry server to become available (XENEON-1: use sync httpx)"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = httpx.get(f"{TELEMETRY_API_URL}/health", timeout=1)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


@app.on_event("startup")
async def startup_event():
    """Start the telemetry server and log startup information.
    
    XENEON-1: Initialize http_client here to avoid module-level initialization issues.
    """
    global telemetry_thread, http_client
    
    # Initialize the async HTTP client (XENEON-1)
    http_client = httpx.AsyncClient(base_url=TELEMETRY_API_URL, timeout=30.0)
    
    logger.info("Xeneon Dashboard starting up...")
    logger.info(f"Starting embedded FastAPI telemetry server on {TELEMETRY_API_URL}...")
    
    # Start the telemetry server in a background thread
    telemetry_thread = threading.Thread(target=run_telemetry_server, daemon=True)
    telemetry_thread.start()
    
    # Wait for the telemetry server to be ready
    if wait_for_telemetry_server():
        logger.info(f"Telemetry server is ready at {TELEMETRY_API_URL}")
    else:
        logger.warning(f"Telemetry server did not start within timeout. API calls may fail.")
    
    logger.info("Dashboard will be available at: http://localhost:8001/xeneon/dashboard")
    logger.info("Iframe URL: http://localhost:8001/xeneon")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up HTTP client on shutdown"""
    await http_client.aclose()


@app.get("/", response_class=HTMLResponse)
async def dashboard_root():
    """Redirect root to dashboard"""
    return HTMLResponse(content="""
    <html>
        <head>
            <meta http-equiv="refresh" content="0; url=/xeneon/dashboard">
        </head>
        <body>
            <p>Redirecting to dashboard...</p>
        </body>
    </html>
    """)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard page"""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/xeneon", response_class=HTMLResponse)
async def dashboard_embed(request: Request):
    """Serve the dashboard for iframe embedding"""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/health")
async def health_check():
    """Health check endpoint for the dashboard service"""
    return {
        "status": "healthy",
        "service": "xeneon-dashboard",
        "version": "0.1.0"
    }


@app.get("/config")
async def get_dashboard_config():
    """Get dashboard configuration"""
    return {
        "title": "Xeneon Dashboard",
        "version": "0.1.0",
        "refresh_interval": 1000,  # 1 second
        "theme": {
            "primary": "#2ecc71",  # Green
            "danger": "#e74c3c",   # Red
            "warning": "#f1c40f",  # Yellow
            "info": "#3498db",     # Cyan
            "muted": "#34495e"     # Blue
        },
        "tabs": [
            {"id": "fleet", "name": "Fleet", "icon": "📡"},
            {"id": "device", "name": "Device", "icon": "🖥️"},
            {"id": "system", "name": "System", "icon": "⚡"},
            {"id": "voltage", "name": "Voltage", "icon": "🔋"},
            {"id": "temperature", "name": "Temperature", "icon": "🌡️"},
            {"id": "fans", "name": "Fans", "icon": "🌀"}
        ]
    }


# --- API Proxy Routes ---
# These routes forward requests to the FastAPI telemetry server

@app.get("/api/devices")
async def api_list_devices():
    """Proxy to telemetry server's /devices endpoint"""
    try:
        response = await http_client.get("/devices")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/devices: {e}")
        return []


@app.post("/api/scan")
async def api_scan_for_devices():
    """Proxy to telemetry server's /scan endpoint"""
    try:
        response = await http_client.post("/scan")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/scan: {e}")
        return {
            "scan_time": "",
            "total_devices": 0,
            "new_devices": [],
            "disconnected_devices": [],
            "devices": [],
            "error": str(e)
        }


@app.get("/api/device/{uid}/info")
async def api_get_device_info(uid: str):
    """Proxy to telemetry server's /device/{uid}/info endpoint"""
    try:
        response = await http_client.get(f"/device/{uid}/info")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/device/{uid}/info: {e}")
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")


@app.get("/api/device/{uid}/telemetry")
async def api_get_telemetry(uid: str):
    """Proxy to telemetry server's /device/{uid}/telemetry endpoint"""
    try:
        response = await http_client.get(f"/device/{uid}/telemetry")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/device/{uid}/telemetry: {e}")
        return {"error": f"Device {uid} not found"}


@app.get("/api/device/{uid}/telemetry/{sensor}")
async def api_get_sensor(uid: str, sensor: str):
    """Proxy to telemetry server's /device/{uid}/telemetry/{sensor} endpoint"""
    try:
        response = await http_client.get(f"/device/{uid}/telemetry/{sensor}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/device/{uid}/telemetry/{sensor}: {e}")
        return {"error": f"Sensor {sensor} not found"}


@app.get("/api/device/{uid}/history")
async def api_get_history(uid: str, limit: int = 100):
    """Proxy to telemetry server's /device/{uid}/history endpoint"""
    try:
        response = await http_client.get(f"/device/{uid}/history", params={"limit": limit})
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/device/{uid}/history: {e}")
        return {"error": f"Device {uid} not found"}


@app.get("/api/device/{uid}/sensors")
async def api_get_sensors(uid: str):
    """Proxy to telemetry server's /device/{uid}/sensors endpoint"""
    try:
        response = await http_client.get(f"/device/{uid}/sensors")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/device/{uid}/sensors: {e}")
        return {"error": f"Device {uid} not found"}


@app.get("/api/device/{uid}/status")
async def api_get_device_status(uid: str):
    """Proxy to telemetry server's /device/{uid}/status endpoint"""
    try:
        response = await http_client.get(f"/device/{uid}/status")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Error proxying /api/device/{uid}/status: {e}")
        raise HTTPException(status_code=404, detail=f"Device {uid} not found")


@app.websocket("/api/device/{uid}/stream")
async def api_stream_device(uid: str, ws: WebSocket):
    """Proxy WebSocket to telemetry server's /device/{uid}/stream endpoint"""
    await ws.accept()
    logger.info(f"[{uid}] WebSocket client connected to dashboard")
    
    try:
        # Connect to the telemetry server's WebSocket using httpx
        async with httpx.AsyncClient() as client:
            # Use the WebSocket endpoint on the telemetry server
            async with client.websocket_connect(f"/device/{uid}/stream") as remote_ws:
                logger.info(f"[{uid}] WebSocket proxy connected to telemetry server")
                
                async def receive_from_remote():
                    """Receive messages from remote server and forward to client"""
                    try:
                        while True:
                            data = await remote_ws.receive_text()
                            await ws.send_text(data)
                    except Exception as e:
                        logger.debug(f"[{uid}] Remote WebSocket closed: {e}")
                
                async def receive_from_client():
                    """Receive messages from client and forward to remote server"""
                    try:
                        while True:
                            data = await ws.receive_text()
                            await remote_ws.send_text(data)
                    except WebSocketDisconnect:
                        logger.info(f"[{uid}] Client disconnected")
                
                # Run both receive tasks concurrently
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(receive_from_remote()),
                        asyncio.create_task(receive_from_client())
                    ],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel pending tasks
                for task in pending:
                    task.cancel()
                
    except Exception as e:
        logger.error(f"[{uid}] WebSocket proxy error: {e}")
        try:
            await ws.close(code=1011, reason=str(e))
        except Exception:
            pass
    finally:
        logger.info(f"[{uid}] WebSocket connection closed")


@app.get("/favicon.ico")
async def favicon():
    """Serve favicon"""
    favicon_path = Path(__file__).parent / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(favicon_path)
    raise HTTPException(status_code=404, detail="Favicon not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "benchlab.xeneon.xeneon_main:app",
        host="127.0.0.1",
        port=8001,
        log_level="info"
    )