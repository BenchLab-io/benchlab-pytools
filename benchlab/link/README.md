# BENCHLAB Link

## Overview

Benchlab Link is a cloud MQTT publisher. It reads telemetry from any local BENCHLAB data source (direct serial, FastAPI, or MQTT) and republishes it as JSON to a remote/cloud MQTT broker — typically BENCHLAB's SaaS broker, for dashboards, fleet monitoring, or other cloud consumers.

It automatically:

- Connects to a local data source via `DataSourceManager` (direct / fastapi / fastapi_custom / mqtt).
- Polls each discovered device's telemetry snapshot on a background thread.
- Publishes a JSON payload per device to the remote broker on a configurable interval.
- Supports TLS and WebSocket transport for the remote connection.
- Reconnects automatically if the remote broker connection drops.
- Handles graceful shutdown on Ctrl+C.

## Features

| Feature | Description |
|---|---|
| Local source flexibility | Works with any `--source` supported by the rest of the suite (direct, fastapi, fastapi_custom, mqtt). |
| Cloud MQTT publishing | Publishes one topic per device, built from a configurable `{uid}` topic pattern. |
| TLS / WebSocket support | Connects over `websockets` (default) or `tcp` transport, with TLS enabled by default. |
| Auto-reconnect | If the cloud broker connection drops, Link retries every 5 seconds using a fresh client. |
| Layered configuration | CLI args > environment variables > `.env` file > `link.config` JSON file > built-in defaults. |
| Graceful shutdown | Stops the poller thread, disconnects from both the local source and the cloud broker on Ctrl+C. |

## Installation

Link is included in BENCHLAB PyTools. It requires `paho-mqtt` (already a dependency of the suite) and whatever the chosen local `--source` requires (e.g. `benchlab-pycore` for `direct`, `requests` for `fastapi`).

## Usage

Run via the main launcher:

```bash
python -m benchlab -link
```

By default this connects to the local `direct` data source and publishes to the remote host configured via env/config (see Configuration below). Combine with the usual `--source` flags to choose a different local source:

```bash
# Read telemetry from a local FastAPI server instead of direct serial
python -m benchlab -link --source fastapi --api-url http://127.0.0.1:8000

# Read telemetry from local MQTT
python -m benchlab -link --source mqtt --mqtt-broker localhost --mqtt-port 1883
```

Cloud connection settings can be passed directly on the command line, overriding any env var / `.env` / `link.config` value:

```bash
python -m benchlab -link \
  --remote-host mqtt.benchlab.io \
  --remote-port 443 \
  --remote-user my-device-id \
  --remote-pass "my-secret" \
  --topic-pattern "benchlab/{uid}/telemetry"

# Disable TLS (e.g. for a local test broker)
python -m benchlab -link --remote-host localhost --remote-port 1883 --no-tls
```

CLI flags (from `benchlab/main.py`):

| Flag | Description |
|---|---|
| `-link` | Run the cloud MQTT link publisher |
| `--remote-host` | Cloud MQTT broker hostname (overrides `REMOTE_MQTT_HOST`) |
| `--remote-port` | Cloud MQTT broker port (default: 443) |
| `--remote-user` | Cloud MQTT username (overrides `REMOTE_MQTT_USER`) |
| `--remote-pass` | Cloud MQTT password (overrides `REMOTE_MQTT_PASS`) |
| `--no-tls` | Disable TLS for the cloud MQTT connection |
| `--topic-pattern` | MQTT topic pattern with a `{uid}` token (overrides `LINK_TOPIC_PATTERN`) |
| `--source` | Local data source: `direct` \| `fastapi` \| `fastapi_custom` \| `mqtt` \| `mqtt_custom` |
| `--api-url` | FastAPI base URL, when `--source fastapi`/`fastapi_custom` |
| `--mqtt-broker`, `--mqtt-port` | Local MQTT broker, when `--source mqtt`/`mqtt_custom` |

You can also run the module directly with a pre-built `argparse.Namespace` (as `run_link(args)` is called from `main.py`), or invoke `python -m benchlab.link.link_main` to use env-var-only defaults (`BENCHLAB_DATA_SOURCE`, `MQTT_POLL_RATE`, `BENCHLAB_API_URL`, `MQTT_BROKER`, `MQTT_PORT`).

## Configuration

Configuration is resolved with this priority (highest first):

1. CLI args (`--remote-host`, `--no-tls`, etc. — mirrored into env vars by `main.py`'s `_export_link_env()`)
2. Environment variables
3. `.env` file in `benchlab/link/` (only sets a var if it isn't already set in the environment)
4. `link.config` JSON file (`benchlab/link/link.config` by default, or `LINK_CONFIG_PATH`)
5. Built-in defaults

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `REMOTE_MQTT_HOST` | *(none — required)* | Cloud MQTT broker hostname |
| `REMOTE_MQTT_PORT` | `443` | Cloud MQTT broker port |
| `REMOTE_MQTT_USER` | *(none)* | MQTT username |
| `REMOTE_MQTT_PASS` | *(none)* | MQTT password |
| `REMOTE_MQTT_PATH` | `/mqtt` | WebSocket path (used when transport is `websockets`) |
| `REMOTE_MQTT_TRANSPORT` | `websockets` | Transport: `websockets` or `tcp` |
| `REMOTE_MQTT_PROTOCOL` | `mqtt.MQTTv5` | MQTT protocol version string; `"5"` in the value selects `MQTTv5`, anything else selects `MQTTv311` |
| `REMOTE_MQTT_QOS` | `1` | QoS level: 0, 1, or 2 |
| `REMOTE_MQTT_TLS` | `true` | Enable TLS. Set to `false`/`0`/`no` to disable |
| `CLIENT_UUID` | *(none)* | Device UUID, used to build the default MQTT client ID |
| `MQTT_POLL_RATE` | `2` | Poll/publish interval in seconds |
| `LINK_TOPIC_PATTERN` | `benchlab/{uid}/telemetry` | Topic pattern; `{uid}` (device UID) and `{client_uuid}` (from `CLIENT_UUID`) are replaced for each publish |
| `LINK_CLIENT_ID` | `benchlab-link-<uuid-or-hostname>` | MQTT client ID sent to the broker |
| `LINK_CONFIG_PATH` | `benchlab/link/link.config` | Override path to the JSON config file |

### `.env` file

Place a `.env` file in `benchlab/link/` with `KEY=VALUE` pairs — copy `.env.EXAMPLE` as a starting point:

```
REMOTE_MQTT_HOST=mqtt.benchlab.io
REMOTE_MQTT_PORT=443
REMOTE_MQTT_PATH=/mqtt
REMOTE_MQTT_TRANSPORT=websockets
REMOTE_MQTT_PROTOCOL=mqtt.MQTTv5
REMOTE_MQTT_QOS=1
REMOTE_MQTT_USER=mqtt_user
REMOTE_MQTT_PASS=mqtt_pass
CLIENT_UUID=client_uuid
MQTT_POLL_RATE=2
```

`.env` values only take effect if the corresponding environment variable is not already set (i.e. they are a fallback, not an override).

### `link.config` JSON file

`benchlab/link/link.config` uses the same settings under snake_case keys:

```json
{
  "remote_host":      "mqtt.benchlab.io",
  "remote_port":      443,
  "remote_user":      "test-device-001",
  "remote_pass":      "your-mqtt-password",
  "remote_tls":       true,
  "topic_pattern":    "benchlab/{uid}/telemetry",
  "publish_interval": 1.0,
  "client_id":        "your-client-uuid"
}
```

This file is loaded only as a fallback below env vars/`.env`/CLI args, and is `.gitignore`d — create it locally with real credentials, it is never checked in.

### Topic pattern

`topic_pattern` (default `benchlab/{uid}/telemetry`) is formatted per-device with `.format(uid=uid, client_uuid=client_uuid)`, so both `{uid}` (the device's UID) and `{client_uuid}` (from `CLIENT_UUID`/`client_uuid`, empty string if unset) are supported tokens. For example, a deployment can use `clients/{client_uuid}/devices/{uid}/telemetry` to key topics by tenant/client instead of by device alone. Published payloads are `{"uid": <uid>, ...telemetry fields...}`.

## Troubleshooting

**"No remote MQTT host configured"** — `REMOTE_MQTT_HOST` (or `remote_host` in `link.config`, or `--remote-host`) is not set. Link exits immediately without a host.

**"Failed to connect to `<source>` datasource"** — the local data source (direct/fastapi/mqtt) could not be reached. Verify the device is connected (for `direct`) or the FastAPI/MQTT service is running and reachable at the configured URL/broker.

**"Cloud broker disconnected — retrying in 5s"** — the connection to the remote broker dropped; Link automatically reconnects with a fresh client every 5 seconds. Check network connectivity and credentials if this repeats continuously.

**Connection timeout to cloud broker** — verify `REMOTE_MQTT_PORT` matches the transport (443/`websockets`+TLS is typical for BENCHLAB's cloud broker; local test brokers are often 1883/`tcp` with `--no-tls`), and that `REMOTE_MQTT_PATH` is correct when using `websockets` transport.

**Nothing gets published** — Link only publishes devices it currently has a telemetry snapshot for. Confirm the local data source's `list_devices()`/`snapshot()` calls are returning data; check the local source is healthy first (e.g. hit the FastAPI server's `/health` endpoint) before troubleshooting Link itself.

## See Also

- [BENCHLAB PyTools Documentation](../../README.md) - Main PyTools documentation
- [BENCHLAB Core README](../core/README.md) - `DataSourceManager` and the data source abstraction Link consumes
- [MQTT Publisher README](../mqtt/README.md) - the local MQTT source Link can read from
- [FastAPI Telemetry Server README](../restapi/readme.md) - the local FastAPI source Link can read from

## License

Part of BENCHLAB PyTools. See main project license for details.
