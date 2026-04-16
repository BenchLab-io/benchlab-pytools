# Xeneon Dashboard

Modern HTML dashboard for BenchLab telemetry data, designed for iframe embedding and external device integration.

## Overview

The Xeneon Dashboard provides a web-based interface that replicates the full TUI experience with real-time telemetry updates. It's optimized for iframe embedding on external devices, particularly the 2560×720 (32:9) ultrawide touch display.

## Features

### 📡 Complete TUI Replication
- **6 Full Tabs**: Fleet, Device, System, Voltage, Temperature, Fans
- **Identical Data Organization**: Same telemetry structure as the original TUI
- **TUI Color Scheme**: Professional color coding for status indication
- **Monospace Typography**: Maintains terminal aesthetic

### ⚡ Real-time Telemetry
- **WebSocket Connections**: Live updates from FastAPI backend
- **REST Fallback**: Automatic polling when WebSocket unavailable
- **Device Discovery**: Automatic fleet scanning and connection
- **Status Monitoring**: Connection status and uptime tracking

### 🖼️ Iframe Optimized
- **Responsive Design**: Works on various screen sizes
- **Ultrawide Optimized**: Perfect fit for 2560×720 displays
- **Touch Friendly**: Designed for touch interface interaction
- **Zero Dependencies**: Pure HTML/CSS/JavaScript

## Installation

The Xeneon dashboard is included as part of the BenchLab pytools package:

```bash
# Navigate to your BenchLab project
cd benchlab-pytools

# The dashboard is located at:
# benchlab/xeneon/
```

## Quick Start

### 1. Start the Dashboard Server

```bash
# Start the Xeneon dashboard server
python -m benchlab.xeneon.xeneon_main
```

The server will start on `http://localhost:8001` by default.

### 2. Access the Dashboard

- **Main Dashboard**: `http://localhost:8001/xeneon/dashboard`
- **Iframe Embedding**: `http://localhost:8001/xeneon`
- **API Endpoints**: `http://localhost:8001/api/`

### 3. Embed in External Device

```html
<iframe 
    src="http://your-server:8001/xeneon" 
    width="2560" 
    height="720" 
    frameborder="0">
</iframe>
```

## Dashboard Tabs

### 📡 Fleet Tab
- **Device Discovery**: Automatic scanning for connected BenchLab devices
- **Manual Rescan**: Click "Refresh Fleet" to scan for newly connected devices
- **Connection Management**: Connect/disconnect from devices
- **Status Overview**: Connection status and uptime display
- **Device Information**: Port, UID, and firmware version

### 🖥️ Device Tab
- **Connection Details**: Port, UID, firmware version
- **Configuration Status**: Fan switch, RGB switch, RGB ext status
- **Device Health**: Connection status and error reporting

### ⚡ System Tab
- **Power Summary**: SYS, CPU, GPU, MB power monitoring
- **Power Telemetry**: Per-rail power measurements (EPS, PCIE, HPWR)
- **Current Telemetry**: Per-rail current measurements
- **Voltage Telemetry**: Per-rail voltage measurements

### 🔋 Voltage Tab
- **Board Voltages**: Vdd, Vref monitoring
- **VIN Measurements**: VIN_0 through VIN_12 channels
- **Voltage Status**: OK/LOW/HIGH status indicators

### 🌡️ Temperature Tab
- **Chip Temperature**: Main processor temperature with warning thresholds
- **Ambient Environment**: Temperature and humidity monitoring
- **Sensor Temperatures**: Up to 4 external temperature sensors
- **Visual Indicators**: Color-coded temperature bars

### 🌀 Fans Tab
- **Fan Status**: RPM, duty cycle, and enable status for all fans
- **External Fan**: Dedicated control for external fan
- **Real-time Monitoring**: Live RPM and duty cycle updates

## Configuration

### Server Configuration

Environment variables (create `.env` file in `benchlab/xeneon/static/`):

```env
# Server Settings
API_HOST=0.0.0.0
API_PORT=8001
LOG_LEVEL=INFO

# Telemetry Settings
POLL_INTERVAL=1.0
HISTORY_LENGTH=100
SCAN_INTERVAL=30
```

### Dashboard Configuration

The dashboard can be configured via URL parameters:

```
http://localhost:8001/xeneon?tab=system
```

Available tabs: `fleet`, `device`, `system`, `voltage`, `temperature`, `fans`

## API Integration

The Xeneon dashboard leverages the existing FastAPI telemetry API:

### Device Management
- `GET /api/devices` - List all connected devices
- `GET /api/device/{uid}/info` - Get device information
- `GET /api/device/{uid}/telemetry` - Get real-time telemetry

### Telemetry Data
- `GET /api/device/{uid}/telemetry/{sensor}` - Get specific sensor data
- `GET /api/device/{uid}/history` - Get telemetry history
- `GET /api/device/{uid}/sensors` - List available sensors

### WebSocket Streaming
- `WS /api/device/{uid}/stream` - Real-time telemetry streaming

## Technical Specifications

### Display Requirements
- **Resolution**: 2560×720 (32:9) optimal
- **Touch Support**: Full touch interface compatibility
- **Refresh Rate**: 60Hz
- **Brightness**: 350 cd/m² minimum

### Browser Support
- **Modern Browsers**: Chrome, Firefox, Safari, Edge
- **WebSocket Support**: Required for real-time updates
- **CSS Grid/Flexbox**: Modern layout support
- **ES6+ JavaScript**: Modern JavaScript features

### Performance
- **Update Frequency**: 1Hz default (configurable)
- **Memory Usage**: <50MB typical
- **Network Usage**: Minimal (WebSocket + REST fallback)

## Development

### File Structure

```
benchlab/xeneon/
├── __init__.py              # Module initialization
├── xeneon_main.py           # FastAPI integration
├── static/
│   ├── css/
│   │   └── dashboard.css    # TUI-inspired styling
│   └── js/
│       └── dashboard.js     # Real-time data handling
└── templates/
    ├── dashboard.html       # Main dashboard template
    └── test_iframe.html     # Iframe testing page
```

### Customization

#### Color Scheme
Edit CSS variables in `static/css/dashboard.css`:

```css
:root {
    --color-primary: #2ecc71;    /* Green - OK/Connected */
    --color-danger: #e74c3c;     /* Red - Error/Disconnected */
    --color-warning: #f1c40f;    /* Yellow - Caution */
    --color-info: #3498db;       /* Cyan - Temperature/Fans */
    --color-muted: #34495e;      /* Blue - Voltage/Info */
}
```

#### Layout
The dashboard uses CSS Grid and Flexbox for responsive layouts. Modify the grid templates in the CSS file to adjust column layouts.

#### Telemetry Data
The JavaScript handles telemetry updates. Modify the data mapping in `dashboard.js` to customize how telemetry is displayed.

## Troubleshooting

### Common Issues

#### Dashboard Not Loading
- Check that the FastAPI telemetry server is running
- Verify the API endpoints are accessible
- Check browser console for JavaScript errors

#### WebSocket Connection Failed
- Ensure WebSocket support in your browser
- Check firewall settings for port 8001
- Verify CORS settings in the FastAPI configuration

#### No Telemetry Data
- Confirm devices are connected and powered
- Check device discovery in the Fleet tab
- Verify serial communication is working

#### Iframe Not Displaying
- Ensure the iframe URL is correct
- Check for CORS restrictions
- Verify the server is accessible from the iframe host

### Debug Mode

Enable debug logging in the browser console:

```javascript
// In browser console
window.xeneonDashboard.config.refreshInterval = 500; // Faster updates
console.log('Dashboard status:', window.xeneonDashboard);
```

## Integration Examples

### Basic Iframe Embedding

```html
<!DOCTYPE html>
<html>
<head>
    <title>External Device Dashboard</title>
    <style>
        body { margin: 0; background: black; }
        iframe { width: 100vw; height: 100vh; border: none; }
    </style>
</head>
<body>
    <iframe src="http://your-server:8001/xeneon?tab=system"></iframe>
</body>
</html>
```

### Responsive Dashboard

```html
<div style="width: 100%; height: 600px; border: 1px solid #ccc;">
    <iframe 
        src="http://your-server:8001/xeneon" 
        style="width: 100%; height: 100%; border: none;">
    </iframe>
</div>
```

### Multiple Dashboards

```html
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
    <div>
        <h3>System Overview</h3>
        <iframe src="http://your-server:8001/xeneon?tab=system"></iframe>
    </div>
    <div>
        <h3>Temperature Monitoring</h3>
        <iframe src="http://your-server:8001/xeneon?tab=temperature"></iframe>
    </div>
</div>
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is part of the BenchLab pytools package. See the main project license for details.

## Support

For issues and questions:
- Check the troubleshooting section above
- Review the browser console for errors
- Verify your FastAPI telemetry server is running
- Ensure devices are properly connected

## Changelog

### v0.1.0
- Initial release
- Complete TUI replication with 6 tabs
- Real-time WebSocket telemetry
- Iframe optimization for 2560×720 displays
- Responsive design for various screen sizes