"""
TUI Core - Clean UI rendering logic

Contains the main TUI class and rendering functions, separated from
datasource concerns. Consumes snapshots from DataSourceManager.
"""

import curses
import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import Config
from benchlab.core.statistics import ChannelStats, StatsFormatter

logger = logging.getLogger("benchlab.tui.tui_core")


class TUICore:
    """
    Core TUI class handling rendering, navigation, and user input.
    Separated from datasource management for clean architecture.
    """

    def __init__(self, stdscr, version: str = "1.0.0"):
        """Initialize TUI core.

        Args:
            stdscr: Curses screen object
            version: Application version string
        """
        self.stdscr = stdscr
        self.version = version

        # UI state
        self.current_tab = 0
        self.fleet_index = 0
        self.show_help_modal = False

        self._help_win = None
        self._help_win_size = None
        self._last_size = None

        # Status message system
        self.status_msg = ""
        self.status_msg_expires = 0.0

        # Initialize curses
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        Config.init_colors()

        self.stdscr.nodelay(True)
        self.stdscr.timeout(100)  # 100ms poll for responsive UI

    def set_status(self, msg: str, secs: float = 2.0):
        """Set transient status message.

        Args:
            msg: Status message to display
            secs: Duration in seconds to show message
        """
        self.status_msg = msg
        self.status_msg_expires = time.monotonic() + secs

    def render(self, snapshot: Dict[str, Any], stats: ChannelStats,
               fleet_devices: List[Dict[str, Any]], refresh_interval: float):
        """Main render method - draws entire UI.

        Args:
            snapshot: Current state snapshot from DataSourceManager
            stats: Statistics object for min/max/avg data
            fleet_devices: List of available devices for fleet tab
            refresh_interval: Telemetry refresh interval
        """
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()

        # Check minimum size
        if (height < Config.MIN_TERMINAL_ROWS
                or width < Config.MIN_TERMINAL_COLS):
            self._render_size_warning(width, height)
            return False

        # Detect silent resize (windows-curses may not send KEY_RESIZE)
        last = getattr(self, '_last_size', None)
        if last != (height, width):
            self._last_size = (height, width)
            self._help_win = None
            self._reset_input_settings()
            self.stdscr.clearok(True)

        # Render UI components
        self._render_header(width)
        self._render_tabs(width)
        self._render_separator(width)
        self._render_current_tab(
            snapshot,
            stats,
            fleet_devices,
            refresh_interval,
            height,
            width)
        self._render_status_bar(snapshot, height, width)

        # Stage stdscr first so the help window (staged after, if shown)
        # composites on top of it — doupdate() paints windows in the order
        # they were noutrefresh()'d, so staging order here is significant.
        self.stdscr.noutrefresh()

        # Show help modal if requested
        if self.show_help_modal:
            self._render_help_modal(height, width)

        # Composite stdscr + help window (if any) into one physical update.
        # Using stdscr.refresh() here (or in the caller) would immediately
        # repaint the physical screen from stdscr alone, wiping out the
        # help window's own refresh() and causing it to flicker/vanish on
        # every render tick.
        curses.doupdate()
        return True

    def handle_key(self, key: str) -> Dict[str, Any]:
        """Handle keyboard input and return action information.

        Args:
            key: Key pressed

        Returns:
            Dict with action information for the main application to handle
        """
        action = {'type': 'none'}

        # Global keys
        if key in ('q', 'Q'):
            action = {'type': 'quit'}
        elif key == '?':
            self.show_help_modal = not self.show_help_modal
        elif self.show_help_modal:
            # Any key closes help modal
            self.show_help_modal = False
        elif key in ('KEY_RIGHT', 'l'):
            self.current_tab = (self.current_tab + 1) % len(Config.TAB_NAMES)
        elif key in ('KEY_LEFT', 'h'):
            self.current_tab = (self.current_tab - 1) % len(Config.TAB_NAMES)
        elif key.isdigit() and int(key) < len(Config.TAB_NAMES):
            self.current_tab = int(key)
        elif key in ('r', 'R'):
            action = {'type': 'reset_stats'}
        elif key == 'f':
            action = {'type': 'rescan_fleet'}
        elif key == "KEY_RESIZE":
            import shutil
            real = shutil.get_terminal_size(fallback=(80, 24))
            try:
                if hasattr(curses, 'resizeterm'):
                    curses.resizeterm(real.lines, real.columns)
                elif (hasattr(curses, '_curses')
                        and hasattr(curses._curses, 'resize_term')):
                    curses._curses.resize_term(real.lines, real.columns)
            except Exception:
                pass
            self._reset_input_settings()   # ← add this
            self.stdscr.clearok(True)
            self._help_win = None
            self._help_win_size = None

        # Tab-specific keys
        elif self.current_tab == 0:  # Fleet tab
            fleet_action = self._handle_fleet_keys(key)
            if fleet_action:
                action = fleet_action

        return action

    def _handle_fleet_keys(self, key: str) -> Optional[Dict[str, Any]]:
        """Handle fleet tab specific keys.

        Args:
            key: Key pressed

        Returns:
            Action dict or None
        """
        if key == 'KEY_UP':
            # Let caller know fleet_index changed, they need to provide device
            # count
            return {'type': 'fleet_nav', 'direction': 'up'}
        elif key == 'KEY_DOWN':
            return {'type': 'fleet_nav', 'direction': 'down'}
        elif key in ('\n', '\r', 'KEY_ENTER'):
            return {'type': 'fleet_select', 'index': self.fleet_index}
        return None

    def update_fleet_index(self, device_count: int, direction: str = None):
        """Update fleet index for navigation.

        Args:
            device_count: Total number of devices
            direction: 'up' or 'down' for navigation
        """
        if direction == 'up' and device_count > 0:
            self.fleet_index = (self.fleet_index - 1) % device_count
        elif direction == 'down' and device_count > 0:
            self.fleet_index = (self.fleet_index + 1) % device_count
        elif device_count == 0:
            self.fleet_index = 0

    def _render_size_warning(self, width: int, height: int):
        msg = (
            f" Terminal too small ({width}x{height})"
            f" — resize to "
            f"{Config.MIN_TERMINAL_COLS}x{Config.MIN_TERMINAL_ROWS} ")
        try:
            self.stdscr.addstr(
                0, 0, msg[:width],   # ← truncate, don't center
                curses.A_BOLD | curses.color_pair(
                    Config.COLOR_PAIRS['error']))
        except curses.error:
            pass

    def _render_header(self, width: int):
        """Render application header."""
        header = f" BENCHLAB Telemetry v{self.version} "
        try:
            self.stdscr.addstr(
                0,
                0,
                header.center(width),
                curses.A_BOLD | curses.color_pair(
                    Config.COLOR_PAIRS['header']))
        except curses.error:
            pass

    def _render_tabs(self, width: int):
        """Render tab navigation bar."""
        tab_x = 2
        for i, name in enumerate(Config.TAB_NAMES):
            label = f" {i}:{name} "
            try:
                if i == self.current_tab:
                    self.stdscr.addstr(
                        2, tab_x, label, curses.color_pair(
                            Config.COLOR_PAIRS['header']) | curses.A_BOLD)
                else:
                    self.stdscr.addstr(
                        2, tab_x, label, curses.color_pair(
                            Config.COLOR_PAIRS['default']))
            except curses.error:
                pass
            tab_x += len(label) + 1

    def _render_separator(self, width: int):
        """Render separator line below tabs."""
        try:
            self.stdscr.addstr(
                3, 0, "─" * (width - 1),
                curses.color_pair(Config.COLOR_PAIRS['default']))
        except curses.error:
            pass

    def _render_current_tab(self,
                            snapshot: Dict[str,
                                           Any],
                            stats: ChannelStats,
                            fleet_devices: List[Dict[str,
                                                     Any]],
                            refresh_interval: float,
                            height: int,
                            width: int):
        """Render the currently selected tab."""
        try:
            if self.current_tab == 0:
                self._render_fleet_tab(snapshot, fleet_devices, height, width)
            elif self.current_tab == 1:
                self._render_device_tab(snapshot, refresh_interval)
            elif self.current_tab == 2:
                self._render_system_tab(snapshot, stats)
            elif self.current_tab == 3:
                self._render_mb_tab(snapshot, stats)
            elif self.current_tab == 4:
                self._render_hpwr_tab(snapshot, stats)
            elif self.current_tab == 5:
                self._render_voltage_tab(snapshot, stats)
            elif self.current_tab == 6:
                self._render_temperature_tab(snapshot, stats)
            elif self.current_tab == 7:
                self._render_fans_tab(snapshot, stats, height, width)
        except curses.error:
            pass

    def _render_status_bar(
            self, snapshot: Dict[str, Any], height: int, width: int):
        """Render bottom status bar."""
        try:
            # Separator line
            self.stdscr.addstr(
                height - 2, 0, "─" * (width - 1),
                curses.color_pair(Config.COLOR_PAIRS['default']))

            # Left side: transient message or last error or help
            if self.status_msg and time.monotonic() < self.status_msg_expires:
                left_msg = self.status_msg
                left_col = curses.color_pair(Config.COLOR_PAIRS['caution'])
            elif snapshot.get('last_error'):
                left_msg = f"! {snapshot['last_error']}"
                left_col = curses.color_pair(Config.COLOR_PAIRS['error'])
            else:
                left_msg = "q=quit  ?=help  r=reset stats  f=rescan"
                left_col = curses.color_pair(Config.COLOR_PAIRS['default'])

            # Right side: uptime | connection status | device
            ct = snapshot.get('connection_time')
            if snapshot.get('connected') and ct:
                uptime_str = str(datetime.now() - ct).split('.')[0]
                uptime_part = f"up {uptime_str}  "
            else:
                uptime_part = ""

            device_str = snapshot.get(
                'uid') or snapshot.get('port') or "no device"
            con_str = "CONN" if snapshot.get('connected') else "DISC"
            con_col = (
                curses.color_pair(Config.COLOR_PAIRS['ok'])
                if snapshot.get('connected')
                else curses.color_pair(Config.COLOR_PAIRS['error']))
            right_msg = f"{uptime_part}{con_str}  {device_str}"
            right_col = max(0, width - len(right_msg) - 2)

            # Draw status bar
            self.stdscr.addstr(
                height - 1, 2, left_msg[:width - len(right_msg) - 4], left_col)
            self.stdscr.addstr(
                height - 1, right_col,
                right_msg[:width - right_col - 1], con_col)

        except curses.error:
            pass

    def _render_help_modal(self, height: int, width: int):
        """Render help modal overlay."""
        h = min(len(Config.HELP_TEXT) + 4, height - 2)
        w = min(60, width - 4)

        # Don't attempt to draw if terminal is too small for a modal
        if h < 3 or w < 10:
            return

        sy = (height - h) // 2
        sx = (width - w) // 2

        # Cache window; only recreate if size changed (also prevents per-frame
        # alloc)
        if (not hasattr(self, '_help_win') or self._help_win is None
                or getattr(self, '_help_win_size', None) != (h, w, sy, sx)):
            try:
                self._help_win = curses.newwin(h, w, sy, sx)
            except curses.error:
                return
            self._help_win_size = (h, w, sy, sx)

        win = self._help_win
        try:
            win.erase()
            win.attron(
                curses.color_pair(
                    Config.COLOR_PAIRS['header']) | curses.A_BOLD)
            win.border()
            win.attroff(
                curses.color_pair(
                    Config.COLOR_PAIRS['header']) | curses.A_BOLD)
            for i, line in enumerate(Config.HELP_TEXT):
                if i < h - 2:
                    win.addstr(i + 1, 2, line[:w - 4])
            # noutrefresh (not refresh) — this window is composited together
            # with stdscr via a single curses.doupdate() call in render(),
            # so it doesn't get immediately overwritten by stdscr's own
            # refresh on the next tick.
            win.noutrefresh()
        except curses.error:
            pass

    def _reset_input_settings(self):
        """Re-apply input settings — windows-curses resets these after
        resize."""
        self.stdscr.nodelay(True)
        self.stdscr.timeout(100)
        # discard any garbage keystrokes queued during resize
        curses.flushinp()

    # ═══════════════════════════════════════════════════════════════════════════════
    #  Tab Renderers
    # ═══════════════════════════════════════════════════════════════════════════════

    def _render_fleet_tab(self,
                          snapshot: Dict[str,
                                         Any],
                          fleet_devices: List[Dict[str,
                                                   Any]],
                          height: int,
                          width: int):
        """Render Fleet tab."""
        source_type = snapshot.get('source_type', 'unknown')
        if source_type == 'direct':
            title = "BENCHLAB Fleet"
        else:
            title = f"BENCHLAB Fleet (via {source_type.upper()})"

        self._draw_section(4, 2, title)

        if not fleet_devices:
            try:
                self.stdscr.addstr(
                    6, 4, "No connected devices found.", curses.color_pair(
                        Config.COLOR_PAIRS['error']))
            except curses.error:
                pass
            return

        # Column headers
        cols = Config.Layout.FLEET_COLS
        widths = Config.Layout.FLEET_WIDTHS
        hdr_attr = curses.A_UNDERLINE | curses.color_pair(
            Config.COLOR_PAIRS['default'])

        try:
            self.stdscr.addstr(6, cols['SEL'], f"{'':2}", hdr_attr)
            self.stdscr.addstr(
                6, cols['MODEL'],
                f"{'Model':<{widths['MODEL']}}", hdr_attr)
            self.stdscr.addstr(
                6, cols['PORT'], f"{'Port':<{widths['PORT']}}", hdr_attr)
            self.stdscr.addstr(
                6, cols['FW'], f"{'Firmware':<{widths['FW']}}", hdr_attr)
            self.stdscr.addstr(
                6, cols['UID'], f"{'UID':<{widths['UID']}}", hdr_attr)
            self.stdscr.addstr(
                6, cols['STATUS'],
                f"{'Status':<{widths['STATUS']}}", hdr_attr)
            self.stdscr.addstr(6, cols['ACTIVE'], "Active", hdr_attr)
        except curses.error:
            pass

        # Device rows
        connected_uid = snapshot.get('uid')
        connected = snapshot.get('connected', False)

        for i, dev in enumerate(fleet_devices):
            port = dev.get('port', 'Unknown')
            firmware = dev.get('firmware', 0)
            dev_uid = dev.get('uid', 'Unknown')
            is_busy = dev_uid == "BUSY"

            variant = dev.get('variant')
            if variant is None and dev.get('ProductId') is not None:
                variant = 'BL2' if dev.get('ProductId') == 0x11 else 'ORIGINAL'
            model_str = 'BL2' if variant == 'BL2' else (
                'BL1' if variant else '?')

            cursor = "->" if i == self.fleet_index else "  "
            is_active = connected_uid is not None and dev_uid == connected_uid
            is_connected = is_active and connected

            if is_busy:
                status = "BUSY"
                status_color = curses.color_pair(Config.COLOR_PAIRS['caution'])
            elif is_connected:
                status = "CONNECTED"
                status_color = curses.color_pair(Config.COLOR_PAIRS['ok'])
            else:
                status = "DISCONNECTED"
                status_color = curses.color_pair(Config.COLOR_PAIRS['error'])

            row_color = (
                curses.color_pair(Config.COLOR_PAIRS['header'])
                | curses.A_BOLD if i == self.fleet_index
                else curses.color_pair(Config.COLOR_PAIRS['default']))

            row = 8 + i
            try:
                self.stdscr.addstr(row, cols['SEL'], cursor, row_color)
                self.stdscr.addstr(
                    row, cols['MODEL'],
                    f"{model_str:<{widths['MODEL']}}", row_color)
                self.stdscr.addstr(
                    row, cols['PORT'],
                    f"{port:<{widths['PORT']}}", row_color)

                if is_busy:
                    self.stdscr.addstr(
                        row, cols['FW'],
                        f"{'N/A':<{widths['FW']}}", row_color)
                    self.stdscr.addstr(
                        row, cols['UID'], f"{dev_uid:<{widths['UID']}}",
                        curses.color_pair(
                            Config.COLOR_PAIRS['caution']) | curses.A_BOLD)
                else:
                    try:
                        fw_int = int(firmware) if firmware is not None else 0
                        fw_str = f"0x{fw_int:08X}"
                    except (TypeError, ValueError):
                        fw_str = "0x????????"
                    self.stdscr.addstr(
                        row, cols['FW'],
                        f"{fw_str:<{widths['FW']}}", row_color)
                    self.stdscr.addstr(
                        row, cols['UID'],
                        f"{dev_uid:<{widths['UID']}}", row_color)

                self.stdscr.addstr(
                    row, cols['STATUS'],
                    f"{status:<{widths['STATUS']}}", status_color)

                if is_active:
                    self.stdscr.addstr(
                        row,
                        cols['ACTIVE'],
                        "*",
                        curses.color_pair(
                            Config.COLOR_PAIRS['ok']) | curses.A_BOLD)
            except curses.error:
                pass

        # Footer
        status_text = "CONNECTED" if connected else "DISCONNECTED"
        status_color = (
            curses.color_pair(
                Config.COLOR_PAIRS['ok']) if connected else curses.color_pair(
                Config.COLOR_PAIRS['error']))
        try:
            self.stdscr.addstr(
                height - 4,
                2,
                f"Status: {status_text}",
                status_color | curses.A_BOLD)
            ct = snapshot.get('connection_time')
            if connected and ct:
                uptime = str(datetime.now() - ct).split('.')[0]
                self.stdscr.addstr(
                    height - 2,
                    2,
                    f"Uptime: {uptime}",
                    curses.color_pair(
                        Config.COLOR_PAIRS['caution']))
            else:
                self.stdscr.addstr(
                    height - 2,
                    2,
                    "Uptime: —",
                    curses.color_pair(
                        Config.COLOR_PAIRS['error']))
        except curses.error:
            pass

    def _render_device_tab(
            self, snapshot: Dict[str, Any], refresh_interval: float):
        """Render Device tab."""
        device_info = snapshot.get('device_info') or {}
        uid = snapshot.get('uid')
        source_type = snapshot.get('source_type', 'Unknown')
        source_desc = snapshot.get('source_desc', '')
        port_str = snapshot.get('port', 'Unknown')

        try:
            self._draw_section(4, 2, "Connection")
            self.stdscr.addstr(5, 4, f"{'Data Source':<22} {source_type}")
            self.stdscr.addstr(6, 4, f"{'Connection':<22} {source_desc}")
            self.stdscr.addstr(7, 4, f"{'Device Port':<22} {port_str}")

            if not snapshot.get('connected'):
                self.stdscr.addstr(
                    10, 4, "Not connected to device.", curses.color_pair(
                        Config.COLOR_PAIRS['error']))
                return

            # Device information
            def _hex_str(value):
                try:
                    return f"0x{int(value):02X}"
                except (TypeError, ValueError):
                    return "0x??"

            vendor_str = _hex_str(device_info.get('VendorId', 0))
            product_id = device_info.get('ProductId', 0)
            product_str = _hex_str(product_id)
            fw_str = _hex_str(device_info.get('FwVersion', 0))

            variant = device_info.get('variant')
            if variant is None:
                variant = 'BL2' if product_id == 0x11 else 'ORIGINAL'
            model_str = (
                "BENCHLAB 2 (BL2)" if variant == 'BL2'
                else "BENCHLAB 1 (Original)")

            self._draw_section(9, 2, "Device")
            self.stdscr.addstr(10, 4, f"{'Model':<22} {model_str}")
            self.stdscr.addstr(11, 4, f"{'Vendor ID':<22} {vendor_str}")
            self.stdscr.addstr(12, 4, f"{'Product ID':<22} {product_str}")
            self.stdscr.addstr(13, 4, f"{'Device UID':<22} {uid or 'N/A'}")
            self.stdscr.addstr(14, 4, f"{'Firmware Version':<22} {fw_str}")

            self._draw_section(16, 2, "TUI")
            self.stdscr.addstr(
                17, 4, f"{'Refresh Interval':<22} {refresh_interval} s")

        except curses.error:
            pass

    def _render_system_tab(
            self, snapshot: Dict[str, Any], stats: ChannelStats):
        """Render System tab."""
        if not snapshot.get('connected') or not snapshot.get('sensor_data'):
            self._draw_disconnected("System")
            return

        sd = snapshot['sensor_data']
        uid = snapshot.get('uid', '')
        device_info = snapshot.get('device_info') or {}

        # Detect device variant from device info (Product ID) or sensor data
        product_id = device_info.get('ProductId')
        if product_id is not None:
            variant = 'BL2' if product_id == 0x11 else 'ORIGINAL'
        else:
            variant = Config.Channels.detect_variant(sd)
        rail_channels = Config.Channels.get_rail_channels(variant)

        # Summary section
        row = 4
        self._draw_section(row, 2, "Summary")
        row += 1

        sum_vals = [
            sd.get(
                k,
                0.0) or 0.0 for k,
            _ in Config.Channels.POWER_SUMMARY]
        max_sum = (Config.BarScales.POWER_MAX or
                   max(Config.BarScales.POWER_AUTO_FLOOR, max(sum_vals) * 1.2))

        for (key, label), val in zip(Config.Channels.POWER_SUMMARY, sum_vals):
            stat = stats.get(uid, key)
            self._draw_bar(
                row, 4, label, val, 'W', max_sum, curses.color_pair(
                    Config.COLOR_PAIRS['caution']), stat=stat)
            row += 1

        # Power telemetry
        row += 1
        self._draw_section(row, 2, "Power Telemetry")
        row += 1

        pwr_vals = [sd.get(f'{k}_Power', 0.0) or 0.0 for k, _ in rail_channels]
        max_pwr = (Config.BarScales.POWER_MAX or
                   max(Config.BarScales.POWER_AUTO_FLOOR, max(pwr_vals) * 1.2))

        for (key_pfx, label), val in zip(rail_channels, pwr_vals):
            stat = stats.get(uid, f'{key_pfx}_Power')
            self._draw_bar(
                row, 4, label, val, 'W', max_pwr, curses.color_pair(
                    Config.COLOR_PAIRS['caution']), stat=stat)
            row += 1

        # Current telemetry
        row += 1
        self._draw_section(row, 2, "Current Telemetry")
        row += 1

        cur_vals = [
            sd.get(
                f'{k}_Current',
                0.0) or 0.0 for k,
            _ in rail_channels]
        max_cur = (
            Config.BarScales.CURRENT_MAX or max(
                Config.BarScales.CURRENT_AUTO_FLOOR,
                max(cur_vals) * 1.2))

        for (key_pfx, label), val in zip(rail_channels, cur_vals):
            stat = stats.get(uid, f'{key_pfx}_Current')
            self._draw_bar(
                row,
                4,
                label,
                val,
                'A',
                max_cur,
                curses.color_pair(
                    Config.COLOR_PAIRS['info']),
                stat=stat,
                decimals=2)
            row += 1

        # Voltage telemetry
        row += 1
        self._draw_section(row, 2, "Voltage Telemetry")
        row += 1

        for (
            key_pfx, label), val in zip(
            rail_channels, [
                sd.get(
                f'{k}_Voltage', 0.0) or 0.0 for k, _ in rail_channels]):
            stat = stats.get(uid, f'{key_pfx}_Voltage')
            self._draw_bar(
                row,
                4,
                label,
                val,
                'V',
                Config.BarScales.RAIL_VOLTAGE_MAX,
                curses.color_pair(
                    Config.COLOR_PAIRS['voltage']),
                stat=stat,
                decimals=2)
            row += 1

    def _render_mb_tab(self, snapshot: Dict[str, Any], stats: ChannelStats):
        """Render Motherboard rails tab (ATX3V, ATX5V, ATX5VSB, ATX12V)."""
        if not snapshot.get('connected') or not snapshot.get('sensor_data'):
            self._draw_disconnected("Motherboard")
            return

        sd = snapshot['sensor_data']
        uid = snapshot.get('uid', '')
        mb_channels = Config.Channels.MB_RAIL_CHANNELS

        row = 4
        self._draw_section(row, 2, "Motherboard Power Rails")
        row += 1

        # Power readings
        self._draw_section(row, 2, "Power")
        row += 1

        mb_pwr_vals = [
            sd.get(
                f'{k}_Power',
                0.0) or 0.0 for k,
            _ in mb_channels]
        max_mb_pwr = (
            Config.BarScales.POWER_MAX or max(
                Config.BarScales.POWER_AUTO_FLOOR,
                max(mb_pwr_vals) * 1.2))

        for (key_pfx, label), val in zip(mb_channels, mb_pwr_vals):
            stat = stats.get(uid, f'{key_pfx}_Power')
            self._draw_bar(
                row, 4, label, val, 'W', max_mb_pwr, curses.color_pair(
                    Config.COLOR_PAIRS['caution']), stat=stat)
            row += 1

        # Current readings
        row += 1
        self._draw_section(row, 2, "Current")
        row += 1

        mb_cur_vals = [
            sd.get(
                f'{k}_Current',
                0.0) or 0.0 for k,
            _ in mb_channels]
        max_mb_cur = (
            Config.BarScales.CURRENT_MAX or max(
                Config.BarScales.CURRENT_AUTO_FLOOR,
                max(mb_cur_vals) * 1.2))

        for (key_pfx, label), val in zip(mb_channels, mb_cur_vals):
            stat = stats.get(uid, f'{key_pfx}_Current')
            self._draw_bar(
                row,
                4,
                label,
                val,
                'A',
                max_mb_cur,
                curses.color_pair(
                    Config.COLOR_PAIRS['info']),
                stat=stat,
                decimals=2)
            row += 1

        # Voltage readings
        row += 1
        self._draw_section(row, 2, "Voltage")
        row += 1

        for (
            key_pfx, label), val in zip(
            mb_channels, [
                sd.get(
                f'{k}_Voltage', 0.0) or 0.0 for k, _ in mb_channels]):
            stat = stats.get(uid, f'{key_pfx}_Voltage')
            # Use appropriate voltage max based on rail type
            if key_pfx == 'ATX3V':
                v_max = 5.0
            elif key_pfx == 'ATX5V' or key_pfx == 'ATX5VSB':
                v_max = 7.0
            else:  # ATX12V
                v_max = 15.0
            self._draw_bar(row, 4, label, val, 'V', v_max, curses.color_pair(
                Config.COLOR_PAIRS['voltage']), stat=stat, decimals=2)
            row += 1

    def _render_hpwr_tab(self, snapshot: Dict[str, Any], stats: ChannelStats):
        """Render 12VHPWR tab (BL2-specific HPWR_Wx sense lines)."""
        if not snapshot.get('connected') or not snapshot.get('sensor_data'):
            self._draw_disconnected("12VHPWR")
            return

        sd = snapshot['sensor_data']
        uid = snapshot.get('uid', '')
        device_info = snapshot.get('device_info') or {}

        # Detect device variant from device info (Product ID)
        product_id = device_info.get('ProductId')
        if product_id is not None:
            variant = 'BL2' if product_id == 0x11 else 'ORIGINAL'
        else:
            variant = Config.Channels.detect_variant(sd)

        # Only show HPWR_Wx sensors for BL2 (BENCHLAB 2) devices
        if variant != 'BL2':
            self._draw_section(4, 2, "12VHPWR Sense Lines")
            try:
                self.stdscr.addstr(
                    6,
                    4,
                    "HPWR_Wx sense lines are only available on BENCHLAB 2 "
                    "devices.",
                    curses.color_pair(
                        Config.COLOR_PAIRS['info']))
            except curses.error:
                pass
            return

        hpwr_channels = Config.Channels.HPWR_SENSE_CHANNELS

        row = 4
        self._draw_section(row, 2, "12VHPWR Sense Lines (BENCHLAB 2)")
        row += 1

        # Power readings
        self._draw_section(row, 2, "Power")
        row += 1

        for (key_pfx, label) in hpwr_channels:
            val = sd.get(f'{key_pfx}_Power', 0.0)
            stat = stats.get(uid, f'{key_pfx}_Power')
            self._draw_bar(
                row,
                4,
                label,
                val,
                'W',
                Config.BarScales.POWER_MAX,
                curses.color_pair(
                    Config.COLOR_PAIRS['caution']),
                stat=stat)
            row += 1

        # Current readings
        row += 1
        self._draw_section(row, 2, "Current")
        row += 1

        for (key_pfx, label) in hpwr_channels:
            val = sd.get(f'{key_pfx}_Current', 0.0)
            stat = stats.get(uid, f'{key_pfx}_Current')
            self._draw_bar(
                row,
                4,
                label,
                val,
                'A',
                Config.BarScales.CURRENT_MAX,
                curses.color_pair(
                    Config.COLOR_PAIRS['info']),
                stat=stat,
                decimals=2)
            row += 1

        # Voltage readings
        row += 1
        self._draw_section(row, 2, "Voltage")
        row += 1

        for (key_pfx, label) in hpwr_channels:
            val = sd.get(f'{key_pfx}_Voltage', 0.0)
            stat = stats.get(uid, f'{key_pfx}_Voltage')
            self._draw_bar(
                row,
                4,
                label,
                val,
                'V',
                Config.BarScales.RAIL_VOLTAGE_MAX,
                curses.color_pair(
                    Config.COLOR_PAIRS['voltage']),
                stat=stat,
                decimals=2)
            row += 1

    def _render_voltage_tab(
            self, snapshot: Dict[str, Any], stats: ChannelStats):
        """Render Voltage tab."""
        if not snapshot.get('connected') or not snapshot.get('sensor_data'):
            self._draw_disconnected("Voltage")
            return

        sd = snapshot['sensor_data']
        uid = snapshot.get('uid', '')
        row = 4

        # Board voltages
        self._draw_section(row, 2, "Board")
        row += 1

        for key, label, band in Config.Channels.BOARD_VOLTAGES:
            val = sd.get(key, 0.0)
            stat = stats.get(uid, key)
            self._draw_bar(
                row,
                4,
                label,
                val,
                'V',
                Config.BarScales.BOARD_VOLTAGE_MAX,
                curses.color_pair(
                    Config.COLOR_PAIRS['voltage']),
                stat=stat,
                decimals=3)
            row += 1

        # VIN measurements
        row += 1
        self._draw_section(row, 2, "Voltage Measurements")
        row += 1

        for vin_key in Config.Channels.VIN_CHANNELS:
            val = sd.get(vin_key, 0.0)
            stat = stats.get(uid, vin_key)
            self._draw_bar(
                row,
                4,
                vin_key,
                val,
                'V',
                Config.BarScales.VIN_VOLTAGE_MAX,
                curses.color_pair(
                    Config.COLOR_PAIRS['voltage']),
                stat=stat,
                decimals=3)
            row += 1

    def _render_temperature_tab(
            self, snapshot: Dict[str, Any], stats: ChannelStats):
        """Render Temperature tab."""
        if not snapshot.get('connected') or not snapshot.get('sensor_data'):
            self._draw_disconnected("Temperature")
            return

        sd = snapshot['sensor_data']
        uid = snapshot.get('uid', '')
        device_info = snapshot.get('device_info') or {}

        # Detect device variant from device info (Product ID) or sensor data
        product_id = device_info.get('ProductId')
        if product_id is not None:
            variant = 'BL2' if product_id == 0x11 else 'ORIGINAL'
        else:
            variant = Config.Channels.detect_variant(sd)
        temp_sensors = Config.Channels.get_temperature_sensors(variant)

        row = 4

        # Board
        self._draw_section(row, 2, "Board")
        row += 1

        chip_temp = sd.get('Chip_Temp', 0.0)
        # Handle None values from sensor data
        if chip_temp is None:
            chip_temp = 0.0
        chip_color = Config.get_temperature_color(
            chip_temp, Config.BarScales.CHIP_TEMP_WARN)
        stat = stats.get(uid, 'Chip_Temp')
        self._draw_bar(
            row,
            4,
            'Chip Temp',
            chip_temp,
            '°C',
            Config.BarScales.CHIP_TEMP_MAX,
            curses.color_pair(chip_color),
            stat=stat,
            decimals=1)
        row += 2

        # System
        self._draw_section(row, 2, "System")
        row += 1

        for key, label in [('Ambient_Temp', 'Ambient Temp'),
                           ('Humidity', 'Humidity')]:
            val = sd.get(key, 0.0)
            stat = stats.get(uid, key)
            max_val = (
                Config.BarScales.AMBIENT_TEMP_MAX if key == 'Ambient_Temp'
                else Config.BarScales.HUMIDITY_MAX)
            unit = '°C' if key == 'Ambient_Temp' else '%'
            self._draw_bar(
                row,
                4,
                label,
                val,
                unit,
                max_val,
                curses.color_pair(
                    Config.COLOR_PAIRS['info']),
                stat=stat,
                decimals=1)
            row += 1

        row += 1

        # Sensors - dynamic based on variant (only show variant label for BL2)
        if variant == 'BL2':
            self._draw_section(row, 2, "Sensors (BENCHLAB 2)")
        else:
            self._draw_section(row, 2, "Sensors")
        row += 1

        for i, sensor_key in enumerate(temp_sensors):
            val = sd.get(sensor_key, 0.0)
            # Handle None values from sensor data
            if val is None:
                val = 0.0
            stat = stats.get(uid, sensor_key)
            s_color = Config.get_temperature_color(
                val, Config.BarScales.SENSOR_TEMP_WARN)
            # Use actual sensor name for display
            label = sensor_key if '_' in sensor_key else f'Sensor {i + 1}'
            self._draw_bar(
                row,
                4,
                label,
                val,
                '°C',
                Config.BarScales.SENSOR_TEMP_MAX,
                curses.color_pair(s_color),
                stat=stat,
                decimals=1)
            row += 1

    def _render_fans_tab(self, snapshot: Dict[str, Any], stats: ChannelStats,
                         height: int = None, width: int = None):
        """Render Fans tab."""
        self._draw_section(4, 2, "Fan Control & Monitoring")

        if not snapshot.get('connected') or not snapshot.get('sensor_data'):
            self._draw_disconnected("Fan")
            return

        sd = snapshot['sensor_data']
        uid = snapshot.get('uid', '')

        # Column positions
        cols = Config.Layout.FAN_COLS
        rpm_max_str = str(Config.BarScales.FAN_RPM_MAX)
        hdr = (f"{'Fan':<8} {'Duty':>5}%  {'RPM':>6}  {'On':<3}  "
               f"{'Bar (' + rpm_max_str + ' RPM)':<20}  Stats")

        try:
            self.stdscr.addstr(
                5,
                cols['NAME'] + 2,
                hdr,
                curses.A_UNDERLINE | curses.color_pair(
                    Config.COLOR_PAIRS['default']))
        except curses.error:
            pass

        # Determine number of fans (highest fan index present, so
        # non-contiguous numbering like Fan1/Fan3 with no Fan2 still
        # renders all known fans)
        fan_indices = [int(k[3:-5]) for k in sd if k.startswith('Fan')
                       and k.endswith('_Duty') and k[3:-5].isdigit()]
        num_fans = max(fan_indices, default=0)

        # Reserve rows for the external fan row, active-count line, and the
        # status bar so fan rows never silently overlap them on a short
        # terminal. Each fan takes one row starting at row 7.
        FAN_ROWS_START = 7
        # ext fan row + active count + status bar + margin
        RESERVED_TRAILING_ROWS = 4
        if height is not None:
            max_visible_fans = max(
                0, height - RESERVED_TRAILING_ROWS - FAN_ROWS_START)
        else:
            max_visible_fans = num_fans
        visible_fans = min(num_fans, max_visible_fans)
        hidden_fans = num_fans - visible_fans

        # Fan rows
        for i in range(1, visible_fans + 1):
            duty = sd.get(f'Fan{i}_Duty', 0) or 0
            rpm = sd.get(f'Fan{i}_RPM', 0) or 0
            # Assume enabled since we can't get status from dict
            enabled = True

            rpm_key = f'Fan{i}_RPM'
            duty_key = f'Fan{i}_Duty'

            bar_len = max(
                0, min(
                    20, int(
                        (rpm / Config.BarScales.FAN_RPM_MAX) * 20)))
            fan_color = (curses.color_pair(Config.COLOR_PAIRS['ok']) if rpm > 0
                         else curses.color_pair(Config.COLOR_PAIRS['error']))
            en_str = "Yes" if enabled else "No"
            bar = "█" * bar_len + "░" * (20 - bar_len)

            mn_r, mx_r, avg_r = stats.get(uid, rpm_key)
            mn_d, mx_d, avg_d = stats.get(uid, duty_key)

            row = 7 + i - 1
            try:
                self.stdscr.addstr(
                    row,
                    cols['NAME'] + 2,
                    f"Fan{i:<5}",
                    fan_color)
                self.stdscr.addstr(
                    row, cols['DUTY'] + 2, f"{duty:>5}%", fan_color)
                self.stdscr.addstr(
                    row, cols['RPM'] + 2, f"{rpm:>6}", fan_color)
                self.stdscr.addstr(
                    row, cols['ENABLED'] + 2, f"{en_str:<3}", fan_color)
                self.stdscr.addstr(row, cols['BAR'] + 2, bar, fan_color)

                if mn_r is not None:
                    stat_str = (f"  {mn_r:.0f}-{mx_r:.0f} ~{avg_r:.0f} RPM"
                                f"  {mn_d:.0f}-{mx_d:.0f} ~{avg_d:.0f}%")
                    self.stdscr.addstr(
                        row,
                        cols['STATS'] + 2,
                        stat_str,
                        curses.color_pair(
                            Config.COLOR_PAIRS['default']))
            except curses.error:
                pass

        # Note if some fans were hidden to avoid overlapping the status bar
        if hidden_fans > 0:
            try:
                self.stdscr.addstr(
                    FAN_ROWS_START + visible_fans,
                    cols['NAME'] + 2,
                    f"... +{hidden_fans} more fan(s) — resize terminal to "
                    "see all",
                    curses.color_pair(
                        Config.COLOR_PAIRS['caution']))
            except curses.error:
                pass

        # External fan
        ext_duty = sd.get('FanExtDuty', 0) or 0
        ext_bar = max(0, min(20, int(ext_duty / 5)))
        ext_row = 8 + visible_fans + 1 + (1 if hidden_fans > 0 else 0)

        try:
            self.stdscr.addstr(
                ext_row,
                cols['NAME'] + 2,
                "Ext Fan ",
                curses.color_pair(
                    Config.COLOR_PAIRS['highlight']))
            self.stdscr.addstr(
                ext_row, cols['DUTY'] + 2, f"{ext_duty:>5}%",
                curses.color_pair(Config.COLOR_PAIRS['highlight']))
            self.stdscr.addstr(
                ext_row, cols['RPM'] + 2, f"{'N/A':>6}",
                curses.color_pair(Config.COLOR_PAIRS['highlight']))
            self.stdscr.addstr(
                ext_row, cols['ENABLED'] + 2, f"{'N/A':<3}",
                curses.color_pair(Config.COLOR_PAIRS['highlight']))
            self.stdscr.addstr(
                ext_row, cols['BAR'] + 2,
                "█" * ext_bar + "░" * (20 - ext_bar),
                curses.color_pair(Config.COLOR_PAIRS['highlight']))
        except curses.error:
            pass

        # Active count
        if num_fans > 0:
            active_count = sum(
                1 for i in range(
                    1,
                    num_fans +
                    1) if (
                    sd.get(
                        f'Fan{i}_RPM',
                        0) or 0) > 0)
            try:
                self.stdscr.addstr(
                    ext_row + 2,
                    cols['NAME'] + 2,
                    f"Active: {active_count}/{num_fans} fans running",
                    curses.color_pair(
                        Config.COLOR_PAIRS['ok']))
            except curses.error:
                pass

    # ═══════════════════════════════════════════════════════════════════════════════
    #  Helper Methods
    # ═══════════════════════════════════════════════════════════════════════════════

    def _draw_section(self, y: int, x: int, title: str):
        """Draw section header."""
        try:
            self.stdscr.addstr(
                y, x, f"┌─ {title} ", curses.color_pair(
                    Config.COLOR_PAIRS['caution']) | curses.A_BOLD)
        except curses.error:
            pass

    def _draw_disconnected(self, tab_name: str):
        """Draw disconnected message."""
        try:
            self.stdscr.addstr(
                6,
                4,
                f"{tab_name} telemetry unavailable — device disconnected.",
                curses.color_pair(
                    Config.COLOR_PAIRS['error']))
        except curses.error:
            pass

    def _draw_bar(
            self,
            y: int,
            x: int,
            label: str,
            value: float,
            unit: str,
            max_val: float,
            color: int,
            bar_width: int = None,
            decimals: int = 1,
            stat=None):
        """Draw progress bar with value and optional statistics."""
        if value is None:
            value = 0.0

        if bar_width is None:
            bar_width = Config.Layout.BAR_WIDTH

        filled = 0
        if max_val > 0:
            filled = max(0, min(bar_width, int((value / max_val) * bar_width)))

        val_str = f"{value:>7.{decimals}f}"
        bar = "█" * filled + "░" * (bar_width - filled)

        try:
            self.stdscr.addstr(
                y, x, f"{label:<14} {val_str} {unit:<3} ", color)
            self.stdscr.addstr(bar, color)

            if stat and stat[0] is not None:
                stat_str = StatsFormatter.format_stat_string(
                    stat[0], stat[1], stat[2], decimals, unit,
                    Config.STAT_COLUMN_WIDTH)
                self.stdscr.addstr(
                    stat_str, curses.color_pair(
                        Config.COLOR_PAIRS['default']))
        except curses.error:
            pass
