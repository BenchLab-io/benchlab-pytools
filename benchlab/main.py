"""
BENCHLAB PyTools v2 - Main Launcher

Refactored architecture:
  Step 1: Choose telemetry source (who owns the serial bus)
  Step 2: Choose consumer tools (all tools share the source from Step 1)
  Step 3: Launch — source starts first, then tools consume from it

Uses:
  - DeviceRegistry for device lifecycle tracking
  - ProcessManager for infrastructure service management (FastAPI, MQTT, broker)
"""

import argparse
import curses
import importlib
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from typing import List, Optional

from benchlab.core.process_manager import ProcessManager
from benchlab.core.device_registry import DeviceRegistry


# ──────────────────────────────────────────────────────────────
# Tool Definitions (consumer tools only)
# ──────────────────────────────────────────────────────────────

CONSUMER_TOOLS = {
    "tui": {
        "name": "TUI",
        "description": "Interactive terminal user interface",
        "flag": "-tui",
        "module": "benchlab.tui.tui_main",
        "function": "tui_main",
    },
    "csv_log": {
        "name": "CSV Logger",
        "description": "Log device telemetry to CSV files",
        "flag": "-logfleet",
        "module": "benchlab.csv_log.csv_logger_enhanced",
        "function": "run_enhanced_csv_logger",
    },
    "graph": {
        "name": "DearPyGui Graph",
        "description": "Interactive sensor graphing widget",
        "flag": "-graph",
        "module": "benchlab.graph.runner",
        "function": "run_graph_mode",
    },
    "hwinfo": {
        "name": "HWiNFO Export",
        "description": "Export sensors to HWiNFO64 custom sensors",
        "flag": "-hwinfo",
        "module": "benchlab.hwinfo.hwinfo_export",
        "function": "export_all_devices",
    },
    "vu": {
        "name": "VU Dials",
        "description": "Analog-style VU meter dials",
        "flag": "-vu",
        "module": "benchlab.vu.vu_updater",
        "function": "run_updater",
    },
    "vuconfig": {
        "name": "VU Config",
        "description": "VU meter configuration interface",
        "flag": "-vuconfig",
        "module": "benchlab.vu.vu_tui",
        "function": "launch_vu_config",
    },
    "wigidash": {
        "name": "WigiDash",
        "description": "Display telemetry on WigiDash device",
        "flag": "-wigidash",
        "module": "benchlab.wigidash.wigidash_manager",
        "function": "main",
    },
    "xeneon": {
        "name": "Xeneon Dashboard",
        "description": "Web-based telemetry dashboard",
        "flag": "-xeneon",
        "module": "benchlab.xeneon.xeneon_main",
        "function": "app",  # uvicorn.run(app) entry point
    },
}


# ──────────────────────────────────────────────────────────────
# Data Source Helpers
# ──────────────────────────────────────────────────────────────

def check_fastapi_running(host: str = "127.0.0.1", port: int = 8000) -> bool:
    """Check if FastAPI server is already running and has devices."""
    import urllib.request
    import urllib.error
    try:
        url = f"http://{host}:{port}/devices"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                import json
                devices = json.loads(resp.read().decode())
                return isinstance(devices, list) and len(devices) > 0
    except Exception:
        pass
    return False


def check_mqtt_running(host: str = "localhost", port: int = 1883) -> bool:
    """Check if MQTT broker is accepting connections."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _fastapi_health(host: str, port: int) -> bool:
    """Health check: /devices returns non-empty list."""
    import urllib.request, json
    try:
        url = f"http://{host}:{port}/devices"
        with urllib.request.urlopen(url, timeout=3) as resp:
            devices = json.loads(resp.read().decode())
            return isinstance(devices, list) and len(devices) > 0
    except Exception:
        return False


def start_fastapi_source(port: int = 8000) -> bool:
    """Start FastAPI server as telemetry source via ProcessManager."""
    pm = ProcessManager.get_instance()

    # Check if already managed
    if pm.is_running("fastapi"):
        return True

    print(f"  Starting FastAPI server on port {port}...")
    print("  Note: Make sure the serial device is not held by another process.")

    ok = pm.start_service(
        name="fastapi",
        cmd=[sys.executable, "-m", "benchlab", "-fastapi"],
        health_check=lambda: _fastapi_health("127.0.0.1", port),
        timeout=20,
    )

    if ok:
        print(f"  ✓ FastAPI server ready with device(s) on port {port}")
    else:
        svc = pm.get_service("fastapi")
        if svc:
            print(f"  ✗ FastAPI failed to start. Server log:")
            if svc.stderr_log or svc.stdout_log:
                for line in (svc.stderr_log or svc.stdout_log).splitlines()[-15:]:
                    print(f"    > {line}")
    return ok


def _mqtt_device_check(broker: str) -> bool:
    """Check if the MQTT datasource can discover any devices."""
    try:
        from benchlab.core.datasource import MQTTDataSource
        ds = MQTTDataSource(broker=broker, timeout=3)
        if ds.connect():
            devices = ds.list_devices()
            ds.disconnect()
            return len(devices) > 0
    except Exception:
        pass
    return False


def start_mqtt_broker(port: int = 1883) -> bool:
    """Start embedded amqtt broker if no external broker detected."""
    if check_mqtt_running(port=port):
        return True  # Broker already running

    print(f"  Starting embedded MQTT broker on port {port}...")
    pm = ProcessManager.get_instance()

    if pm.is_running("mqtt_broker"):
        return True

    broker_script = f'''
import asyncio
from amqtt.broker import Broker

config = {{
    "listeners": {{
        "default": {{
            "type": "tcp",
            "bind": "0.0.0.0:{port}",
        }},
    }},
    "auth": {{"allow-anonymous": True}},
    "topic-check": {{"enabled": False}},
}}

async def main():
    broker = Broker()
    await broker.start(config)
    while True:
        await asyncio.sleep(1)

asyncio.run(main())
'''
    def broker_port_check():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            return result == 0
        except Exception:
            return False

    ok = pm.start_service(
        name="mqtt_broker",
        cmd=[sys.executable, "-c", broker_script],
        health_check=broker_port_check,
        timeout=15,
    )

    if ok:
        print(f"  ✓ Embedded MQTT broker ready on port {port}")
    else:
        print(f"  ✗ Embedded MQTT broker failed to start")
    return ok


def start_mqtt_source(broker: str = "localhost", port: int = 1883) -> bool:
    """Start MQTT publisher as telemetry source via ProcessManager."""
    pm = ProcessManager.get_instance()

    # Check if already managed
    if pm.is_running("mqtt_publisher"):
        return True

    print(f"  Starting MQTT publisher to {broker}:{port}...")
    print("  Note: Make sure the serial device is not held by another process.")

    ok = pm.start_service(
        name="mqtt_publisher",
        cmd=[sys.executable, "-m", "benchlab", "-mqtt", broker],
        health_check=lambda: _mqtt_device_check(broker),
        timeout=20,
    )

    if ok:
        print(f"  ✓ MQTT publisher ready with device(s)")
    else:
        svc = pm.get_service("mqtt_publisher")
        if svc:
            print(f"  ✗ MQTT publisher failed to start. Server log:")
            if svc.stderr_log or svc.stdout_log:
                for line in (svc.stderr_log or svc.stdout_log).splitlines()[-15:]:
                    print(f"    > {line}")
    return ok


def check_and_setup_source(source_type: str, **kwargs) -> bool:
    """Check if a data source is available, start it if not.
    
    Returns True if source is ready, False if setup failed.
    """
    if source_type == "direct":
        # Direct mode doesn't need a subprocess — this process owns serial
        os.environ["BENCHLAB_DATA_SOURCE"] = "direct"
        return True

    if source_type == "fastapi":
        port = kwargs.get("port", 8000)
        host = kwargs.get("host", "127.0.0.1")
        api_url = f"http://{host}:{port}"
        os.environ["BENCHLAB_API_URL"] = api_url
        os.environ["API_PORT"] = str(port)

        if check_fastapi_running(host, port):
            print(f"  ✓ FastAPI already running at {api_url} (device(s) detected)")
            os.environ["BENCHLAB_DATA_SOURCE"] = "fastapi"
            return True
        else:
            print(f"  FastAPI not detected at {api_url}")
            return start_fastapi_source(port)

    if source_type == "mqtt":
        host = kwargs.get("broker", "localhost")
        mqtt_port = kwargs.get("mqtt_port", 1883)
        os.environ["MQTT_BROKER"] = host
        os.environ["MQTT_PORT"] = str(mqtt_port)

        # Step 1: Ensure broker is running
        if not check_mqtt_running(host, mqtt_port):
            print(f"  MQTT broker not detected at {host}:{mqtt_port}")
            if not start_mqtt_broker(mqtt_port):
                return False
        else:
            print(f"  ✓ MQTT broker available at {host}:{mqtt_port}")

        # Step 2: Start publisher and verify device discovery
        return start_mqtt_source(host, mqtt_port)

    return False


def _cleanup_all_services() -> None:
    """Kill all infrastructure services started during this session."""
    pm = ProcessManager.get_instance()
    pm.shutdown_all()
    # Also clear DeviceRegistry
    reg = DeviceRegistry.get_instance()
    reg.clear()


# ──────────────────────────────────────────────────────────────
# Menu Functions
# ──────────────────────────────────────────────────────────────

def show_step1_menu() -> Optional[str]:
    """Show main mode selection menu. Returns 'provider', 'single', 'multi', or None."""
    print("What would you like to do?\n")
    print("  1. Data Provider   - Run FastAPI or MQTT server for other tools")
    print("  2. Single Tool     - Run one tool with a data source")
    print("  3. Multi-Tool      - Run multiple tools with shared data")
    print()
    print("  q. Quit")
    print()

    try:
        choice = input("Choice: ").strip().lower()
        if choice == "1":
            return "provider"
        elif choice == "2":
            return "single"
        elif choice == "3":
            return "multi"
        elif choice in ("q", "quit", "exit"):
            return None
        else:
            print("Invalid choice. Enter 1, 2, 3, or q.")
    except (EOFError, KeyboardInterrupt):
        return None


def step2_data_provider() -> None:
    """Step 2a: Select and start a data provider."""
    print("\n=== Data Provider ===")
    print("  1. FastAPI Server  - REST API + WebSocket on port 8000")
    print("  2. MQTT Publisher  - Publish telemetry to MQTT broker")
    print()

    choice = input("Choice [1-2]: ").strip()

    if choice == "1":
        port_input = input("  Port [8000]: ").strip()
        port = int(port_input) if port_input else 8000
        os.environ["API_PORT"] = str(port)
        check_and_setup_source("fastapi", port=port)
        print("\n  Press Ctrl+C to stop the provider.\n")
        input("  (Press Enter to return to menu after verifying...) ")

    elif choice == "2":
        host = input("  Broker host [localhost]: ").strip() or "localhost"
        port_input = input("  Broker port [1883]: ").strip()
        port = int(port_input) if port_input else 1883
        if not check_mqtt_running(host, port):
            print(f"  ⚠ No MQTT broker at {host}:{port}")
            print("  Starting embedded broker...")
            if not start_mqtt_broker(port):
                print("  ✗ Could not start broker.")
                return
        else:
            print(f"  ✓ MQTT broker available at {host}:{port}")
        os.environ["MQTT_BROKER"] = host
        os.environ["MQTT_PORT"] = str(port)
        start_mqtt_source(host, port)
        print("\n  Press Ctrl+C to stop the provider.\n")
        input("  (Press Enter to return to menu after verifying...) ")
    else:
        print("  Invalid choice.")


def step2_single_tool() -> None:
    """Step 2b: Select one tool and its data source."""
    print("\n=== Select Tool ===")
    consumer_list = list(CONSUMER_TOOLS.items())
    for i, (tid, t) in enumerate(consumer_list, 1):
        print(f"  {i}. {t['name']} - {t['description']}")
    print()

    choice = input("Tool number: ").strip()
    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(consumer_list)):
            print("  Invalid selection.")
            return
        tool_id, tool_info = consumer_list[idx]
    except (ValueError, IndexError):
        print("  Invalid selection.")
        return

    # Step 3: Select data source
    step3_select_source(tool_ids=[tool_id], tool_names=[tool_info["name"]])


def step2_multi_tool() -> None:
    """Step 2c: Select multiple tools and shared data source."""
    print("\n=== Select Tools ===")
    print("Enter tool numbers separated by commas (e.g., 1,3,5)")
    print("Or 'all' to select all.\n")

    consumer_list = list(CONSUMER_TOOLS.items())
    for i, (tid, t) in enumerate(consumer_list, 1):
        print(f"  {i}. {t['name']} - {t['description']}")
    print()

    choice = input("Tools: ").strip().lower()
    if not choice:
        print("  No tools selected.")
        return

    if choice == "all":
        selected = [(tid, t) for tid, t in consumer_list]
    else:
        selected = []
        for part in choice.split(","):
            part = part.strip()
            try:
                idx = int(part) - 1
                if 0 <= idx < len(consumer_list):
                    selected.append(consumer_list[idx])
            except ValueError:
                pass

    if not selected:
        print("  No valid tools selected.")
        return

    tool_ids = [tid for tid, _ in selected]
    tool_names = [t["name"] for _, t in selected]

    # Step 3: Select data source
    step3_select_source(tool_ids=tool_ids, tool_names=tool_names)


def step3_select_source(tool_ids: List[str], tool_names: List[str]) -> None:
    """Step 3: Select data source, check/start it, then launch tools."""
    is_multi = len(tool_ids) > 1
    print(f"\n=== Data Source ===")
    if is_multi:
        print(f"Tools: {', '.join(tool_names)}")
        print("  1. FastAPI (recommended)")
        print("  2. MQTT")
        print()
        print("  Note: Direct mode is not available for multi-tool")
        print("  because the serial port can only be used by one application.")
    else:
        print(f"Tool: {tool_names[0]}")
        print("  1. Direct (serial port)")
        print("  2. FastAPI")
        print("  3. MQTT")
    print()

    default = "1" if not is_multi else "1"
    choice = input(f"Choice [1-3] (default: {default}): ").strip() or default

    source_ready = False

    # Map choice to source type based on context
    if is_multi:
        source_map = {"1": "fastapi", "2": "mqtt"}
    else:
        source_map = {"1": "direct", "2": "fastapi", "3": "mqtt"}
    
    if choice not in source_map:
        print("  Invalid choice.")
        return

    source_type = source_map[choice]
    print(f"\n  Setting up {source_type} data source...")

    if source_type == "fastapi":
        port = int(os.environ.get("API_PORT", "8000"))
        os.environ["API_PORT"] = str(port)
        source_ready = check_and_setup_source("fastapi", port=port)
    elif source_type == "mqtt":
        broker = os.environ.get("MQTT_BROKER", "localhost")
        mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        os.environ["MQTT_BROKER"] = broker
        os.environ["MQTT_PORT"] = str(mqtt_port)
        source_ready = check_and_setup_source("mqtt", broker=broker, mqtt_port=mqtt_port)
    else:
        source_ready = check_and_setup_source("direct")

    if not source_ready:
        print(f"\n  ✗ Could not set up {source_type} data source.")
        return

    print(f"\n=== Launch Summary ===")
    print(f"  Tools: {', '.join(tool_names)}")
    print(f"  Data source: {source_type}")
    print()

    confirm = input("Launch? (Y/n): ").strip().lower()
    if confirm in ("n", "no"):
        print("  Aborted.")
        return

    # Set data source env
    os.environ["BENCHLAB_DATA_SOURCE"] = source_type

    # Launch tools
    try:
        if is_multi:
            _launch_tools_concurrent(tool_ids)
        else:
            _launch_single_tool(tool_ids[0])
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        # Clean up all infrastructure services started during this session
        _cleanup_all_services()


def _launch_single_tool(tool_id: str) -> None:
    """Launch a single tool (blocks until exit)."""
    tool = CONSUMER_TOOLS[tool_id]
    print(f"\n  Starting {tool['name']}...")
    print("  Press Ctrl+C to stop.\n")

    try:
        module = importlib.import_module(tool["module"])
        func = getattr(module, tool["function"])

        if tool_id == "csv_log":
            interval = float(os.environ.get("CSV_LOG_INTERVAL", "1.0"))
            data_source = os.environ.get("BENCHLAB_DATA_SOURCE", "direct")
            func(interval, data_source)
        elif tool_id == "hwinfo":
            interval = float(os.environ.get("POLL_INTERVAL", "1.0"))
            func(update_interval=interval)
        elif tool_id == "vu":
            func()
        elif tool_id == "vuconfig":
            func()
        elif tool_id == "tui":
            import types
            args = types.SimpleNamespace()
            args.source = os.environ.get("BENCHLAB_DATA_SOURCE", "direct")
            args.interval = float(os.environ.get("POLL_INTERVAL", "1.0"))
            args.api_url = os.environ.get("BENCHLAB_API_URL", "http://127.0.0.1:8000")
            args.api_port = int(os.environ.get("API_PORT", "8000"))
            args.mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
            curses.wrapper(lambda stdscr: func(stdscr, None, args))
        elif tool_id == "wigidash":
            func()
        elif tool_id == "graph":
            func()
        elif tool_id == "xeneon":
            import uvicorn
            print("  Starting Xeneon Dashboard on http://localhost:8001")
            uvicorn.run(func, host="127.0.0.1", port=8001, log_level="info")
        else:
            func()

    except KeyboardInterrupt:
        print(f"\n  {tool['name']} stopped.")
    except Exception as e:
        print(f"\n  {tool['name']} failed: {e}")
        traceback.print_exc()


def _launch_tools_concurrent(tool_ids: List[str]) -> None:
    """Launch multiple tools in background threads."""
    threads: List[threading.Thread] = []

    def run_tool(tid: str):
        try:
            _launch_single_tool(tid)
        except Exception as e:
            print(f"\n  [{CONSUMER_TOOLS.get(tid, {}).get('name', tid)}] Error: {e}")

    for tid in tool_ids:
        t = threading.Thread(target=run_tool, args=(tid,), daemon=True)
        t.start()
        threads.append(t)
        print(f"  Started: {CONSUMER_TOOLS.get(tid, {}).get('name', tid)}")

    print(f"\n  Running {len(threads)} tool(s). Press Ctrl+C to stop all.\n")

    try:
        for t in threads:
            while t.is_alive():
                t.join(timeout=1)
    except (KeyboardInterrupt, EOFError):
        print("\n  Stopping all tools...")
    print("  Done.")


# ──────────────────────────────────────────────────────────────
# Interactive Loop
# ──────────────────────────────────────────────────────────────

def interactive_loop() -> None:
    """Main interactive menu loop."""
    while True:
        try:
            mode = show_step1_menu()
            if mode is None:
                print("  Goodbye!")
                # Clean up any services that may have been started
                _cleanup_all_services()
                return

            if mode == "provider":
                step2_data_provider()
            elif mode == "single":
                step2_single_tool()
            elif mode == "multi":
                step2_multi_tool()

            input("\n  Press Enter to continue... ")
            if os.name == "nt":
                os.system("cls")
            else:
                os.system("clear")
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            _cleanup_all_services()
            return


# ──────────────────────────────────────────────────────────────
# Argument Parser
# ──────────────────────────────────────────────────────────────

def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BENCHLAB PyTools v2 - Device Telemetry Suite"
    )

    parser.add_argument("-fastapi", action="store_true",
                        help="Launch FastAPI telemetry API server")
    parser.add_argument("-graph", action="store_true",
                        help="Launch GUI graphing mode")
    parser.add_argument("-hwinfo", action="store_true",
                        help="Export sensors to HWiNFO custom sensors")
    parser.add_argument("-i", "--interval", type=float, default=1.0,
                        help="Refresh interval in seconds")
    parser.add_argument("-logfleet", action="store_true",
                        help="Run CSV logger without TUI")
    parser.add_argument("-mqtt", nargs="?", const="localhost",
                        help="MQTT publisher to localhost mosquitto")
    parser.add_argument("-tui", action="store_true",
                        help="Enable TUI (default)")
    parser.add_argument("-vu", action="store_true",
                        help="Launch VU analog dials")
    parser.add_argument("-vuconfig", action="store_true",
                        help="Launch VU configuration interface")
    parser.add_argument("-wigidash", action="store_true",
                        help="Connect to WigiDash")
    parser.add_argument("-xeneon", action="store_true",
                        help="Launch Xeneon web dashboard")

    return parser


def launch_mode() -> None:
    """Handle CLI arguments - respects BENCHLAB_DATA_SOURCE env if set."""
    parser = get_parser()
    args = parser.parse_args()

    # If no flags, run interactive mode
    if not any([args.fastapi, args.graph, args.hwinfo, args.logfleet,
                args.mqtt, args.tui, args.vu, args.vuconfig,
                args.wigidash, args.xeneon]):
        interactive_loop()
        return

    if args.fastapi:
        try:
            from benchlab.fastapi.telemetry_api import run_server
            run_server()
        except ModuleNotFoundError:
            print("FastAPI / Uvicorn not available in this build.")

    elif args.graph:
        try:
            from benchlab.graph.runner import run_graph_mode
            run_graph_mode()
        except ModuleNotFoundError:
            print("Graph module not available in this build.")

    elif args.hwinfo:
        try:
            from benchlab.hwinfo.hwinfo_export import export_all_devices
            export_all_devices(update_interval=args.interval)
        except ModuleNotFoundError:
            print("HWiNFO export module not available in this build.")

    elif args.logfleet:
        try:
            from benchlab.csv_log.csv_logger_enhanced import run_enhanced_csv_logger
            run_enhanced_csv_logger(args.interval)
        except ModuleNotFoundError:
            print("Enhanced CSV logger not available in this build.")

    elif args.mqtt:
        try:
            from benchlab.mqtt.mqtt_publisher import run_mqtt_mode
            broker = args.mqtt if args.mqtt else "localhost"
            run_mqtt_mode(broker)
        except ModuleNotFoundError:
            print("MQTT module not available in this build.")

    elif args.vu:
        try:
            from benchlab.vu.vu_updater import run_updater
            run_updater()
        except ModuleNotFoundError:
            print("VU module not available in this build.")

    elif args.vuconfig:
        try:
            from benchlab.vu.vu_tui import launch_vu_config
            launch_vu_config()
        except ModuleNotFoundError:
            print("VU configuration module not available in this build.")

    elif args.wigidash:
        try:
            from benchlab.wigidash.wigidash_manager import main
            main()
        except ModuleNotFoundError:
            traceback.print_exc()
            print("WigiDash module not available in this build.")

    elif args.xeneon:
        try:
            from benchlab.xeneon.xeneon_main import app
            import uvicorn
            print("Starting Xeneon Dashboard with Device Telemetry...")
            print("Dashboard: http://localhost:8001/xeneon/dashboard")
            print("Iframe URL: http://localhost:8001/xeneon")
            print("Press Ctrl+C to stop the server")
            print("-" * 50)
            uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
        except ModuleNotFoundError:
            print("Xeneon dashboard module not available in this build.")

    else:  # default: TUI
        try:
            from benchlab.tui.tui_main import tui_main
            curses.wrapper(tui_main, None, args)
        except ModuleNotFoundError:
            print("TUI module not available in this build.")


def main() -> None:
    """Entry point."""
    launch_mode()


if __name__ == "__main__":
    main()