# BENCHLAB Device Configuration Tool

A command-line tool for importing and exporting BENCHLAB device configuration via JSON files. Supports both direct serial (pycore) and Windows named pipe (BL_Service) data sources.

## Features

- **Export Configuration**: Read current device settings and save to JSON
- **Import Configuration**: Apply JSON configuration to devices
- **Multi-Source Support**: Works with both direct serial and named pipe connections
- **Interactive Mode**: Simple interface for loading JSON configs
- **Validation**: Schema validation with detailed error messages
- **Batch Operations**: Configure multiple devices from a single JSON file

## Installation

The config tool is included in BENCHLAB PyTools. Ensure you have the required dependencies:

```bash
pip install -r requirements.txt
```

For direct serial support, you also need:
```bash
pip install benchlab-pycore
```

For named pipe support (Windows only), you need:
```bash
pip install pywin32
```

## Usage

### Interactive Mode

Simply run the tool without arguments to enter interactive mode:

```bash
python -m benchlab -config
```

This will:
1. List available devices
2. Ask whether you want to import or export configuration
3. For import: prompt for JSON file, confirm before applying, and ask whether to save to flash
4. For export: prompt for output filename and save current device config

### Command-Line Mode

#### List Devices

```bash
# List devices via direct serial
python -m benchlab -config --list

# List devices via named pipe (Windows only)
python -m benchlab -config --list --source named_pipe
```

#### Export Configuration

```bash
# Export from first available device
python -m benchlab -config --export my_config.json

# Export from specific device
python -m benchlab -config --export my_config.json --device COM4

# Export via named pipe
python -m benchlab -config --export my_config.json --source named_pipe
```

#### Import Configuration

```bash
# Import configuration - shows a diff of what would actually change on the
# device (current value -> new value, only changed fields), then asks for
# confirmation before applying
python -m benchlab -config --import my_config.json

# Preview changes without applying anything (still connects to the device
# and reads its current config to compute the diff)
python -m benchlab -config --import my_config.json --dry-run

# Apply without the confirmation prompt (diff is still printed) - for
# scripts/automation
python -m benchlab -config --import my_config.json --yes

# Import via named pipe
python -m benchlab -config --import my_config.json --source named_pipe
```

## JSON Configuration Format

### Basic Structure

```json
{
  "version": "1.0",
  "description": "Configuration description",
  "devices": [
    {
      "selector": {
        "type": "any",
        "value": null
      },
      "deviceName": "BENCHLAB_MAIN",
      "fanProfiles": [...],
      "rgbProfiles": [...],
      "saveToFlash": true
    }
  ]
}
```

### Device Selector Types

- **`any`**: Select first available device
- **`guid`**: Match by device UID/GUID (recommended - portable across systems)
- **`port`**: Match by serial port (e.g., "COM4" - not portable)
- **`productId`**: Match by product ID (0x10 or 0x11)
- **`pipeName`**: Match by named pipe name (named_pipe source only)

**Note:** When exporting, the tool automatically uses `guid` selector with the device UID for portability. This allows you to export configuration on one system and import it on another, and the tool will find the same device regardless of which COM port it's connected to.

### Fan Configuration

```json
{
  "fanId": 0,
  "FanMode": 0,
  "TempSource": 0,
  "Temp": [300, 600],
  "Duty": [30, 80],
  "RampStep": 5,
  "FixedDuty": 50,
  "MinDuty": 20,
  "MaxDuty": 100,
  "FanStop": 0
}
```

**Fan Modes:**
- `0` = Auto (temperature-based curve)
- `1` = Fixed (constant duty cycle)
- `2` = External (controlled by external signal)

**Temperature Source:**
- `0` = Auto
- `1-4` = TS_1 through TS_4
- `5` = Ambient

**Temperature Values:** In tenths of °C (e.g., 300 = 30.0°C)

### RGB Configuration

```json
{
  "profileId": 0,
  "Mode": 9,
  "Red": 255,
  "Green": 0,
  "Blue": 0,
  "Direction": 0,
  "Speed": 50
}
```

**RGB Modes:**
- `0` = Rainbow Cycle
- `1` = Rainbow Wave
- `2` = Color Shift
- `3` = Color Pulse
- `4` = Color Wave
- `5` = Breathing
- `6` = Spectrum Cycle
- `7` = Alternating
- `8` = Candle
- `9` = Single Color

## Examples

Example configurations are provided in `benchlab/config/examples/`:

### Simple Fan Control

Set all fans to 50% fixed speed:

```bash
python -m benchlab -config --import benchlab/config/examples/simple_fan.json
```

### RGB Lighting

Set RGB to solid blue:

```bash
python -m benchlab -config --import benchlab/config/examples/rgb_lighting.json
```

### Complete Configuration

Full device setup with fan curves, RGB, and device name:

```bash
python -m benchlab -config --import benchlab/config/examples/complete_config.json
```

## Data Sources

### Direct Serial (pycore)

Uses direct USB-to-serial communication via the `benchlab-pycore` library. This is the default source and works on all platforms.

**Advantages:**
- Cross-platform (Windows, Linux, macOS)
- No additional services required
- Direct hardware access

**Limitations:**
- Exclusive port access (only one tool can connect at a time)
- Requires pycore library

### Named Pipe (Windows Service)

Uses the Windows BL_Service named pipe interface for communication.

**Advantages:**
- Multiple tools can access device simultaneously
- Integrates with Windows service
- No direct serial port management

**Limitations:**
- Windows only
- Requires BL_Service to be running
- Requires pywin32 library

## Configuration Profiles

### Profile IDs

- **Fan Profiles**: 0-2 (3 profiles available)
- **RGB Profiles**: 0-1 (2 profiles available)

### Fan IDs

- **Fan Channels**: 0-8 (9 fans available)

## Saving to Flash

Set `"saveToFlash": true` in the device configuration to persist settings to device flash memory. Without this, changes are only applied to RAM and will be lost on device reset.

In **interactive mode**, you will be prompted at runtime whether to save to flash, which overrides the `saveToFlash` value in the JSON file. In **command-line mode** (`--import`), the `saveToFlash` value from the JSON file is used directly.

## Error Handling

The tool provides detailed error messages for:
- Invalid JSON syntax
- Schema validation failures
- Device connection errors
- Configuration write failures

Use `--dry-run` to preview exactly what would change on the device (connects and reads current config to compute a diff) without applying anything. Use `--yes` to skip the confirmation prompt while still showing the diff, for scripted/automated use.

## Troubleshooting

### No Devices Found

**Direct Serial:**
- Check USB connection
- Verify device is powered on
- Check serial port permissions (Linux: add user to `dialout` group)

**Named Pipe:**
- Ensure BL_Service is running
- Check Windows service status
- Verify device is connected to service

### Import Fails

- Use `--dry-run` to validate JSON syntax
- Check device selector matches available devices
- Verify all required fields are present
- Check value ranges (duty 0-100%, temp in 0.1°C units)

### Permission Errors

**Linux:**
```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

**Windows:**
- Run as Administrator if needed
- Check BL_Service permissions

## API Reference

For programmatic use, import the configuration manager:

```python
from benchlab.config.config_manager import ConfigManager

# Create manager
manager = ConfigManager(source='direct')

# Discover devices
devices = manager.discover_devices()

# Export configuration
manager.export_config('COM4', 'output.json')

# Import configuration
manager.import_config('input.json', dry_run=False)
```

## See Also

- [BENCHLAB PyTools Documentation](../../README.md) - Main PyTools documentation
- [benchlab-pycore](https://github.com/BenchLab-io/benchlab-pycore) - PyCore library used for direct serial configuration I/O

## License

Part of BENCHLAB PyTools. See main project license for details.
