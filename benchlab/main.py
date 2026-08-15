"""BENCHLAB PyTools v2 – Main Launcher.

This module implements the command-line entry point for the Benchlab
telemetry suite. The workflow is split into three steps:

1. **Select a data source** – FastAPI, MQTT, direct serial, named pipe, or service HTTP.
2. **Choose consumer tools** – one or many tools that will read from the selected source.
3. **Launch** – start the source (if needed) and then launch the selected tools,
   handling cleanup on exit.
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

logger = logging.getLogger("benchlab.launcher")

try:
    from .menu import interactive_loop
except ImportError as e:
    # prompt_toolkit isn't installed (or failed to import for some other
    # reason) — fall back to the plain numbered-input menu rather than
    # crashing the whole interactive entry point.
    logger.debug(
        f"Falling back to classic menu (prompt_toolkit unavailable: {e})")
    from .menu_classic import interactive_loop

# ──────────────────────────────────────────────────────────────
# Argument Parser
# ──────────────────────────────────────────────────────────────


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BENCHLAB PyTools v2 - Device Telemetry Suite"
    )

    parser.add_argument("-config", action="store_true",
                        help="Device configuration import/export tool")
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
    parser.add_argument(
        "--remote-host",
        default=None,
        dest="remote_host",
        help="Cloud MQTT broker hostname (overrides LINK_REMOTE_HOST)")
    parser.add_argument(
        "--remote-port",
        type=int,
        default=None,
        dest="remote_port",
        help="Cloud MQTT broker port (default: 8883)")
    parser.add_argument(
        "--remote-user",
        default=None,
        dest="remote_user",
        help="Cloud MQTT username (overrides LINK_REMOTE_USER)")
    parser.add_argument(
        "--remote-pass",
        default=None,
        dest="remote_pass",
        help="Cloud MQTT password (overrides LINK_REMOTE_PASS)")
    parser.add_argument("--no-tls", action="store_true", dest="no_tls",
                        help="Disable TLS for cloud MQTT connection")
    parser.add_argument(
        "--topic-pattern",
        default=None,
        dest="topic_pattern",
        help="MQTT topic pattern with {uid} token (overrides LINK_TOPIC_PATTERN)")
    parser.add_argument("-tui", action="store_true",
                        help="Enable TUI (default)")
    parser.add_argument(
        "--source",
        help="Data source: direct | fastapi | fastapi_custom | mqtt | mqtt_custom | named_pipe | service_http",
        choices=[
            "direct",
            "fastapi",
            "fastapi_custom",
            "mqtt",
            "mqtt_custom",
            "named_pipe",
            "service_http"],
        default=None,
        metavar="SOURCE")
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
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
    parser.add_argument(
        "--service-url",
        default="http://localhost:8585",
        dest="service_url",
        help="C# BenchLab service HTTP API URL (default: http://localhost:8585)")
    parser.add_argument("-vu", action="store_true",
                        help="Launch VU analog dials")
    parser.add_argument("-vuconfig", action="store_true",
                        help="Launch VU configuration interface")
    parser.add_argument("-wigidash", action="store_true",
                        help="Connect to WigiDash")
    parser.add_argument("--profile",
                        help="Launch predefined multi-tool profile",
                        default=None)

    return parser


# ──────────────────────────────────────────────────────────────
# Source Setup from CLI Args
# ──────────────────────────────────────────────────────────────

def _setup_source_from_args(args) -> bool:
    """Resolve and start the data source requested via --source (or env fallback).

    Returns True if the source is ready, False on failure.
    """
    source = args.source or os.environ.get("BENCHLAB_DATA_SOURCE", "direct")
    logger.info(f"Setting up data source: {source}")

    if source == "fastapi":
        port = getattr(
            args,
            "api_port",
            None) or int(
            os.environ.get(
                "API_PORT",
                "8000"))
        os.environ["API_PORT"] = str(port)
        os.environ["BENCHLAB_API_URL"] = getattr(
            args, "api_url", f"http://127.0.0.1:{port}")
        ready = check_and_setup_source("fastapi", port=port)

    elif source == "fastapi_custom":
        api_url = getattr(
            args,
            "api_url",
            os.environ.get(
                "BENCHLAB_API_URL",
                "http://127.0.0.1:8000"))
        os.environ["BENCHLAB_API_URL"] = api_url
        ready = check_and_setup_source("fastapi_custom", base_url=api_url)

    elif source == "mqtt":
        broker = getattr(
            args,
            "mqtt_broker",
            None) or os.environ.get(
            "MQTT_BROKER",
            "localhost")
        mqtt_port = getattr(
            args,
            "mqtt_port",
            None) or int(
            os.environ.get(
                "MQTT_PORT",
                "1883"))
        os.environ["MQTT_BROKER"] = broker
        os.environ["MQTT_PORT"] = str(mqtt_port)
        ready = check_and_setup_source(
            "mqtt", broker=broker, mqtt_port=mqtt_port)

    elif source == "mqtt_custom":
        broker = getattr(
            args,
            "mqtt_broker",
            None) or os.environ.get(
            "MQTT_BROKER",
            "localhost")
        mqtt_port = getattr(
            args,
            "mqtt_port",
            None) or int(
            os.environ.get(
                "MQTT_PORT",
                "1883"))
        os.environ["MQTT_BROKER"] = broker
        os.environ["MQTT_PORT"] = str(mqtt_port)
        ready = check_and_setup_source(
            "mqtt_custom", broker=broker, mqtt_port=mqtt_port)

    elif source == "named_pipe":
        ready = check_and_setup_source("named_pipe")

    elif source == "service_http":
        service_url = getattr(args, "service_url", None) or os.environ.get(
            "BENCHLAB_SERVICE_URL", "http://localhost:8585"
        )
        os.environ["BENCHLAB_SERVICE_URL"] = service_url
        # Parse host/port from URL for the check
        import urllib.parse
        parsed = urllib.parse.urlparse(service_url)
        ready = check_and_setup_source(
            "service_http",
            host=parsed.hostname or "localhost",
            port=parsed.port or 8585,
        )

    else:
        ready = check_and_setup_source("direct")

    if ready:
        os.environ["BENCHLAB_DATA_SOURCE"] = source
    else:
        logger.error(f"Could not set up '{source}' data source.")
    return ready


def _export_link_env(args) -> None:
    """Mirror --remote-*/--no-tls/--topic-pattern CLI flags into env vars.

    link_main.py's _resolve_config already falls back to these env vars
    (REMOTE_MQTT_HOST etc.) when no args object provides a value, so
    exporting here is what lets a spawned/multi-tool `link` process
    (launched via launcher.py's _spawn_tool_in_terminal, which parses
    fresh argv with none of these flags) still pick up the config the
    user passed on the parent process's command line.
    """
    mapping = {
        "remote_host": "REMOTE_MQTT_HOST",
        "remote_port": "REMOTE_MQTT_PORT",
        "remote_user": "REMOTE_MQTT_USER",
        "remote_pass": "REMOTE_MQTT_PASS",
        "topic_pattern": "LINK_TOPIC_PATTERN",
    }
    for attr, env_key in mapping.items():
        val = getattr(args, attr, None)
        if val is not None:
            os.environ[env_key] = str(val)
    if getattr(args, "no_tls", False):
        os.environ["REMOTE_MQTT_TLS"] = "false"


# ──────────────────────────────────────────────────────────────
# Per-tool CLI Dispatch Helpers
# ──────────────────────────────────────────────────────────────

def _run_with_source(
        args,
        import_path: str,
        func_name: str,
        call_fn,
        tool_label: str) -> None:
    """Set up source, call call_fn, then clean up."""
    from .tools import CONSUMER_TOOLS, ensure_tool_dependencies
    tool_id = next((tid for tid, t in CONSUMER_TOOLS.items()
                    if t["module"] == import_path), None)
    if tool_id:
        try:
            ensure_tool_dependencies(tool_id)
        except Exception as e:
            logger.warning(
                f"Failed to install dependencies for {tool_label}: {e}")

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

    # Check if -config is in args - if so, use parse_known_args to allow
    # config tool args through
    if '-config' in sys.argv:
        args, unknown = parser.parse_known_args()
    else:
        args = parser.parse_args()

    no_flags = not any([args.config,
                        args.fastapi,
                        args.graph,
                        args.hwinfo,
                        args.link,
                        args.logfleet,
                        args.mqtt,
                        args.tui,
                        args.vu,
                        args.vuconfig,
                        args.wigidash,
                        args.profile,
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

        from .tools import ensure_profile_dependencies
        ensure_profile_dependencies(args.profile)

        profile_args = argparse.Namespace(
            source=profile.get("source", "direct"),
            api_url=getattr(args, "api_url", "http://127.0.0.1:8000"),
            api_port=getattr(args, "api_port", 8000),
            mqtt_broker=getattr(args, "mqtt_broker", "localhost"),
            mqtt_port=getattr(args, "mqtt_port", 1883),
            service_url=getattr(args, "service_url", "http://localhost:8585"),
        )
        if not _setup_source_from_args(profile_args):
            print("Failed to initialize data source")
            return
        os.environ["BENCHLAB_DATA_SOURCE"] = profile.get("source", "direct")
        launch_tools_concurrent(profile["tools"])
        return

    # ── Individual tool flags ─────────────────────────────────
    if args.config:
        # Config tool handles its own argument parsing
        # Remove -config from sys.argv so config tool can parse remaining args
        try:
            sys.argv.remove('-config')
            from benchlab.config.config_tool import main as config_main
            sys.exit(config_main())
        except ModuleNotFoundError:
            print("Config tool module not available in this build.")

    elif args.fastapi:
        try:
            from benchlab.restapi.telemetry_api import run_server
            run_server()
        except ModuleNotFoundError:
            print("FastAPI / Uvicorn not available in this build.")

    elif args.mqtt:
        try:
            from benchlab.mqtt.mqtt_publisher import run_mqtt_mode
            broker = args.mqtt if args.mqtt else "localhost"
            os.environ.setdefault("MQTT_BROKER", broker)
            run_mqtt_mode(broker)
        except ModuleNotFoundError:
            print("MQTT module not available in this build.")

    elif args.link:
        _export_link_env(args)
        _run_with_source(args,
                         "benchlab.link.link_main", "run_link",
                         lambda fn: fn(args), "Link")

    elif args.graph:
        _run_with_source(args,
                         "benchlab.graph.runner", "run_graph_mode",
                         lambda fn: fn(args), "Graph")

    elif args.hwinfo:
        _run_with_source(
            args,
            "benchlab.hwinfo.hwinfo_export",
            "export_all_devices",
            lambda fn: fn(
                update_interval=args.interval),
            "HWiNFO export")

    elif args.logfleet:
        _run_with_source(
            args,
            "benchlab.csv_log.csv_logger_enhanced",
            "run_enhanced_csv_logger",
            lambda fn: fn(args),
            "Enhanced CSV logger")

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
        logger.info(
            "No specific mode flags provided; launching interactive loop.")
        interactive_loop()


def main() -> None:
    """Entry point."""
    launch_mode()


if __name__ == "__main__":
    main()
