# BenchLab FastAPI Telemetry Server

A lightweight, cross-platform REST API server for BenchLab telemetry data collection and monitoring.

## Features

### ✨ Core Improvements
- **Cross-platform device discovery** - Automatically detects BenchLab devices on Windows, Linux, and macOS
- **Robust error handling** - Comprehensive error handling and logging for production use
- **Configuration management** - Environment-based configuration with validation
- **Health monitoring** - Built-in health check and status endpoints
- **CORS support** - Ready for web client integration

### 🚀 Performance Enhancements
- **Efficient data streaming** - WebSocket support for real-time telemetry updates
- **Configurable polling** - Adjustable sensor read intervals (minimum 0.1s)
- **History management** - Configurable history buffer with pagination
- **Connection pooling** - Optimized serial connection management

### 🛡️ Reliability Features
- **Graceful shutdown** - Proper cleanup of threads and connections
- **Device reconnection** - Automatic handling of device disconnects/reconnects
- **Validation** - Input validation and configuration checks
- **Logging** - Structured logging with configurable levels

## Quick Start

### 1. Installation
```bash
cd benchlab/fastapi
pip install -r requirements.txt
```

### 2. Configuration
Copy the example configuration and customize as needed:
```bash
cp .env.example .env
```

Edit `.env` to configure:
- Server host and port
- Polling intervals
- History buffer size
- Log levels

### 3. Run the Server
```bash
python -m benchlab.fastapi.telemetry_api
```

The server will start on `http://localhost:8000` by default.

## API Endpoints

### Device Management
- `GET /devices` - List all connected devices
- `GET /device/{uid}/info` - Get device information
- `GET /device/{uid}/status` - Get detailed device status

### Telemetry Data
- `GET /device/{uid}/telemetry` - Get latest telemetry
- `GET /device/{uid}/telemetry/{sensor}` - Get specific sensor data
- `GET /device/{uid}/history` - Get telemetry history (with pagination)
- `GET /device/{uid}/sensors` - List available sensors

### Real-time Streaming
- `WebSocket /device/{uid}/stream` - Real-time telemetry updates

### Monitoring
- `GET /health` - Basic health check
- `GET /status` - Detailed server status

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Server bind address |
| `API_PORT` | `8000` | Server port (1-65535) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `POLL_INTERVAL` | `1.0` | Sensor read interval in seconds (min 0.1) |
| `HISTORY_LENGTH` | `10` | Number of history entries per device |
| `MAX_HISTORY_LIMIT` | `1000` | Max history limit for API requests |
| `SCAN_INTERVAL` | `30` | Device discovery scan interval in seconds |

## Cross-Platform Support

The server automatically detects and works with BenchLab devices across different platforms:

- **Windows**: COM1-COM9 ports
- **Linux**: `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*` ports
- **macOS**: `/dev/tty.*` ports

## Testing

Run the test suite to verify the server functionality:
```bash
python test_server.py
```

This tests:
- Server startup and configuration
- API endpoint registration
- Cross-platform device detection
- Error handling and validation

## Usage Examples

### Get Device List
```bash
curl http://localhost:8000/devices
```

### Get Latest Telemetry
```bash
curl http://localhost:8000/device/ABC123/telemetry
```

### Get Sensor History
```bash
curl "http://localhost:8000/device/ABC123/history?limit=50"
```

### Real-time Streaming (WebSocket)
```javascript
const ws = new WebSocket('ws://localhost:8000/device/ABC123/stream');
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Telemetry update:', data);
};
```

## Production Deployment

### Docker Support
The server is designed for containerization:
```dockerfile
FROM python:3.9-slim
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "-m", "benchlab.fastapi.telemetry_api"]
```

### Systemd Service (Linux)
Create `/etc/systemd/system/benchlab-api.service`:
```ini
[Unit]
Description=BenchLab Telemetry API
After=network.target

[Service]
Type=simple
User=benchlab
WorkingDirectory=/opt/benchlab
ExecStart=/usr/bin/python3 -m benchlab.fastapi.telemetry_api
Restart=always

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

### Common Issues

1. **No devices found**
   - Check USB connections
   - Verify device firmware
   - Check serial port permissions

2. **Permission errors**
   - On Linux, add user to `dialout` group: `sudo usermod -a -G dialout $USER`
   - Restart terminal or reboot after group changes

3. **Port binding errors**
   - Check if port is already in use
   - Try a different port in configuration

### Logging
Enable debug logging for troubleshooting:
```bash
LOG_LEVEL=DEBUG python -m benchlab.fastapi.telemetry_api
```

## Development

### Adding New Endpoints
1. Add the endpoint function in `telemetry_api.py`
2. Use FastAPI decorators (`@app.get`, `@app.post`, etc.)
3. Add proper error handling and validation
4. Update this README with new endpoints

### Testing Changes
1. Run the test suite: `python test_server.py`
2. Test manually with curl or a REST client
3. Verify WebSocket functionality

## License

This project is part of the BenchLab suite. See the main project license for details.