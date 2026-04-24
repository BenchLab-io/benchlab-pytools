"""BENCHLAB PyTools v2 – Main Launcher.

This module implements the command-line entry point for the Benchlab
telemetry suite. The workflow is split into three steps:

1. **Select a data source** – FastAPI, MQTT, or direct serial access.
2. **Choose consumer tools** – one or many tools that will read from the
   selected source.
3. **Launch** – start the source (if needed) and then launch the selected
   tools, handling cleanup on exit.

The launcher relies on :class:`benchlab.core.process_manager.ProcessManager`
to start and monitor auxiliary services and on
:class:`benchlab.core.device_registry.DeviceRegistry` to keep track of
discovered devices.
"""

import argparse
import curses
import logging
import os
import sys
import traceback

from .tools import CONSUMER_TOOLS, LAUNCH_PROFILES
from .sources import check_and_setup_source, cleanup_all_services
from .launcher import launch_tools_concurrent
from .menu import interactive_loop

logger = logging.getLogger("benchlab.launcher")


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
    parser.add_argument("-link", action="store_true",
                        help="Run cloud MQTT link publisher")
    parser.add_argument("--remote-host", default=None, dest="remote_host",
                        help="Cloud MQTT broker hostname (overrides LINK_REMOTE_HOST)")
    parser.add_argument("--remote-port", type=int, default=None, dest="remote_port",
                        help="Cloud MQTT broker port (default: 8883)")
    parser.add_argument("--remote-user", default=None, dest="remote_user",
                        help="Cloud MQTT username (overrides LINK_REMOTE_USER)")
    parser.add_argument("--remote-pass", default=None, dest="remote_pass",
                        help="Cloud MQTT password (overrides LINK_REMOTE_PASS)")
    parser.add_argument("--no-tls", action="store_true", dest="no_tls",
                        help="Disable TLS for cloud MQTT connection")
    parser.add_argument("--topic-pattern", default=None, dest="topic_pattern",
                        help="MQTT topic pattern with {uid} token (overrides LINK_TOPIC_PATTERN)")
    parser.add_argument("-tui", action="store_true",
                        help="Enable TUI (default)")
    parser.add_argument("--source",
                        help="Data source: direct | fastapi | mqtt",
                        choices=["direct", "fastapi", "mqtt"],
                        default=None,
                        metavar="SOURCE")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000",
                        dest="api_url",
                        help="FastAPI base URL (default: http://127.0.0.1:8000)")
    parser.add_argument("--api-port", type=int, default=8000,
                        dest="api_port",
                        help="FastAPI port (default: 8000)")
    parser.add_argument("--mqtt-broker", default="localhost",
                        dest="mqtt_broker",
                        help="MQTT broker host (default: localhost)")
    parser.add_argument("--mqtt-port", type=int, default=1883,
                        dest="mqtt_port",
                        help="MQTT broker port (default: 1883)")
    parser.add_argument("-vu", action="store_true",
                        help="Launch VU analog dials")
    parser.add_argument("-vuconfig", action="store_true",
                        help="Launch VU configuration interface")
    parser.add_argument("-wigidash", action="store_true",
                        help="Connect to WigiDash")
    parser.add_argument("-xeneon", action="store_true",
                        help="Launch Xeneon web dashboard")
    parser.add_argument("--profile",
                        help="Launch predefined multi-tool profile",
                        default=None)

    return parser


# ──────────────────────────────────────────────────────────────
# Source Setup from CLI Args
# ──────────────────────────────────────────────────────────────

def _setup_source_from_args(args) -> bool:
    """Resolve and start the data source requested via --source (or env fallback).

    Sets BENCHLAB_DATA_SOURCE (and related env vars) so that any tool that
    reads from the environment will pick up the correct source.

    Returns True if the source is ready, False on failure.
    """
    source = args.source or os.environ.get("BENCHLAB_DATA_SOURCE", "direct")
    logger.info(f"Setting up data source: {source}")

    if source == "fastapi":
        port = getattr(args, "api_port", None) or int(os.environ.get("API_PORT", "8000"))
        os.environ["API_PORT"] = str(port)
        os.environ["BENCHLAB_API_URL"] = getattr(args, "api_url", f"http://127.0.0.1:{port}")
        ready = check_and_setup_source("fastapi", port=port)
    elif source == "mqtt":
        broker = getattr(args, "mqtt_broker", None) or os.environ.get("MQTT_BROKER", "localhost")
        mqtt_port = getattr(args, "mqtt_port", None) or int(os.environ.get("MQTT_PORT", "1883"))
        os.environ["MQTT_BROKER"] = broker
        os.environ["MQTT_PORT"] = str(mqtt_port)
        ready = check_and_setup_source("mqtt", broker=broker, mqtt_port=mqtt_port)
    else:
        ready = check_and_setup_source("direct")

    if ready:
        os.environ["BENCHLAB_DATA_SOURCE"] = source
    else:
        logger.error(f"Could not set up '{source}' data source.")
    return ready


# ──────────────────────────────────────────────────────────────
# Per-tool CLI Dispatch Helpers
# ──────────────────────────────────────────────────────────────

def _run_with_source(args, import_path: str, func_name: str, call_fn, tool_label: str) -> None:
    """Set up source, call call_fn, then clean up."""
    if not _setup_source_from_args(args):
        return
    try:
        mod = __import__(import_path, fromlist=[func_name])
        call_fn(getattr(mod, func_name))
    except ModuleNotFoundError:
        traceback.print_exc()
        print(f"{tool_label} module not available in this build.")
    finally:
        cleanup_all_services()


# ──────────────────────────────────────────────────────────────
# CLI Entry Point
# ──────────────────────────────────────────────────────────────

def launch_mode() -> None:
    """Parse CLI arguments and dispatch to the appropriate mode."""
    parser = get_parser()
    args = parser.parse_args()

    no_flags = not any([
        args.fastapi, args.graph, args.hwinfo, args.link, args.logfleet,
        args.mqtt, args.tui, args.vu, args.vuconfig,
        args.wigidash, args.xeneon, args.profile,
    ])
    if no_flags:
        interactive_loop()
        return

    # ── Profile ──────────────────────────────────────────────
    if args.profile:
        profile = LAUNCH_PROFILES.get(args.profile)
        if not profile:
            print(f"Unknown profile: {args.profile}")
            return

        print(f"Launching profile: {args.profile}")
        profile_args = argparse.Namespace(
            source=profile.get("source", "direct"),
            api_url=getattr(args, "api_url", "http://127.0.0.1:8000"),
            api_port=getattr(args, "api_port", 8000),
            mqtt_broker=getattr(args, "mqtt_broker", "localhost"),
            mqtt_port=getattr(args, "mqtt_port", 1883),
        )
        if not _setup_source_from_args(profile_args):
            print("Failed to initialize data source")
            return
        os.environ["BENCHLAB_DATA_SOURCE"] = profile.get("source", "direct")
        launch_tools_concurrent(profile["tools"])
        return

    # ── Individual tool flags ─────────────────────────────────
    if args.fastapi:
        try:
            from benchlab.restapi.telemetry_api import run_server
            run_server()
        except ModuleNotFoundError:
            print("FastAPI / Uvicorn not available in this build.")

    elif args.mqtt:
        try:
            from benchlab.mqtt.mqtt_publisher import run_mqtt_mode
            run_mqtt_mode(args.mqtt if args.mqtt else "localhost")
        except ModuleNotFoundError:
            print("MQTT module not available in this build.")

    elif args.link:
        _run_with_source(args,
                         "benchlab.link.link_main", "run_link",
                         lambda fn: fn(args), "Link")

    elif args.graph:
        _run_with_source(args,
                         "benchlab.graph.runner", "run_graph_mode",
                         lambda fn: fn(args), "Graph")

    elif args.hwinfo:
        _run_with_source(args,
                         "benchlab.hwinfo.hwinfo_export", "export_all_devices",
                         lambda fn: fn(update_interval=args.interval), "HWiNFO export")

    elif args.logfleet:
        _run_with_source(args,
                         "benchlab.csv_log.csv_logger_enhanced", "run_enhanced_csv_logger",
                         lambda fn: fn(args), "Enhanced CSV logger")

    elif args.vu:
        _run_with_source(args,
                         "benchlab.vu.vu_updater", "run_updater",
                         lambda fn: fn(args), "VU")

    elif args.vuconfig:
        _run_with_source(args,
                         "benchlab.vu.vu_tui", "launch_vu_config",
                         lambda fn: fn(args), "VU configuration")

    elif args.wigidash:
        _run_with_source(args,
                         "benchlab.wigidash.wigidash_manager", "main",
                         lambda fn: fn(args), "WigiDash")

    elif args.xeneon:
        _run_with_source(args,
                         "benchlab.xeneon.xeneon_main", "run_xeneon",
                         lambda fn: fn(args), "Xeneon dashboard")

    elif args.tui:
        if not _setup_source_from_args(args):
            return
        try:
            from benchlab.tui.tui_main import tui_main
            curses.wrapper(tui_main, None, args)
        except KeyboardInterrupt:
            pass
        except ModuleNotFoundError:
            logger.error("TUI module not available in this build.")
        finally:
            cleanup_all_services()

    else:
        logger.info("No specific mode flags provided; launching interactive loop.")
        interactive_loop()


def main() -> None:
    """Entry point."""
    launch_mode()


if __name__ == "__main__":
    main()