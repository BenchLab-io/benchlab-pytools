"""
Multi-tool launcher utilities for BENCHLAB

Provides interactive menu system for selecting tools and data sources,
and orchestrates launching multiple tools simultaneously.
"""

import importlib
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("benchlab.core.launcher")


class DataSourceType(str, Enum):
    """Data source types supported by the launcher."""
    DIRECT = "direct"
    FASTAPI = "fastapi"
    MQTT = "mqtt"


@dataclass
class ToolRunConfig:
    """Configuration for running a tool with data source info."""
    tool_id: str
    tool_config: Dict[str, Any]
    data_source: str
    data_source_config: Dict[str, Any] = field(default_factory=dict)
    thread: Optional[threading.Thread] = None
    stop_event: threading.Event = field(default_factory=threading.Event)


# Tool definitions with metadata
TOOLS = {
    "csv_log": {
        "name": "CSV Logger",
        "description": "Log device telemetry to CSV files",
        "flag": "-logfleet",
        "module": "benchlab.csv_log.csv_logger_enhanced",
        "function": "run_enhanced_csv_logger",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
    },
    "fastapi": {
        "name": "FastAPI Server",
        "description": "REST API server for telemetry data",
        "flag": "-fastapi",
        "module": "benchlab.restapi.telemetry_api",
        "function": "run_server",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
        "is_infrastructure": True,  # Can be a data source for other tools
    },
    "graph": {
        "name": "DearPyGui Graph",
        "description": "Interactive sensor graphing with DearPyGui",
        "flag": "-graph",
        "module": "benchlab.graph.runner",
        "function": "run_graph_mode",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86"],  # Not available on ARM
    },
    "hwinfo": {
        "name": "HWiNFO Export",
        "description": "Export sensors to HWiNFO64 custom sensors",
        "flag": "-hwinfo",
        "module": "benchlab.hwinfo.hwinfo_export",
        "function": "export_all_devices",
        "platforms": ["windows"],  # Windows only
        "architectures": ["x86"],
    },
    "mqtt": {
        "name": "MQTT Publisher",
        "description": "Publish telemetry to MQTT broker",
        "flag": "-mqtt",
        "module": "benchlab.mqtt.mqtt_publisher",
        "function": "run_mqtt_mode",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
        "is_infrastructure": True,  # Can be a data source for other tools
    },
    "tui": {
        "name": "TUI",
        "description": "Interactive terminal user interface",
        "flag": "-tui",
        "module": "benchlab.tui.tui_main",
        "function": "tui_main",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
    },
    "vu": {
        "name": "VU Dials",
        "description": "Analog-style VU meter dials",
        "flag": "-vu",
        "module": "benchlab.vu.vu_updater",
        "function": "run_updater",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
    },
    "vuconfig": {
        "name": "VU Config",
        "description": "VU meter configuration interface",
        "flag": "-vuconfig",
        "module": "benchlab.vu.vu_tui",
        "function": "launch_vu_config",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
    },
    "wigidash": {
        "name": "WigiDash",
        "description": "Display telemetry on WigiDash device",
        "flag": "-wigidash",
        "module": "benchlab.wigidash.wigidash_manager",
        "function": "main",
        "platforms": ["windows", "linux", "mac"],
        "architectures": ["x86", "arm"],
    },
}


def get_platform_info() -> Dict[str, str]:
    """Get current platform information.
    
    Returns:
        Dict with 'os' and 'arch' keys
    """
    import platform
    import sys
    
    is_windows = sys.platform.startswith('win')
    is_linux = sys.platform.startswith('linux')
    is_mac = sys.platform.startswith('darwin')
    
    current_os = "windows" if is_windows else "linux" if is_linux else "mac"
    arch = platform.machine().lower()
    current_arch = "arm" if arch.startswith("arm") or arch.startswith("aarch") else "x86"
    
    return {"os": current_os, "arch": current_arch}


def is_tool_available(tool_id: str) -> bool:
    """Check if a tool is available on the current platform.
    
    Args:
        tool_id: Tool identifier (e.g., 'tui', 'hwinfo')
        
    Returns:
        True if tool is available
    """
    if tool_id not in TOOLS:
        return False
    
    tool = TOOLS[tool_id]
    platform_info = get_platform_info()
    
    # Check platform
    if "platforms" in tool and platform_info["os"] not in tool["platforms"]:
        return False
    
    # Check architecture
    if "architectures" in tool and platform_info["arch"] not in tool["architectures"]:
        return False
    
    return True


def get_available_tools() -> List[str]:
    """Get list of available tool IDs.
    
    Returns:
        List of tool IDs that are available on current platform
    """
    return [tool_id for tool_id in TOOLS if is_tool_available(tool_id)]


def get_tool_by_flag(flag: str) -> Optional[str]:
    """Get tool ID by its command-line flag.
    
    Args:
        flag: Command-line flag (e.g., '-tui')
        
    Returns:
        Tool ID if found, None otherwise
    """
    for tool_id, tool in TOOLS.items():
        if tool["flag"] == flag:
            return tool_id
    return None


def select_tools_interactive() -> List[str]:
    """Interactive tool selection menu.
    
    Returns:
        List of selected tool IDs
    """
    available = get_available_tools()
    non_infrastructure = [t for t in available if not TOOLS[t].get("is_infrastructure", False)]
    
    if not non_infrastructure:
        print("No tools available on this platform.")
        return []
    
    print("\n=== Select Tools ===")
    print("Use space to toggle, Enter to confirm, 'a' for all, 'n' for none")
    print()
    
    selected = set()
    
    while True:
        # Display menu
        for i, tool_id in enumerate(non_infrastructure, 1):
            tool = TOOLS[tool_id]
            marker = "[x]" if tool_id in selected else "[ ]"
            print(f"  {i}. {marker} {tool['name']} - {tool['description']}")
        
        print()
        print("  a - Select all")
        print("  n - Select none")
        print("  <number> - Toggle specific tool")
        print("  Enter - Confirm selection")
        print()
        
        choice = input("Selection: ").strip().lower()
        
        if not choice or choice == "":
            # Enter pressed - confirm
            if selected:
                break
            else:
                print("Please select at least one tool.")
                continue
        elif choice == "a":
            selected = set(non_infrastructure)
        elif choice == "n":
            selected = set()
        else:
            # Try to parse as number
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(non_infrastructure):
                    tool_id = non_infrastructure[idx]
                    if tool_id in selected:
                        selected.remove(tool_id)
                    else:
                        selected.add(tool_id)
            except ValueError:
                pass
        
        # Clear screen for next iteration (optional)
        # os.system('cls' if os.name == 'nt' else 'clear')
        print()
    
    return list(selected)


def select_data_source(tool_count: int) -> str:
    """Select data source based on number of tools.
    
    Args:
        tool_count: Number of tools to run
        
    Returns:
        Data source type ('direct', 'fastapi', or 'mqtt')
    """
    if tool_count == 1:
        # Single tool - use direct by default
        print("\nSingle tool selected. Data source: Direct (pycore)")
        return "direct"
    
    # Multiple tools - ask user
    print("\n=== Select Data Source ===")
    print("Multiple tools selected. Choose how they should share data:")
    print()
    print("  1. FastAPI (recommended) - Tools connect to a local REST API server")
    print("  2. MQTT - Tools subscribe to MQTT topics")
    print("  3. Direct - Each tool connects directly (NOT recommended for multiple tools)")
    print()
    
    while True:
        choice = input("Choice [1-3] (default: 1): ").strip()
        
        if not choice or choice == "1":
            return "fastapi"
        elif choice == "2":
            return "mqtt"
        elif choice == "3":
            print("Warning: Direct mode with multiple tools may cause serial port conflicts.")
            confirm = input("Continue anyway? (y/N): ").strip().lower()
            if confirm in ("y", "yes"):
                return "direct"
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")


def create_tool_config(tools: List[str], data_source: str) -> List[Dict[str, Any]]:
    """Create tool configuration for launcher.
    
    Args:
        tools: List of tool IDs
        data_source: Data source type
        
    Returns:
        List of tool configuration dictionaries
    """
    configs = []
    for tool_id in tools:
        configs.append({
            "id": tool_id,
            "name": TOOLS[tool_id]["name"],
            "flag": TOOLS[tool_id]["flag"],
            "source": data_source,
            "module": TOOLS[tool_id]["module"],
            "function": TOOLS[tool_id]["function"],
        })
    return configs


def print_launch_summary(tool_configs: List[Dict[str, Any]], data_source: str) -> None:
    """Print summary of what will be launched.
    
    Args:
        tool_configs: List of tool configurations
        data_source: Data source type
    """
    print("\n=== Launch Summary ===")
    print()
    print(f"Tools to launch ({len(tool_configs)}):")
    for config in tool_configs:
        print(f"  - {config['name']} ({config['flag']})")
    print()
    print(f"Data source: {data_source}")
    if data_source == "fastapi":
        print("  → FastAPI server will be started on port 8000")
    elif data_source == "mqtt":
        print("  → MQTT publisher will be started")
    else:
        print("  → Each tool will connect directly to serial port")
    print()


def run_tool_in_thread(run_config: ToolRunConfig) -> None:
    """Run a single tool in a background thread with data source config.
    
    This is the internal worker that actually executes the tool.
    The stop_event can be set to signal the tool to shutdown gracefully.
    """
    tool_config = run_config.tool_config
    data_source = run_config.data_source
    
    try:
        module = importlib.import_module(tool_config["module"])
        func = getattr(module, tool_config["function"])
        tool_name = tool_config.get("name", tool_config["id"])
        
        # Set environment variable for data source type
        os.environ["BENCHLAB_DATA_SOURCE"] = data_source
        
        logger.info(f"Starting tool: {tool_name} (data_source={data_source})")
        
        # Call tool function with appropriate parameters based on tool type
        if tool_config["id"] == "csv_log":
            # CSV logger - pass interval and data source info
            func(
                interval=run_config.data_source_config.get("interval", 1.0),
                data_source=data_source
            )
        elif tool_config["id"] == "mqtt":
            # MQTT publisher - pass broker and data source config
            broker = run_config.data_source_config.get("broker", "localhost")
            func(broker_type=broker)
        elif tool_config["id"] == "tui":
            # TUI - use curses wrapper
            import curses
            # Pass args namespace with data source info
            import types
            args = types.SimpleNamespace()
            args.source = data_source
            args.port = run_config.data_source_config.get("port", None)
            args.interval = run_config.data_source_config.get("interval", 1.0)
            func(stdscr=None, _unused=None, args=args)
        elif tool_config["id"] == "fastapi":
            # FastAPI server (infrastructure tool)
            func()
        else:
            # Default: call with no arguments
            func()
            
    except Exception as e:
        logger.error(f"Tool {tool_config.get('name', tool_config['id'])} failed: {e}")
    finally:
        logger.info(f"Tool {tool_config.get('name', tool_config['id'])} stopped")


def launch_tool_concurrent(run_config: ToolRunConfig) -> threading.Thread:
    """Launch a tool in a background thread.
    
    Args:
        run_config: ToolRunConfig with all necessary parameters
        
    Returns:
        The thread running the tool
    """
    thread = threading.Thread(
        target=run_tool_in_thread,
        args=(run_config,),
        daemon=True,
        name=f"Tool-{run_config.tool_config.get('name', 'unknown')}"
    )
    thread.start()
    return thread


def launch_tool(tool_config: Dict[str, Any], data_source_config: Optional[Dict[str, Any]] = None):
    """Launch a single tool (blocking, for backward compatibility).
    
    Args:
        tool_config: Tool configuration dictionary
        data_source_config: Optional data source configuration
    
    Note: This function blocks until the tool exits. Use launch_tool_concurrent()
          for non-blocking concurrent execution.
    """
    run_config = ToolRunConfig(
        tool_id=tool_config["id"],
        tool_config=tool_config,
        data_source=tool_config.get("source", "direct"),
        data_source_config=data_source_config or {}
    )
    
    thread = launch_tool_concurrent(run_config)
    # Wait for thread to complete (backward compatible blocking behavior)
    try:
        thread.join()
    except KeyboardInterrupt:
        logger.info(f"Interrupted tool: {tool_config.get('name', 'unknown')}")
        run_config.stop_event.set()
        thread.join(timeout=5)


def run_interactive_launcher() -> None:
    """Run the full interactive launcher flow.
    
    This is the main entry point for the interactive multi-tool launcher.
    Launches all selected tools in concurrent threads.
    """
    from benchlab.core.infrastructure import InfrastructureManager
    
    # Step 1: Select tools
    selected_tools = select_tools_interactive()
    if not selected_tools:
        print("No tools selected. Exiting.")
        return
    
    # Step 2: Select data source
    data_source = select_data_source(len(selected_tools))
    
    # Step 3: Create configurations
    tool_configs = create_tool_config(selected_tools, data_source)
    
    # Step 4: Print summary and confirm
    print_launch_summary(tool_configs, data_source)
    confirm = input("Launch tools? (Y/n): ").strip().lower()
    if confirm in ("n", "no"):
        print("Aborted.")
        return
    
    # Step 5: Start infrastructure if needed
    infra = InfrastructureManager()
    running_threads: List[Dict[str, Any]] = []
    
    try:
        if data_source in ("fastapi", "mqtt"):
            print("\nStarting infrastructure...")
            tools_for_infra = [{"source": data_source}]
            if not infra.start_all(tools_for_infra):
                print("Failed to start infrastructure. Exiting.")
                return
            
            # Give infrastructure time to initialize
            print("Waiting for infrastructure to initialize...")
            time.sleep(2)
            print("Infrastructure started.")
        
        # Step 6: Launch all tools concurrently
        print("\nLaunching tools...")
        print("Press Ctrl+C to stop all tools.\n")
        
        # Build data source config for tools
        data_source_config = {}
        if data_source == "fastapi":
            data_source_config = {
                "base_url": "http://localhost:8000",
                "interval": 1.0
            }
        elif data_source == "mqtt":
            data_source_config = {
                "broker": "localhost",
                "port": 1883,
                "interval": 1.0
            }
        else:
            data_source_config = {"interval": 1.0}
        
        # Launch each tool in its own thread
        for config in tool_configs:
            run_config = ToolRunConfig(
                tool_id=config["id"],
                tool_config=config,
                data_source=data_source,
                data_source_config=data_source_config
            )
            thread = launch_tool_concurrent(run_config)
            running_threads.append({
                "config": config,
                "run_config": run_config,
                "thread": thread
            })
            print(f"  Started: {config['name']} (PID: {thread.ident})")
        
        print(f"\n  Running {len(running_threads)} tool(s) concurrently.")
        print("  Press Ctrl+C to stop all.\n")
        
        # Wait for all threads (with Ctrl+C handling)
        while True:
            # Check for completed threads
            alive_count = 0
            for item in running_threads:
                if item["thread"].is_alive():
                    alive_count += 1
            
            if alive_count == 0:
                print("\nAll tools have completed.")
                break
            
            # Short sleep to allow interrupt
            time.sleep(0.5)
                
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Stopping all tools...")
    finally:
        print("\nCleaning up...")
        
        # Signal all tools to stop
        for item in running_threads:
            item["run_config"].stop_event.set()
        
        # Wait for threads to finish (with timeout)
        for item in running_threads:
            item["thread"].join(timeout=5)
            if item["thread"].is_alive():
                print(f"  Warning: {item['config']['name']} did not stop gracefully")
        
        # Stop infrastructure
        infra.stop_all()
        print("Done.")
