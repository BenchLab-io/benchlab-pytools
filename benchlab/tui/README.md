# BENCHLAB Enhanced TUI

## Overview

The BENCHLAB Enhanced TUI provides a modern, feature-rich curses-based interface for real-time telemetry monitoring of BENCHLAB devices. This enhanced version includes significant improvements over the original implementation with better visuals, enhanced functionality, and improved user experience.

## 🚀 Key Features

### **Visual Enhancements**
- **Rich Color Scheme**: Color-coded telemetry data for better readability
- **Progress Bars**: Visual indicators for power, temperature, voltage, and fan duty cycles
- **Status Indicators**: Clear visual feedback for connection status and warnings
- **Enhanced Layout**: Better spacing, alignment, and information hierarchy

### **Advanced Telemetry Features**
- **Real-time Statistics**: Min/max/average values for power and temperature
- **Efficiency Monitoring**: System efficiency calculations and display
- **Thermal Status**: Automatic thermal status classification (NORMAL/WARNING/CRITICAL)
- **Voltage Status**: Color-coded voltage status indicators
- **Fan Statistics**: Average duty cycles and RPM monitoring

### **Enhanced Navigation & Interaction**
- **Multiple Navigation Methods**: Vim-style (hjkl) and arrow key navigation
- **Help System**: Built-in help modal with keybindings reference
- **Statistics Reset**: Quick reset of min/max statistics
- **Device Management**: Enhanced fleet management with connection status

### **Robustness & Error Handling**
- **Better Connection Recovery**: More graceful handling of disconnections
- **Enhanced Error Messages**: Clear error indicators and status messages
- **Connection Uptime**: Real-time connection duration tracking
- **Device Statistics**: Comprehensive device connection and performance stats

## 📊 Telemetry Tabs

### **Fleet Tab (Tab 0)**
- Device list with connection status indicators
- Active device highlighting
- Connection uptime display
- Device selection and management

### **Device Tab (Tab 1)**
- Connection details and port information
- Device identification (Vendor ID, Product ID, UID)
- Firmware version display
- Configuration status (Fan Switch, RGB Switch, RGB Ext)

### **Power Tab (Tab 2)**
- Real-time power consumption with progress bars
- Individual component power monitoring (SYS, CPU, GPU, MB)
- Efficiency calculations and display
- Min/max power statistics
- Power progress bars with color coding

### **Voltage Tab (Tab 3)**
- Vdd and Vref voltage monitoring with status
- VIN channel voltage display with progress bars
- Voltage status indicators (OK/LOW/HIGH)
- Average voltage calculations
- Color-coded voltage readings

### **Temperature Tab (Tab 4)**
- Chip and ambient temperature monitoring
- Temperature progress bars with thermal status
- Multiple temperature sensor readings
- Humidity monitoring
- Thermal status classification (NORMAL/WARNING/CRITICAL)
- Min/max temperature statistics

### **Fans Tab (Tab 5)**
- Fan duty cycle monitoring with progress bars
- RPM readings for each fan
- Fan enable/disable status
- External fan duty monitoring
- Fan statistics (average duty, max RPM, active count)

## 🎮 Keyboard Shortcuts

### **Navigation**
- `h`, `k`, `←`, `↑` - Move to previous tab
- `l`, `j`, `→`, `↓` - Move to next tab
- `q`, `Q` - Quit application
- `?` - Show help

### **Fleet Tab Specific**
- `↑`, `↓` - Navigate device list
- `Enter` - Select active device

### **Global Commands**
- `r`, `R` - Reset min/max statistics

## 🎨 Color Coding

- **Green**: Normal operation / Connected / OK status
- **Red**: Warnings / Errors / Disconnected / Critical status
- **Yellow**: High values / Caution / Warning status
- **Blue**: Information / Voltage readings
- **Cyan**: Temperature readings
- **White**: Headers and active elements

## 📈 Enhanced Features

### **Progress Bars**
All major telemetry values now include visual progress bars:
- Power consumption (0-500W scale)
- Temperature readings (0-100°C scale)
- Voltage levels (0-12V scale for VIN channels)
- Fan duty cycles (0-100% scale)

### **Statistics Tracking**
- **Power Statistics**: Track minimum and maximum power consumption
- **Temperature Statistics**: Monitor thermal performance over time
- **Connection Statistics**: Track uptime and connection history
- **Reset Functionality**: Quickly reset statistics with 'r' key

### **Status Indicators**
- **Connection Status**: Real-time connection monitoring
- **Thermal Status**: Automatic thermal classification
- **Voltage Status**: Per-channel voltage health monitoring
- **Fan Status**: Individual fan health and operation status

### **Enhanced Error Handling**
- Graceful handling of device disconnections
- Clear error messages and status indicators
- Automatic reconnection attempts
- Robust serial communication error recovery

## 🛠️ Installation

Ensure your environment has required dependencies:

```bash
pip install -r requirements.txt
```

Dependencies include:
- `python3-curses` (Linux/Unix)
- `benchlab core modules`

## 🚀 Usage

### Launch the Enhanced TUI

```bash
python benchlab.py -tui
```

### Configuration

The TUI supports the same configuration options as the original:
- `--interval` - Telemetry refresh interval
- Environment variables for serial connection settings

## 📋 Requirements

- **Minimum Terminal Size**: 100x35 characters
- **Python 3.6+** with curses support
- **BENCHLAB devices** connected via USB
- **Serial port access** permissions (Linux: `dialout` group)

## 🔧 Technical Details

### **Enhanced Data Storage**
- **Telemetry History**: 100-point rolling history per device
- **Device Statistics**: Comprehensive performance tracking
- **Connection Tracking**: Uptime and disconnect monitoring
- **Memory Efficient**: Automatic cleanup and bounded storage

### **Performance Optimizations**
- **Efficient Redrawing**: Minimal screen updates
- **Smart Refresh**: Different refresh rates per tab
- **Memory Management**: Prevents memory leaks in long sessions
- **Resource Monitoring**: TUI resource usage tracking

### **Cross-Platform Support**
- **Windows**: Full COM port support
- **Linux**: ttyUSB/ttyACM/ttyS port detection
- **macOS**: Serial port compatibility
- **Terminal Compatibility**: Works with most terminal emulators

## 🎯 Comparison with Original TUI

| Feature | Original | Enhanced |
|---------|----------|----------|
| Color Scheme | Basic | Rich, semantic colors |
| Progress Bars | None | Comprehensive visual indicators |
| Statistics | None | Min/max/average tracking |
| Help System | None | Built-in help modal |
| Error Handling | Basic | Enhanced with clear indicators |
| Navigation | Arrow keys only | Vim-style + arrow keys |
| Status Indicators | Text only | Color-coded + text |
| Connection Monitoring | Basic | Uptime + status tracking |
| Statistics Reset | None | Quick reset functionality |
| Visual Layout | Basic | Enhanced spacing and hierarchy |

## 🐛 Troubleshooting

### **Common Issues**

1. **Terminal too small**
   - Resize terminal to at least 100x35 characters
   - Message will appear if terminal is too small

2. **No devices found**
   - Check USB connections
   - Verify device firmware
   - Check serial port permissions (Linux: `sudo usermod -a -G dialout $USER`)

3. **Permission errors**
   - On Linux, add user to `dialout` group
   - Restart terminal or reboot after group changes

4. **Connection issues**
   - Check device power and connections
   - Verify device is in correct mode
   - Check for conflicting applications using the port

### **Debug Mode**
Enable debug logging for troubleshooting:
```bash
LOG_LEVEL=DEBUG python benchlab.py -tui
```

## 🔄 Development

### **Adding New Features**
1. **New Telemetry Fields**: Update `translate_sensor_struct` to expose new metrics
2. **Custom Tabs**: Add to `TAB_NAMES` and implement tab logic
3. **Enhanced Visuals**: Leverage curses color pairs and formatting
4. **Statistics**: Extend `device_stats` structure

### **Customization**
- **Color Schemes**: Modify `curses.init_pair()` calls
- **Progress Bar Scales**: Adjust scaling factors in tab implementations
- **Status Thresholds**: Update temperature and voltage status logic
- **Keyboard Shortcuts**: Add to key handling section

## 📚 References

- [BENCHLAB core modules](https://github.com/<your-org>/benchlab/tree/main/benchlab/core)
- [Python curses documentation](https://docs.python.org/3/library/curses.html)
- [Original TUI documentation](README.md)

## 🤝 Contributing

Enhancements and improvements are welcome! Please follow the existing code patterns and ensure backward compatibility where possible.

## 📄 License

This project is part of the BenchLab suite. See the main project license for details.