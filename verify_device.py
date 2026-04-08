#!/usr/bin/env python
"""
Benchlab Device Verification Script

Simple, readable health check covering all three data ingestion paths:
  1. Direct serial (pycore)
  2. Indirect via FastAPI REST
  3. Indirect via MQTT telemetry

Usage:
    python verify_device.py              # Run all checks
    python verify_device.py --direct     # Direct only
    python verify_device.py --fastapi    # FastAPI only
    python verify_device.py --mqtt       # MQTT only
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

# ── Imports (pycore) ────────────────────────────────────────────────────────

try:
    from benchlab_pycore.core.serial_io import get_fleet_info, open_serial_connection
    from benchlab_pycore.core import read_uid, read_device, read_sensors, translate_sensor_struct
except ImportError as e:
    print(f"ERROR: Cannot import benchlab_pycore: {e}")
    print("Make sure you're in the correct virtual environment.")
    sys.exit(1)

# ── Key sensors to highlight ────────────────────────────────────────────────

KEY_SENSORS = [
    "SYS_Power",
    "CPU_Power",
    "GPU_Power",
    "MB_Power",
    "12V_Power",
    "5V_Power",
    "Chip_Temp",
    "Ambient_Temp",
]

# ── Helpers ─────────────────────────────────────────────────────────────────

_print_lock = threading.Lock()

def _ok(text):
    with _print_lock:
        print(f"  ✓ {text}")


def _fail(text):
    with _print_lock:
        print(f"  ✗ {text}")


def _skip(text):
    with _print_lock:
        print(f"  ⊘ {text}")


def _section(text):
    with _print_lock:
        print(f"\n{text}")
        print("  " + "─" * 40)


# ── Test 1: Direct Serial (pycore) ─────────────────────────────────────────

def test_direct_serial(port, uid):
    """Read device directly via pycore serial."""
    results = {"passed": 0, "failed": 0}

    # Step 1: Direct UID read
    _section("[1/5] Direct UID read...")
    try:
        ser = open_serial_connection(port)
    except Exception as e:
        _fail(f"Cannot open serial port: {e}")
        results["failed"] += 1
        return results

    try:
        direct_uid = read_uid(ser)
    except Exception as e:
        _fail(f"UID read failed: {e}")
        results["failed"] += 1
        ser.close()
        return results

    if direct_uid == uid:
        _ok(f"UID matches: {direct_uid}")
        results["passed"] += 1
    else:
        _fail(f"UID mismatch: fleet={uid!r}, direct={direct_uid!r}")
        results["failed"] += 1

    # Step 2: Device info
    _section("[2/5] Device info...")
    try:
        dev_info = read_device(ser)
    except Exception as e:
        _fail(f"Device info read failed: {e}")
        results["failed"] += 1
        dev_info = {}

    if dev_info:
        _ok(f"Vendor: {dev_info.get('VendorId', '?')}")
        _ok(f"Product: {dev_info.get('ProductId', '?')}")
        _ok(f"Firmware: {dev_info.get('FwVersion', '?')}")
        results["passed"] += 1

    # Step 3: Sensors
    _section("[3/5] Sensor readings via pycore...")
    sensor_data = {}
    for key in KEY_SENSORS:
        try:
            sensors = read_sensors(ser)
            if sensors is None:
                _fail(f"read_sensors returned None for {key}")
                results["failed"] += 1
                break
            sensor_data = translate_sensor_struct(sensors)
            val = sensor_data.get(key, "N/A")
            unit = "W" if "Power" in key else "°C" if "Temp" in key else ""
            _ok(f"{key:20s} = {val}{unit}")
            results["passed"] += 1
        except Exception as e:
            _fail(f"{key}: {e}")
            results["failed"] += 1

    total_sensors = len(sensor_data)
    if total_sensors > 0:
        _ok(f"Total sensors: {total_sensors}")
        results["passed"] += 1

    # Step 4: Rapid serial stress
    _section("[4/5] Rapid serial stress test (5 reads)...")
    all_ok = True
    for i in range(5):
        try:
            sensors = read_sensors(ser)
            if sensors is None:
                _fail(f"Read {i+1}: returned None")
                all_ok = False
                break
            data = translate_sensor_struct(sensors)
            if not data:
                _fail(f"Read {i+1}: empty data")
                all_ok = False
                break
        except Exception as e:
            _fail(f"Read {i+1}: {e}")
            all_ok = False
            break

    if all_ok:
        _ok("All 5 reads returned valid data")
        results["passed"] += 1
    else:
        results["failed"] += 1

    try:
        ser.close()
    except Exception:
        pass

    return results


# ── Test 2: FastAPI ─────────────────────────────────────────────────────────

# Suppress FastAPI/MQTT import logging noise
import logging
logging.getLogger("benchlab").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("mqtt_bridge").setLevel(logging.WARNING)


def test_fastapi(port, uid, timeout=30):
    """Start FastAPI server, query REST API, then stop it."""
    results = {"passed": 0, "failed": 0}
    proc = None
    base_url = "http://127.0.0.1:8000"

    _section("[DATA INGESTION] Path 2: FastAPI REST")

    # Kill any existing process on port 8000
    if _is_port_in_use(8000):
        _ok(f"Port 8000 is in use, cleaning up...")
        _kill_process_on_port(8000)

    print(f"  Starting FastAPI server on {base_url}...")

    # Start FastAPI as subprocess with new process group for proper signal handling
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "benchlab.fastapi.telemetry_api:app",
             "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
    except Exception as e:
        _fail(f"Failed to start FastAPI server: {e}")
        results["failed"] += 1
        return results

    # Wait for server to be ready
    started = False
    deadline = time.time() + timeout
    try:
        import urllib.request
        while time.time() < deadline:
            time.sleep(1)
            ret = proc.poll()
            if ret is not None:
                _, stderr = proc.communicate()
                _fail(f"Server exited with code {ret}: {stderr.decode()[-200:]}")
                results["failed"] += 1
                return results
            try:
                req = urllib.request.urlopen(f"{base_url}/health", timeout=2)
                if req.status == 200:
                    started = True
                    break
            except Exception:
                pass

        if not started:
            _fail(f"FastAPI server did not start within {timeout}s")
            results["failed"] += 1
            return results
    except ImportError:
        _fail("Cannot import urllib.request")
        results["failed"] += 1
        _cleanup_proc(proc)
        return results

    _ok("FastAPI server is ready")
    results["passed"] += 1

    # Query /devices
    _section("  /devices")
    try:
        req = urllib.request.urlopen(f"{base_url}/devices", timeout=5)
        devices = json.loads(req.read())
        _ok(f"Found {len(devices)} device(s) via API")
        results["passed"] += 1
        for d in devices:
            print(f"    UID: {d.get('uid')}, Port: {d.get('port')}")
    except Exception as e:
        _fail(f"/devices failed: {e}")
        results["failed"] += 1

    # Wait for telemetry to be collected
    print("  Waiting for first telemetry sample...")
    telemetry = None
    for _ in range(timeout):
        time.sleep(1)
        try:
            req = urllib.request.urlopen(f"{base_url}/device/{uid}/telemetry", timeout=3)
            data = json.loads(req.read())
            if data and "error" not in data:
                telemetry = data
                break
        except Exception:
            pass

    if telemetry:
        _section(f"  /device/{uid}/telemetry")
        _ok("Telemetry received")
        results["passed"] += 1

        for key in KEY_SENSORS:
            val = telemetry.get(key, "N/A")
            unit = "W" if "Power" in key else "°C" if "Temp" in key else ""
            _ok(f"{key:20s} = {val}{unit}")
            results["passed"] += 1
    else:
        _fail("No telemetry received after waiting")
        results["failed"] += 1

    # Stop server
    _cleanup_proc(proc)
    _ok("FastAPI server stopped")
    return results


def _cleanup_proc(proc):
    """Terminate a subprocess, forcefully if needed."""
    if proc is None:
        return
    if proc.poll() is not None:
        return

    # On Windows, try taskkill first (most reliable)
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=5,
            )
            proc.wait(timeout=3)
            return
        except (subprocess.TimeoutExpired, Exception):
            pass

    # Fallback: signal-based
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass


def _is_port_in_use(port, host="127.0.0.1", timeout=1):
    """Check if a TCP port is already in use."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def _kill_process_on_port(port):
    """Attempt to kill any process listening on the given port (works on Windows and Linux)."""
    try:
        if sys.platform == "win32":
            # Use netstat to find the PID, then taskkill
            result = subprocess.run(
                ["netstat", "-ano"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    _ok(f"Killing existing process on port {port} (PID {pid})...")
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    time.sleep(0.5)
                    break
        else:
            # lsof on Linux/macOS
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if result.stdout.strip():
                for pid in result.stdout.strip().splitlines():
                    _ok(f"Killing existing process on port {port} (PID {pid})...")
                    subprocess.run(["kill", "-9", pid],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    time.sleep(0.5)
    except Exception as e:
        _ok(f"Note: Could not check port {port}: {e}")


# ── Test 3: MQTT ────────────────────────────────────────────────────────────

def _check_broker_running(host="127.0.0.1", port=1883, timeout=2):
    """Check if an MQTT broker is already running."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def _start_embedded_broker(host="127.0.0.1", port=1883):
    """Start an embedded amqtt broker as a subprocess."""
    import yaml
    import tempfile

    # amqtt v0.11+ config format
    broker_config = {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": host,
                "port": port,
            }
        },
        "sys_interval": 0,
        "auth": {
            "allow-anonymous": True,
        },
        "topic-check": {
            "enabled": False,
        },
    }

    # Write config to temp file
    fd, config_path = tempfile.mkstemp(suffix=".yaml", prefix="amqtt_")
    with os.fdopen(fd, "w") as f:
        yaml.dump(broker_config, f)

    _ok(f"Starting embedded MQTT broker on {host}:{port}...")

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "amqtt.broker", "-c", config_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return proc, config_path
    except Exception as e:
        os.unlink(config_path)
        raise e


def _stop_embedded_broker(proc, config_path):
    """Stop the embedded broker and clean up temp config."""
    _cleanup_proc(proc)
    try:
        os.unlink(config_path)
    except OSError:
        pass


def test_mqtt(port, uid, timeout=30):
    """Start MQTT publisher in thread, subscribe, and verify telemetry.
    Auto-starts an embedded broker if none is available locally.
    """
    results = {"passed": 0, "failed": 0}
    received_messages = []
    broker_proc = None
    broker_config_path = None
    broker_started = False

    _section("[DATA INGESTION] Path 3: MQTT")

    import paho.mqtt.client as mqtt

    # Create MQTT subscriber
    try:
        from paho.mqtt.enums import CallbackAPIVersion
        sub_client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id="verify_subscriber"
        )
        use_v2 = True
    except (ImportError, TypeError):
        sub_client = mqtt.Client(client_id="verify_subscriber")
        use_v2 = False

    received_lock = threading.Lock()

    def on_message_v2(client, userdata, msg):
        with received_lock:
            try:
                payload = json.loads(msg.payload)
                received_messages.append(payload)
            except Exception:
                pass

    def on_message_v1(client, userdata, msg):
        with received_lock:
            try:
                payload = json.loads(msg.payload)
                received_messages.append(payload)
            except Exception:
                pass

    def on_connect_v2(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(f"benchlab/{uid}/telemetry", qos=0)
            client.subscribe(f"benchlab/{uid}/info", qos=0)
        else:
            print(f"  MQTT connect failed with rc={rc}")

    def on_connect_v1(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"benchlab/{uid}/telemetry", qos=0)
            client.subscribe(f"benchlab/{uid}/info", qos=0)
        else:
            print(f"  MQTT connect failed with rc={rc}")

    if use_v2:
        sub_client.on_message = on_message_v2
        sub_client.on_connect = on_connect_v2
    else:
        sub_client.on_message = on_message_v1
        sub_client.on_connect = on_connect_v1

    # Check for existing broker, start embedded if none found
    if _check_broker_running():
        _ok("Found existing MQTT broker")
    else:
        _ok("No MQTT broker found, starting embedded one...")
        try:
            broker_proc, broker_config_path = _start_embedded_broker()
            broker_started = True
            # Wait for broker to be ready
            for _ in range(10):
                time.sleep(0.5)
                if _check_broker_running():
                    _ok("Embedded broker is ready")
                    break
            else:
                _fail("Embedded broker did not start within timeout")
                results["failed"] += 1
                return results
        except Exception as e:
            _fail(f"Failed to start embedded broker: {e}")
            results["failed"] += 1
            return results

    # Connect subscriber
    try:
        sub_client.connect("localhost", 1883)
        sub_client.loop_start()
        _ok("Connected as MQTT subscriber")
        results["passed"] += 1
    except Exception as e:
        _fail(f"Subscriber connect failed: {e}")
        results["failed"] += 1
        sub_client.loop_stop()
        if broker_started and broker_proc:
            _stop_embedded_broker(broker_proc, broker_config_path)
        return results

    # Start MQTT publisher thread
    print("  Starting MQTT publisher...")
    pub_thread = None
    try:
        from benchlab.mqtt.mqtt_publisher import (
            device_thread,
            load_mqtt_config,
            global_stop_event,
            device_stop_events,
        )

        fleet = get_fleet_info()
        dev = next((d for d in fleet if d["uid"] == uid), fleet[0] if fleet else None)
        if not dev:
            _fail("No device in fleet for MQTT publishing")
            results["failed"] += 1
            sub_client.loop_stop()
            return results

        cfg = load_mqtt_config()
        pub_thread = threading.Thread(
            target=device_thread,
            args=(dev, cfg, 1.0),
            daemon=True,
        )
        pub_thread.start()
        _ok("MQTT publisher started")
        results["passed"] += 1
    except Exception as e:
        _fail(f"Failed to start MQTT publisher: {e}")
        results["failed"] += 1
        sub_client.loop_stop()
        return results

    # Wait for messages
    print(f"  Waiting for telemetry on benchlab/{uid}/telemetry ...")
    for _ in range(timeout):
        with received_lock:
            if received_messages:
                break
        time.sleep(0.5)

    with received_lock:
        msgs = list(received_messages)

    # Stop publisher
    print("  Stopping MQTT publisher...")
    global_stop_event.set()
    for dev_stop in device_stop_events.values():
        dev_stop.set()
    if pub_thread:
        pub_thread.join(timeout=10)
    sub_client.loop_stop()

    if msgs:
        _ok(f"Received {len(msgs)} message(s)")
        results["passed"] += 1

        # Show last message
        payload = msgs[-1]
        print(f"  Device: {payload.get('timestamp', 'no-ts')}")
        for key in KEY_SENSORS:
            val = payload.get(key, "N/A")
            unit = "W" if "Power" in key else "°C" if "Temp" in key else ""
            _ok(f"{key:20s} = {val}{unit}")
            results["passed"] += 1
    else:
        _fail("No MQTT messages received")
        results["failed"] += 1

    # Cleanup embedded broker
    if broker_started and broker_proc:
        _ok("Stopping embedded MQTT broker...")
        _stop_embedded_broker(broker_proc, broker_config_path)

    return results


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchlab device verification")
    parser.add_argument("--direct", action="store_true", help="Run direct serial test only")
    parser.add_argument("--fastapi", action="store_true", help="Run FastAPI test only")
    parser.add_argument("--mqtt", action="store_true", help="Run MQTT test only")
    args = parser.parse_args()

    run_all = not (args.direct or args.fastapi or args.mqtt)

    print("=" * 50)
    print("  Benchlab Device Verification")
    print("=" * 50)

    # ── Discovery ──────────────────────────────────────────────────────────
    _section("[DISCOVERY] Finding devices...")
    try:
        devices = get_fleet_info()
    except Exception as e:
        _fail(f"Fleet info failed: {e}")
        sys.exit(1)

    if not devices:
        _fail("No devices found")
        print("\n  Make sure the device is plugged in and recognized.")
        print("  Hardware ID should show: VID_0483+PID_5740")
        sys.exit(1)

    _ok(f"Found {len(devices)} device(s)")
    dev = devices[0]
    uid = dev.get("uid", "unknown")
    port = dev.get("port", "unknown")
    fw = dev.get("firmware", "unknown")
    print(f"    UID:      {uid}")
    print(f"    Port:     {port}")
    print(f"    Firmware: v{fw}" if isinstance(fw, int) else f"    Firmware: {fw}")

    # ── Run selected tests ─────────────────────────────────────────────────
    total_passed = 0
    total_failed = 0

    if run_all or args.direct:
        r = test_direct_serial(port, uid)
        total_passed += r["passed"]
        total_failed += r["failed"]
        # Allow serial port to be fully released
        time.sleep(0.5)

    if run_all or args.fastapi:
        r = test_fastapi(port, uid)
        total_passed += r["passed"]
        total_failed += r["failed"]
        # Allow FastAPI server to fully release serial port
        time.sleep(1.0)

    if run_all or args.mqtt:
        # Reset global stop event for fresh MQTT test
        # (it may have been set by a previous run or MQTT test)
        import benchlab.mqtt.mqtt_publisher as mqtt_mod
        mqtt_mod.global_stop_event.clear()
        mqtt_mod.device_stop_events.clear()

        r = test_mqtt(port, uid)
        total_passed += r["passed"]
        total_failed += r["failed"]

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    print("=" * 50)
    print(f"  Total: {total_passed} passed, {total_failed} failed")
    if total_failed == 0:
        print("  All checks passed ✓")
    else:
        print("  Some checks failed ─ see above")
    print("=" * 50)
    sys.exit(0 if total_failed == 0 else 1)


if __name__ == "__main__":
    main()