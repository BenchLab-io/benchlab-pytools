"""
Refactored TUI Main Entry Point
Clean main entry point using the refactored architecture with separated
concerns:
- DataSourceManager handles all data source operations
- ChannelStats manages statistics
- TUICore handles all UI rendering
- Config centralizes configuration
This replaces the original monolithic tui_main.py with a much cleaner
implementation.
"""
import curses
import logging
import sys
import time
from typing import List, Dict, Any

from benchlab.tui.__init__ import __version__
from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.statistics import ChannelStats, create_stats_callback
from .tui_core import TUICore

logger = logging.getLogger("benchlab.tui.main")


class _DevNullWriter:
    """A file-like object that discards all output."""

    def write(self, s):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False


class _TUIStdoutRedirect:
    """Redirect stdout/stderr to prevent interference with curses display.

    The pycore library writes INFO messages directly to stdout which messes
    up the curses layout. This class redirects stdout/stderr to a file or
    /dev/null
    during TUI operation.
    """

    def __init__(self, log_file: str = None):
        self.log_file = log_file
        self._original_stdout = None
        self._original_stderr = None
        self._file_handle = None

    def __enter__(self):
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr

        if self.log_file:
            self._file_handle = open(self.log_file, 'a')
            sys.stdout = self._file_handle
            sys.stderr = self._file_handle
        else:
            # Discard all output
            sys.stdout = _DevNullWriter()
            sys.stderr = _DevNullWriter()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file_handle:
            self._file_handle.flush()
            self._file_handle.close()

        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        return False


def get_default_datasource(args) -> str:
    """Get default data source based on command-line arguments."""
    if hasattr(args, 'source') and args.source:
        return args.source
    return 'direct'


def convert_fleet_format(
        devices_dict: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert DataSourceManager device dict to fleet list format."""
    fleet = []
    for uid, device_info in devices_dict.items():
        fleet.append({
            'uid': uid,
            'port': device_info.get('port', 'unknown'),
            'firmware': device_info.get(
                'firmware', device_info.get('FwVersion', 0)),
            'variant': device_info.get('variant'),
            'ProductId': device_info.get('ProductId'),
        })
    return sorted(fleet, key=lambda d: d["port"])


class TUIApplication:
    """
    Main TUI application class that coordinates between DataSourceManager,
    statistics tracking, and UI rendering.
    """

    def __init__(self, args):
        self.args = args
        self.source_type = get_default_datasource(args)

        self.stats = ChannelStats()
        stats_callback = create_stats_callback(self.stats)

        datasource_kwargs = {
            'poll_interval': getattr(args, 'interval', 1.0),
        }

        if self.source_type == 'fastapi':
            if hasattr(args, 'api_port'):
                datasource_kwargs['base_url'] = (
                    f"http://127.0.0.1:{args.api_port}")
            else:
                datasource_kwargs['base_url'] = "http://127.0.0.1:8000"
            datasource_kwargs['timeout'] = 5.0

        elif self.source_type == 'fastapi_custom':
            # Use the custom URL from args.api_url (set from BENCHLAB_API_URL
            # env var)
            datasource_kwargs['base_url'] = getattr(
                args, 'api_url', 'http://127.0.0.1:8000')
            datasource_kwargs['timeout'] = 5.0

        elif self.source_type == 'mqtt':
            datasource_kwargs['broker'] = getattr(
                args, 'mqtt_broker', 'localhost')
            datasource_kwargs['port'] = getattr(args, 'mqtt_port', 1883)
            datasource_kwargs['timeout'] = 5.0

        elif self.source_type == 'named_pipe':
            datasource_kwargs['timeout'] = 5.0

        elif self.source_type == 'service_http':
            service_url = getattr(
                args, 'service_url', None) or 'http://localhost:8585'
            datasource_kwargs['base_url'] = service_url
            datasource_kwargs['timeout'] = 5.0

        self.datasource_manager = DataSourceManager(
            source_type=self.source_type,
            stats_callback=stats_callback,
            **datasource_kwargs
        )

        self.tui_core = None
        self.fleet_cache = []
        self.last_fleet_refresh = 0.0

    def run(self, stdscr):
        self.tui_core = TUICore(stdscr, __version__)

        # Force full screen redraw to overwrite any stray console output
        stdscr.clearok(True)

        # Auto-connect for all non-direct sources
        if self.source_type != 'direct':
            self._connect_datasource()

        self._refresh_fleet_cache()

        while True:
            snapshot = self.datasource_manager.snapshot()

            rendered = self.tui_core.render(
                snapshot=snapshot,
                stats=self.stats,
                fleet_devices=self.fleet_cache,
                refresh_interval=getattr(self.args, 'interval', 1.0)
            )

            if not rendered:
                time.sleep(0.2)
                continue

            try:
                key = stdscr.getkey()
                action = self.tui_core.handle_key(key)

                if not self._handle_action(action):
                    break

            except curses.error:
                pass

    def _handle_action(self, action: Dict[str, Any]) -> bool:
        action_type = action.get('type', 'none')

        if action_type == 'quit':
            return False

        elif action_type == 'reset_stats':
            uid = self.datasource_manager.get_selected_uid()
            if uid:
                self.stats.reset(uid)
                self.tui_core.set_status("Stats reset.")
            else:
                self.tui_core.set_status("No device selected.")

        elif action_type == 'rescan_fleet':
            self._refresh_fleet_cache()
            count = len(self.fleet_cache)
            source_label = (
                self.source_type.upper()
                if self.source_type != 'direct' else 'serial')
            self.tui_core.set_status(
                f"Fleet rescanned ({source_label}) — {count} device(s) found.")

        elif action_type == 'fleet_nav':
            direction = action.get('direction')
            self.tui_core.update_fleet_index(len(self.fleet_cache), direction)

        elif action_type == 'fleet_select':
            index = action.get('index', 0)
            if 0 <= index < len(self.fleet_cache):
                selected_device = self.fleet_cache[index]
                self._connect_to_device(selected_device)

        return True

    def _connect_datasource(self):
        """Connect via non-direct datasource
        (FastAPI/MQTT/named_pipe/service_http)."""
        try:
            if self.datasource_manager.connect():
                uid = self.datasource_manager.get_selected_uid()
                self.tui_core.set_status(
                    f"Connected via {self.source_type.upper()}")
                logger.info(
                    f"Connected to {self.source_type} datasource, "
                    f"selected: {uid}")
            else:
                snapshot = self.datasource_manager.snapshot()
                error_msg = snapshot.get(
                    'last_error', f"Failed to connect to {self.source_type}")
                self.tui_core.set_status(
                    f"Connection failed: {error_msg}", 5.0)
                logger.error(
                    f"Failed to connect to {self.source_type}: {error_msg}")
        except Exception as e:
            self.tui_core.set_status(f"Connection error: {str(e)}", 5.0)
            logger.error(
                f"Exception during {self.source_type} connection: {e}")

    def _connect_to_device(self, device: Dict[str, Any]):
        port = device.get('port')
        uid = device.get('uid')

        if uid == "BUSY":
            self.tui_core.set_status(
                "Device is busy (already connected elsewhere)", 3.0)
            return

        try:
            if self.source_type == 'direct':
                if self.datasource_manager.connect(port=port):
                    self.tui_core.set_status(f"Connected to {port}")
                    logger.info(f"Connected to device on {port}")
                else:
                    snapshot = self.datasource_manager.snapshot()
                    error_msg = snapshot.get('last_error', 'Connection failed')
                    self.tui_core.set_status(
                        f"Connection failed: {error_msg}", 4.0)
                    logger.error(f"Failed to connect to {port}: {error_msg}")
            else:
                # Network/pipe datasource — select different device
                if self.datasource_manager.select_device(uid):
                    self.tui_core.set_status(f"Selected device {uid}")
                    logger.info(
                        f"Selected device {uid} via {self.source_type}")
                else:
                    self.tui_core.set_status("Failed to select device", 3.0)
                    logger.warning(f"Failed to select device {uid}")

        except Exception as e:
            self.tui_core.set_status(f"Connection error: {str(e)}", 4.0)
            logger.error(f"Exception during device connection: {e}")

    def _refresh_fleet_cache(self):
        try:
            current_time = time.time()
            if current_time - self.last_fleet_refresh < 1.0:
                return
            self.last_fleet_refresh = current_time

            if (self.source_type != 'direct'
                    and self.datasource_manager.is_connected()):
                devices_dict = self.datasource_manager.list_devices()
                self.fleet_cache = convert_fleet_format(devices_dict)
            else:
                self.fleet_cache = self._scan_local_fleet()

            if self.tui_core and self.tui_core.fleet_index >= len(
                    self.fleet_cache):
                self.tui_core.fleet_index = 0

            logger.debug(
                f"Fleet cache refreshed: {len(self.fleet_cache)} devices")

        except Exception as e:
            logger.warning(f"Error refreshing fleet cache: {e}")
            self.fleet_cache = []

    def _scan_local_fleet(self) -> List[Dict[str, Any]]:
        fleet = []
        try:
            devices = self.datasource_manager.discover_devices()
            for device in devices:
                fleet.append({
                    "port": device.get("port", "Unknown"),
                    "firmware": device.get("fw", "?"),
                    "uid": device.get("uid", "Unknown"),
                    "variant": device.get("variant", "ORIGINAL"),
                })
        except Exception as e:
            logger.error(f"Error during local fleet scan: {e}")

        # discover_devices() probes each port with a fresh serial connection,
        # which fails (and is silently dropped) for the port our own active
        # connection already holds exclusively. Fall back to the connected
        # device's own info so it doesn't vanish from the fleet list while
        # we're connected to it.
        if self.datasource_manager.is_connected():
            uid = self.datasource_manager.get_selected_uid()
            if uid and not any(d["uid"] == uid for d in fleet):
                snapshot = self.datasource_manager.snapshot()
                device_info = snapshot.get('device_info') or {}
                fleet.append({
                    "port": snapshot.get("port") or "Unknown",
                    "firmware": device_info.get("FwVersion", "?"),
                    "uid": uid,
                    "variant": device_info.get("variant", "ORIGINAL"),
                })

        return sorted(fleet, key=lambda d: d["port"])

    def cleanup(self):
        if self.datasource_manager:
            self.datasource_manager.disconnect()


def tui_main(stdscr, _unused, args):
    """Main TUI entry point."""
    # Redirect stdout/stderr to prevent pycore INFO messages from messing up
    # curses layout
    with _TUIStdoutRedirect():
        app = TUIApplication(args)

        try:
            app.run(stdscr)
        except Exception as e:
            logger.error(f"TUI application error: {e}")
            raise
        finally:
            app.cleanup()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BenchLab TUI")
    parser.add_argument(
        '--interval',
        type=float,
        default=1.0,
        help='Telemetry refresh interval')
    parser.add_argument(
        '--source',
        choices=[
            'direct',
            'fastapi',
            'fastapi_custom',
            'mqtt',
            'named_pipe',
            'service_http'],
        default='direct',
        help='Data source type')
    parser.add_argument('--api-port', type=int, default=8000, dest='api_port',
                        help='FastAPI server port')
    parser.add_argument(
        '--mqtt-broker',
        default='localhost',
        dest='mqtt_broker',
        help='MQTT broker hostname')
    parser.add_argument(
        '--mqtt-port',
        type=int,
        default=1883,
        dest='mqtt_port',
        help='MQTT broker port')
    parser.add_argument(
        '--service-url',
        default='http://localhost:8585',
        dest='service_url',
        help='C# BenchLab service HTTP API URL')
    args = parser.parse_args()

    try:
        curses.wrapper(tui_main, None, args)
    except KeyboardInterrupt:
        print("\nTUI interrupted by user")
    except Exception as e:
        print(f"TUI error: {e}")
        sys.exit(1)
