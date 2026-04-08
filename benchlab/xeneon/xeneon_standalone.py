#!/usr/bin/env python3
"""
Xeneon Dashboard Standalone Server

A standalone FastAPI server for the Xeneon dashboard that doesn't require device connections.
This provides a web-based interface for testing and demonstration purposes.
"""

import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("benchlab.xeneon.standalone")

# Create standalone FastAPI app (not extending telemetry API)
app = FastAPI(
    title="Xeneon Dashboard",
    description="Standalone web dashboard for BenchLab telemetry",
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

@app.on_event("startup")
async def startup_event():
    """Custom startup for standalone dashboard"""
    logger.info("Xeneon Standalone Dashboard starting up...")
    logger.info("Dashboard will be available at: http://localhost:8001/xeneon/dashboard")
    logger.info("Iframe URL: http://localhost:8001/xeneon")
    logger.info("No device connections required - for testing and demonstration")

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
        "service": "xeneon-dashboard-standalone",
        "version": "0.1.0",
        "message": "Standalone dashboard running without device connections"
    }

@app.get("/config")
async def get_dashboard_config():
    """Get dashboard configuration"""
    return {
        "title": "Xeneon Dashboard (Standalone)",
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
        ],
        "standalone_mode": True,
        "note": "Running in standalone mode - no device connections required"
    }

@app.get("/api/devices")
async def get_devices():
    """Mock device list for standalone mode"""
    return {
        "devices": [],
        "message": "No devices connected (standalone mode)",
        "timestamp": "2026-03-30T12:00:00Z"
    }

@app.get("/api/device/{uid}/info")
async def get_device_info(uid: str):
    """Mock device info for standalone mode"""
    return {
        "UID": uid,
        "port": "N/A",
        "FwVersion": 0,
        "FanSwitchStatus": "N/A",
        "RGBSwitchStatus": "N/A",
        "RGBExtStatus": "N/A",
        "message": "Device not connected (standalone mode)"
    }

@app.get("/api/device/{uid}/telemetry")
async def get_telemetry(uid: str):
    """Mock telemetry data for standalone mode"""
    return {
        "device_id": uid,
        "timestamp": "2026-03-30T12:00:00Z",
        "status": "disconnected",
        "message": "No telemetry available (standalone mode)",
        "data": {}
    }

@app.post("/api/scan")
async def scan_for_devices():
    """Mock device scan for standalone mode.
    
    Returns empty device list since standalone mode doesn't connect to real devices.
    This endpoint exists for compatibility with the dashboard JavaScript.
    """
    from datetime import datetime
    return {
        "scan_time": datetime.utcnow().isoformat(),
        "total_devices": 0,
        "new_devices": [],
        "disconnected_devices": [],
        "devices": [],
        "message": "Scan complete (standalone mode - no real devices)"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "benchlab.xeneon.xeneon_standalone:app",
        host="127.0.0.1",
        port=8001,
        log_level="info"
    )