"""BENCHLAB PyTools v2 – Interactive Menu System.

Implements the three-step interactive menu flow:
  1. Mode selection (provider / single tool / multi-tool)
  2. Tool selection
  3. Data source selection + launch confirmation
"""

import logging
import os
import sys
from typing import List, Optional

from .bootstrap import clear_screen
from .tools import CONSUMER_TOOLS
from .sources import (
    check_and_setup_source,
    check_mqtt_running,
    start_mqtt_broker,
    start_mqtt_source,
    cleanup_all_services,
    SERVICE_HTTP_DEFAULT_PORT,
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
    """Display mode selection.

    Returns 'provider', 'single', 'multi', or None.
    """
    print("What would you like to do?\n")
    print("  1. Data Provider   - Run FastAPI or MQTT server for other tools")
    print("  2. Single Tool     - Run one tool with a data source")
    print(
        "  3. Multi-Tool      - Run multiple tools with shared data "
        "(Experimental!)")
    print()
    print("  q. Quit")
    print()

    try:
        choice = input("Choice: ").strip().lower()
        return {"1": "provider", "2": "single", "3": "multi"}.get(choice) or (
            None if choice in ("q", "quit", "exit")
            else _invalid("Enter 1, 2, 3, or q.")
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
    """Select multiple tools and proceed to source selection.

    (Experimental!)
    """
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

def _build_source_menu(is_multi: bool,
                       supported_sources: Optional[List[str]] = None) -> dict:
    """Build the source selection menu.

    Args:
        is_multi: Whether multiple tools are being launched
        supported_sources: Optional list of source types to include
                          (e.g., ["direct", "named_pipe"])
                          If None, all sources are shown.

    Returns a dict mapping menu key → (label, source_type).
    All sources are always listed unless filtered by supported_sources.
    OS-unsupported ones get a note.
    """
    is_windows = sys.platform.startswith("win")
    os_name = "Windows" if is_windows else (
        "macOS" if sys.platform == "darwin" else "Linux")

    # Build all possible sources
    all_sources = []

    if not is_multi:
        all_sources.append(("Direct (serial port)", "direct"))

    all_sources.append(("FastAPI server (Python)", "fastapi"))
    all_sources.append(("FastAPI server (custom URL)", "fastapi_custom"))
    all_sources.append(("MQTT (Python, experimental)", "mqtt"))
    all_sources.append(("MQTT (custom)", "mqtt_custom"))

    pipe_note = "" if is_windows else f"  (not available on {os_name})"
    all_sources.append(
        (f"BenchLab service - named pipe{pipe_note}", "named_pipe"))
    all_sources.append(
        (f"BenchLab service - HTTP API (port {SERVICE_HTTP_DEFAULT_PORT})",
         "service_http"))

    # Filter by supported_sources if provided
    if supported_sources is not None:
        all_sources = [(label, src_type) for label, src_type in all_sources
                       if src_type in supported_sources]

    # Build numbered menu
    sources = {}
    for key, (label, src_type) in enumerate(all_sources, 1):
        sources[str(key)] = (label, src_type)

    return sources


def step3_select_source(tool_ids: List[str], tool_names: List[str]) -> None:
    """Select data source, verify/start it, confirm, then launch tools."""
    is_multi = len(tool_ids) > 1
    print()
    print("=== Data Source ===")

    if is_multi:
        print(f"Tools: {', '.join(tool_names)}")
        print("  Note: Direct mode is not available for multi-tool")
        print("  because the serial port can only be used by one process.")
    else:
        print(f"Tool: {tool_names[0]}")

    print()

    # Get supported sources for single tool (if specified)
    supported_sources = None
    if not is_multi and len(tool_ids) == 1:
        tool_info = CONSUMER_TOOLS.get(tool_ids[0])
        if tool_info:
            supported_sources = tool_info.get("supported_sources")

    sources = _build_source_menu(is_multi, supported_sources)

    for key, (label, _) in sorted(sources.items()):
        print(f"  {key}. {label}")

    print()
    default_key = min(sources.keys())
    choice = input(f"Choice (default: {default_key}): ").strip() or default_key

    if choice not in sources:
        print("  Invalid choice.")
        return

    label, source_type = sources[choice]

    logger.info(f"Setting up {source_type} data source...")

    # Build kwargs for check_and_setup_source
    setup_kwargs: dict = {}
    if source_type == "fastapi":
        port = int(os.environ.get("API_PORT", "8000"))
        setup_kwargs = {"port": port}
    elif source_type == "fastapi_custom":
        # Prompt for custom FastAPI server URL
        host = input("  Host/IP [127.0.0.1]: ").strip() or "127.0.0.1"
        port_input = input("  Port [8000]: ").strip()
        try:
            port = int(port_input) if port_input else 8000
        except ValueError:
            print("  Invalid port number.")
            return
        base_url = f"http://{host}:{port}"
        setup_kwargs = {"base_url": base_url}
        # Store for later use by datasource manager
        os.environ["BENCHLAB_FASTAPI_CUSTOM_URL"] = base_url
        print(f"  → Using FastAPI server at {base_url}")
    elif source_type == "mqtt":
        broker = os.environ.get("MQTT_BROKER", "localhost")
        mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        setup_kwargs = {"broker": broker, "mqtt_port": mqtt_port}
    elif source_type == "mqtt_custom":
        # Prompt for custom MQTT broker
        broker = input("  Broker host [localhost]: ").strip() or "localhost"
        port_input = input("  Broker port [1883]: ").strip()
        try:
            mqtt_port = int(port_input) if port_input else 1883
        except ValueError:
            print("  Invalid port number.")
            return
        setup_kwargs = {"broker": broker, "mqtt_port": mqtt_port}
        print(f"  → Using MQTT broker at {broker}:{mqtt_port}")
    elif source_type == "service_http":
        import urllib.parse
        svc_url = os.environ.get(
            "BENCHLAB_SERVICE_URL",
            f"http://localhost:{SERVICE_HTTP_DEFAULT_PORT}")
        parsed = urllib.parse.urlparse(svc_url)
        setup_kwargs = {"host": parsed.hostname or "localhost",
                        "port": parsed.port or SERVICE_HTTP_DEFAULT_PORT}

    source_ready = check_and_setup_source(source_type, **setup_kwargs)

    if not source_ready:
        print(f"\n  ✗ Could not set up {source_type} data source.")
        if source_type in ("named_pipe", "service_http"):
            print(
                "  → Start the BenchLab Windows service (BL_Service.exe) "
                "and try again.")
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
