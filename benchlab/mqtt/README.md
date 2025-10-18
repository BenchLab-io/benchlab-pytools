# BENCHLAB MQTT Publisher

## Overview

The MQTT Publisher module provides real-time telemetry from all connected Benchlab devices to an MQTT broker.  

It automatically:

- Detects connected devices via serial ports.
- Reads sensor data from each device.
- Translates sensor data into structured JSON payloads.
- Publishes telemetry and device info to a configured MQTT broker.
- Supports multiple transport options (TCP, TLS, WebSockets) and authentication.
- Handles graceful shutdown and logging.

Designed for integration with dashboards, monitoring platforms, or other MQTT consumers.

---

## Features

| Feature | Description |
|---------|-------------|
| Automatic device discovery | Uses `get_fleet_info()` to list all connected devices. |
| Sensor reading & translation | Reads raw sensor data and converts it into JSON-ready format via `translate_sensor_struct()`. |
| MQTT publishing | Publishes telemetry and device info on separate topics for each device. |
| Multi-device support | Runs each device in its own thread for parallel publishing. |
| TLS / WebSocket support | Supports secure and custom MQTT transports. |
| Graceful shutdown | Stops all threads and disconnects cleanly on Ctrl+C. |
| Structured logging | Logs events and errors in JSON format or standard stdout. |

---

## Installation

Install the required dependencies for the MQTT module:

```
pip install -r requirements_mqtt.txt
```

Dependencies include:

```
paho-mqtt
pyserial
benchlab core modules
```

---

## Configuration

Configure the MQTT publisher using **environment variables**.

### Example

```
export MQTT_BROKER=localhost
export MQTT_PORT=1883
export MQTT_TRANSPORT=tcp
export MQTT_USERNAME=user
export MQTT_PASSWORD=secret
export MQTT_PROTOCOL=MQTTv311
export MQTT_QOS=0
export MQTT_PATH=/mqtt
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_BROKER` | `localhost` | Hostname or IP of the MQTT broker |
| `MQTT_PORT` | `1883` | Port of the broker |
| `MQTT_TRANSPORT` | `tcp` | Transport protocol (`tcp` or `websockets`) |
| `MQTT_USERNAME` | None | MQTT username if authentication is required |
| `MQTT_PASSWORD` | None | MQTT password |
| `MQTT_PROTOCOL` | `MQTTv311` | MQTT protocol version |
| `MQTT_QOS` | `0` | Quality of Service (0, 1, or 2) |
| `MQTT_PATH` | None | WebSocket path (if transport is `websockets`) |

---

## Usage

### Run MQTT Mode

```
python benchlab.py -mqtt
```

Behavior:

- Detects all connected Benchlab devices.
- Starts a separate thread for each device.
- Publishes telemetry continuously until interrupted (`Ctrl+C`).

### MQTT Topics

#### Device Info

```
clients/client_uuid/links/balena_uuid/benchlabs/<device_uid>/info
```

Payload:

```
{
  "uid": "<device_uid>",
  "com_port": "<serial_port>",
  "firmware": "<firmware_version>"
}
```

#### Telemetry

```
clients/client_uuid/links/balena_uuid/benchlabs/<device_uid>/telemetry
```

Payload:

```
{
  "timestamp": 1234567890.123,
  "sensor1": 42.0,
  "sensor2": 3.14,
  ...
}
```

---

## Developer Notes

### Threading

- Each device runs in its own thread (`device_thread`).
- Telemetry publishes at a configurable interval (`publish_interval`).
- Periodic logging of all connected devices runs in a separate thread (`log_connected_devices_periodically`).

### Graceful Shutdown

- Controlled with the global `stop_event` (`threading.Event`).
- `Ctrl+C` triggers cleanup:
  - All device threads join.
  - Serial connections close.
  - MQTT clients disconnect and stop loop.

### Logging

- Supports JSON and plain logging via `JsonFormatter`.
- Logs contain timestamps, log level, messages, and optional exception info.

### Error Handling

- Lost serial connection: retries every second.  
- MQTT publish errors: logged with reason codes.  
- Sensor translation errors: skipped with errors logged.

---

## Extending the Module

1. **Add telemetry fields:** Extend `translate_sensor_struct()` in `benchlab.core.sensor_translation`.  
2. **Custom topics:** Modify `topic_info` and `topic_telemetry` in `device_thread`.  
3. **Additional MQTT features:** Implement `on_message` or `subscribe` callbacks if required.

---

## References

- [Paho-MQTT Python client](https://pypi.org/project/paho-mqtt/)  
- [Benchlab core modules](https://github.com/<your-org>/benchlab/tree/main/benchlab/core)
