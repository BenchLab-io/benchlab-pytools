"""BENCHLAB PyTools v2 – Interactive Menu System.

Implements the three-step interactive menu flow:
  1. Mode selection (provider / single tool / multi-tool)
  2. Tool selection
  3. Data source selection + launch confirmation
"""

import logging
import os
from typing import List, Optional

import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
from bootstrap import clear_screen
from .tools import CONSUMER_TOOLS
from .sources import (
    check_and_setup_source,
    check_mqtt_running,
    start_mqtt_broker,
    start_mqtt_source,
    cleanup_all_services,
)
from .launcher import launch_single_tool, launch_tools_concurrent

logger = logging.getLogger("benchlab.launcher")


# ──────────────────────────────────────────────────────────────
# Banner
# ──────────────────────────────────────────────────────────────

def print_banner() -> None:
    print(r"""
██████╗ ███████╗███╗   ██╗ ██████╗██╗  ██╗██╗      █████╗ ██████╗
██╔══██╗██╔════╝████╗  ██║██╔════╝██║  ██║██║     ██╔══██╗██╔══██╗
██████╔╝█████╗  ██╔██╗ ██║██║     ███████║██║     ███████║██████╔╝
██╔══██╗██╔══╝  ██║╚██╗██║██║     ██╔══██║██║     ██╔══██║██╔══██╗
██████╔╝███████╗██║ ╚████║╚██████╗██║  ██║███████╗██║  ██║██████╔╝
╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═════╝

        ██████╗ ██╗   ██╗████████╗ ██████╗  ██████╗ ██╗     ███████╗  
        ██╔══██╗╚██╗ ██╔╝╚══██╔══╝██╔═══██╗██╔═══██╗██║     ██╔════╝
        ██████╔╝ ╚████╔╝    ██║   ██║   ██║██║   ██║██║     ███████╗
        ██╔═══╝   ╚██╔╝     ██║   ██║   ██║██║   ██║██║     ╚════██║
        ██║        ██║      ██║   ╚██████╔╝╚██████╔╝███████╗███████║
        ╚═╝        ╚═╝      ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝╚══════╝
""")


# ──────────────────────────────────────────────────────────────
# Step 1 – Mode Selection
# ──────────────────────────────────────────────────────────────

def show_step1_menu() -> Optional[str]:
    """Display mode selection. Returns 'provider', 'single', 'multi', or None."""
    print("What would you like to do?\n")
    print("  1. Data Provider   - Run FastAPI or MQTT server for other tools")
    print("  2. Single Tool     - Run one tool with a data source")
    print("  3. Multi-Tool      - Run multiple tools with shared data (Experimental!)")
    print()
    print("  q. Quit")
    print()

    try:
        choice = input("Choice: ").strip().lower()
        return {"1": "provider", "2": "single", "3": "multi"}.get(choice) or (
            None if choice in ("q", "quit", "exit") else _invalid("Enter 1, 2, 3, or q.")
        )
    except (EOFError, KeyboardInterrupt):
        return None


def _invalid(msg: str) -> None:
    print(msg)
    return None


# ──────────────────────────────────────────────────────────────
# Step 2a – Data Provider
# ──────────────────────────────────────────────────────────────

def step2_data_provider() -> None:
    """Select and start a standalone data provider."""
    print()
    print("=== Data Provider ===")
    print("1. FastAPI Server  - REST API + WebSocket on port 8000")
    print("2. MQTT Publisher  - Publish telemetry to MQTT broker")
    print()

    choice = input("Choice [1-2]: ").strip()

    if choice == "1":
        port_input = input("  Port [8000]: ").strip()
        try:
            port = int(port_input) if port_input else 8000
        except ValueError:
            print("  Invalid port number.")
            return
        os.environ["API_PORT"] = str(port)
        if not check_and_setup_source("fastapi", port=port):
            logger.error("Could not start FastAPI server.")
            return
        print("FastAPI server running. Press Ctrl+C to stop the provider.")
        input("  (Press Enter to return to menu after verifying...) ")

    elif choice == "2":
        host = input("  Broker host [localhost]: ").strip() or "localhost"
        port_input = input("  Broker port [1883]: ").strip()
        try:
            port = int(port_input) if port_input else 1883
        except ValueError:
            print("  Invalid port number.")
            return
        if not check_mqtt_running(host, port):
            logger.warning(f"No MQTT broker at {host}:{port}")
            logger.info("Starting embedded broker...")
            if not start_mqtt_broker(port):
                logger.error("Could not start MQTT broker.")
                return
        else:
            logger.info(f"MQTT broker available at {host}:{port}")
        os.environ["MQTT_BROKER"] = host
        os.environ["MQTT_PORT"] = str(port)
        start_mqtt_source(host, port)
        logger.info("Press Ctrl+C to stop the provider.")
        input("  (Press Enter to return to menu after verifying...) ")

    else:
        logger.error("Invalid choice in data provider selection.")


# ──────────────────────────────────────────────────────────────
# Step 2b – Single Tool
# ──────────────────────────────────────────────────────────────

def step2_single_tool() -> None:
    """Select one tool and proceed to source selection."""
    print()
    print("=== Select Tool ===")
    consumer_list = list(CONSUMER_TOOLS.items())
    for i, (_, t) in enumerate(consumer_list, 1):
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

    step3_select_source(tool_ids=[tool_id], tool_names=[tool_info["name"]])


# ──────────────────────────────────────────────────────────────
# Step 2c – Multi-Tool
# ──────────────────────────────────────────────────────────────

def step2_multi_tool() -> None:
    """Select multiple tools and proceed to source selection. (Experimental!)"""
    print()
    print("=== Select Tools ===")
    print("Enter tool numbers separated by commas (e.g., 1,3,5)")
    print("Or 'all' to select all.")

    consumer_list = list(CONSUMER_TOOLS.items())
    for i, (_, t) in enumerate(consumer_list, 1):
        print(f"  {i}. {t['name']} - {t['description']}")
    print()

    choice = input("Tools: ").strip().lower()
    if not choice:
        print("  No tools selected.")
        return

    if choice == "all":
        selected = list(consumer_list)
    else:
        selected = []
        for part in choice.split(","):
            try:
                idx = int(part.strip()) - 1
                if 0 <= idx < len(consumer_list):
                    selected.append(consumer_list[idx])
            except ValueError:
                pass

    if not selected:
        print("  No valid tools selected.")
        return

    step3_select_source(
        tool_ids=[tid for tid, _ in selected],
        tool_names=[t["name"] for _, t in selected],
    )


# ──────────────────────────────────────────────────────────────
# Step 3 – Source Selection & Launch
# ──────────────────────────────────────────────────────────────

def step3_select_source(tool_ids: List[str], tool_names: List[str]) -> None:
    """Select data source, verify/start it, confirm, then launch tools."""
    is_multi = len(tool_ids) > 1
    print()
    print("=== Data Source ===")

    if is_multi:
        print(f"Tools: {', '.join(tool_names)}")
        print("1. FastAPI (recommended)")
        print("2. MQTT")
        print()
        print("  Note: Direct mode is not available for multi-tool")
        print("  because the serial port can only be used by one application.")
        source_map = {"1": "fastapi", "2": "mqtt"}
    else:
        print(f"Tool: {tool_names[0]}")
        print("1. Direct (serial port)")
        print("2. FastAPI")
        print("3. MQTT")
        source_map = {"1": "direct", "2": "fastapi", "3": "mqtt"}

    print()
    choice = input("Choice [1-3] (default: 1): ").strip() or "1"

    if choice not in source_map:
        print("  Invalid choice.")
        return

    source_type = source_map[choice]
    logger.info(f"Setting up {source_type} data source...")

    if source_type == "fastapi":
        port = int(os.environ.get("API_PORT", "8000"))
        source_ready = check_and_setup_source("fastapi", port=port)
    elif source_type == "mqtt":
        broker = os.environ.get("MQTT_BROKER", "localhost")
        mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        source_ready = check_and_setup_source("mqtt", broker=broker, mqtt_port=mqtt_port)
    else:
        source_ready = check_and_setup_source("direct")

    if not source_ready:
        print(f"\n  ✗ Could not set up {source_type} data source.")
        return

    print()
    print("=== Launch Summary ===")
    print(f"Tools: {', '.join(tool_names)}")
    print(f"Data source: {source_type}")
    print()

    if input("Launch? (Y/n): ").strip().lower() in ("n", "no"):
        print("Aborted.")
        return

    os.environ["BENCHLAB_DATA_SOURCE"] = source_type

    try:
        if is_multi:
            launch_tools_concurrent(tool_ids)
        else:
            launch_single_tool(tool_ids[0])
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        cleanup_all_services()


# ──────────────────────────────────────────────────────────────
# Main Interactive Loop
# ──────────────────────────────────────────────────────────────

def interactive_loop() -> None:
    """Drive the top-level interactive menu until the user quits."""
    clear_screen()
    print_banner()

    while True:
        try:
            mode = show_step1_menu()
            if mode is None:
                print("Goodbye!")
                cleanup_all_services()
                return

            if mode == "provider":
                step2_data_provider()
            elif mode == "single":
                step2_single_tool()
            elif mode == "multi":
                step2_multi_tool()

            input("\n  Press Enter to continue... ")
            clear_screen()

        except (EOFError, KeyboardInterrupt):
            print("Goodbye!")
            cleanup_all_services()
            return