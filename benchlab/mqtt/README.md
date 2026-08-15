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
pip install -r requirements.txt
```

Dependencies (see `requirements.txt`):

```
paho-mqtt>=1.6.1
amqtt>=0.12.0,<0.13.0
pyyaml>=6.0,<7.0
```

Plus the `benchlab_pycore` core modules and the rest of the `benchlab` package (installed as part of the overall project).

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
| `MQTT_PROTOCOL` | `MQTTv311` | MQTT protocol version. Accepts (case-insensitive): `MQTTv31`/`v3.1`/`3.1`/`3` for v3.1, `MQTTv311`/`v3.1.1`/`3.1.1`/`4` for v3.1.1 (default), `MQTTv5`/`v5`/`5` for v5. An unrecognized value raises an error at startup rather than failing silently later. |
| `MQTT_QOS` | `0` | Quality of Service (0, 1, or 2) |
| `MQTT_PATH` | None | WebSocket path (if transport is `websockets`) |
| `MQTT_TOPIC_PREFIX` | `benchlab` | Prefix for published topics, e.g. `<prefix>/<device_uid>/telemetry` |
| `MQTT_POLL_RATE` | `1` (or `mqtt.config`'s `poll_rate`) | Seconds between telemetry publishes per device |

### Poll Rate via Config File

The poll rate can also be set via a local `mqtt.config` file instead of `MQTT_POLL_RATE`. Copy `mqtt.config_template` to `mqtt.config` in this directory and edit the `poll_rate` value:

```ini
[settings]
poll_rate = 0.5
```

`MQTT_POLL_RATE`, if set, takes precedence over the config file.

---

## Usage

### Run MQTT Mode

```
python -m benchlab -mqtt
```

`-mqtt` takes an optional positional broker hostname. If omitted, it defaults to `localhost`:

```
python -m benchlab -mqtt mybroker.local
```

The broker hostname can also be set via the `MQTT_BROKER` environment variable, or overridden with `--mqtt-broker`/`--mqtt-port` (used when this tool is launched together with other tools that consume the same MQTT source, e.g. via `--source mqtt`).

Behavior:

- Detects all connected Benchlab devices.
- Starts a separate thread for each device.
- Publishes telemetry continuously until interrupted (`Ctrl+C`).

### MQTT Topics

Topics are published under a configurable prefix (`MQTT_TOPIC_PREFIX`, default `benchlab`):

#### Device Info

```
<topic_prefix>/<device_uid>/info
```

Published with `retain=True` so late subscribers can discover the device without waiting for the next info publish.

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
<topic_prefix>/<device_uid>/telemetry
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
