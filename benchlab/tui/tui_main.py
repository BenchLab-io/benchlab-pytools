"""
Enhanced Curses-based TUI for BENCHLAB telemetry
"""

import curses
import logging
import sys
import time
from collections import deque, defaultdict
from datetime import datetime

# Benchlab imports
from benchlab.tui.__init__ import __version__
from benchlab_pycore.core import read_sensors, read_device, read_uid, translate_sensor_struct
from benchlab_pycore.core.serial_io import get_fleet_info, open_serial_connection

# Setup fleet and device
fleet_cache = []
active_device = None
active_device_info = None
active_device_index = 0
last_active_device = None
ser = None
connected = False

def show_help(stdscr, height, width):
    """Display help information in a modal window."""
    help_text = [
        "BENCHLAB TUI Help",
        "=" * 40,
        "",
        "Navigation:",
        "  ←, →          - Move between tabs",
        "  q, Q          - Quit application",
        "  ?             - Show this help",
        "",
        "Fleet Tab (Tab 0):",
        "  ↑, ↓          - Navigate device list",
        "  Enter         - Select active device",
        "",
        "Device Tab (Tab 1):",
        "  Shows connection and device information",
        "",
        "Power Tab (Tab 2):",
        "  Shows real-time power with progress bars",
        "  Displays Min/Max/Avg per channel",
        "",
        "Current Tab (Tab 3):",
        "  Shows current draw per channel",
        "  Displays Min/Max/Avg per channel",
        "",
        "Voltage Tab (Tab 4):",
        "  Shows Vdd, Vref, and VIN channel voltages",
        "  Color-coded status indicators",
        "",
        "Temperature Tab (Tab 5):",
        "  Shows chip and ambient temperatures",
        "  Temperature progress bars and stats",
        "",
        "Fans Tab (Tab 6):",
        "  Shows fan duty cycles and RPM",
        "  Fan status and statistics",
        "",
        "Global Commands:",
        "  r, R          - Reset min/max statistics",
        "",
        "Status Indicators:",
        "  Green         - Normal/Connected",
        "  Red           - Warning/Error/Disconnected",
        "  Yellow        - High/Caution",
        "  Blue          - Information",
        "",
        "Press any key to close this help"
    ]

    help_height = min(len(help_text) + 4, height - 2)
    help_width = min(70, width - 4)
    help_start_y = (height - help_height) // 2
    help_start_x = (width - help_width) // 2

    help_win = curses.newwin(help_height, help_width, help_start_y, help_start_x)
    help_win.attron(curses.color_pair(7) | curses.A_BOLD)
    help_win.border()
    help_win.attroff(curses.color_pair(7) | curses.A_BOLD)

    for i, line in enumerate(help_text):
        if i < help_height - 4:
            try:
                help_win.addstr(i + 1, 2, line[:help_width - 4])
            except curses.error:
                pass

    help_win.refresh()
    help_win.nodelay(False)
    help_win.getch()


# Enhanced telemetry data storage
telemetry_history = defaultdict(lambda: deque(maxlen=100))

def _make_channel_stats():
    return {'min': float('inf'), 'max': float('-inf'), 'sum': 0.0, 'count': 0}

# Per-device, per-channel stats: device_channel_stats[device][channel_name] -> dict
device_channel_stats = defaultdict(lambda: defaultdict(_make_channel_stats))

# Legacy device-level stats for uptime tracking
device_stats = {}


def update_channel_stat(device, channel, value):
    """Update running min/max/avg for a named channel on a device."""
    if value is None:
        return
    s = device_channel_stats[device][channel]
    if value < s['min']:
        s['min'] = value
    if value > s['max']:
        s['max'] = value
    s['sum'] += value
    s['count'] += 1


def get_channel_stat(device, channel):
    """Return (min, max, avg) or (None, None, None) if no data yet."""
    s = device_channel_stats[device][channel]
    if s['count'] == 0:
        return None, None, None
    return s['min'], s['max'], s['sum'] / s['count']


def reset_channel_stats(device):
    """Reset all channel stats for a device."""
    device_channel_stats[device].clear()


def draw_bar(stdscr, y, x, label, value, unit, max_val, color,
             bar_width=20, decimals=1, device=None, stat_key=None):
    """Single-line row: label  value unit [bar]  \u2193min\t\u2191max\t~avg"""
    filled = int((value / max_val) * bar_width) if max_val > 0 and 0 <= value <= max_val else 0
    filled = max(0, min(bar_width, filled))
    val_str = f"{value:>7.{decimals}f}"
    try:
        stdscr.addstr(y, x, f"{label:<14} {val_str} {unit} ", color)
        stdscr.addstr("█" * filled + "░" * (bar_width - filled), color)
        if device and stat_key:
            mn, mx, avg = get_channel_stat(device, stat_key)
            if mn is not None:
                stat_str = (f"\t\u2193{mn:.{decimals}f} {unit}"
                            f"\t\u2191{mx:.{decimals}f} {unit}"
                            f"\t~{avg:.{decimals}f} {unit}")
                stdscr.addstr(stat_str, curses.color_pair(7))
        return True
    except curses.error:
        return False


def tui_main(stdscr, _unused, args):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)    # Active tab / header
    curses.init_pair(2, curses.COLOR_GREEN, -1)                   # OK / Connected
    curses.init_pair(3, curses.COLOR_RED, -1)                     # Warning / Error
    curses.init_pair(4, curses.COLOR_YELLOW, -1)                  # Caution / Power
    curses.init_pair(5, curses.COLOR_CYAN, -1)                    # Temperature / Fans
    curses.init_pair(6, curses.COLOR_BLUE, -1)                    # Voltage / Info
    curses.init_pair(7, curses.COLOR_WHITE, -1)                   # Default text
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Ext / highlight

    tui_refresh_interval = args.interval
    stdscr.nodelay(True)
    stdscr.timeout(500)

    TAB_NAMES = ["Fleet", "Device", "Power", "Current", "Voltage", "Temperature", "Fans"]
    current_tab = 0

    global fleet_cache, active_device, active_device_info, active_device_index
    global last_active_device, ser, connected

    detected_fleet = get_fleet_info()
    fleet_cache = sorted(detected_fleet, key=lambda d: d["port"])

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        MIN_ROWS, MIN_COLS = 35, 100

        # --- Terminal size check ---
        if height < MIN_ROWS or width < MIN_COLS:
            msg = (f"[!] Terminal too small ({width}x{height})"
                   f" - resize to at least {MIN_COLS}x{MIN_ROWS}")
            stdscr.addstr(0, 0, msg, curses.A_BOLD | curses.color_pair(3))
            stdscr.refresh()
            try:
                key = stdscr.getkey()
                if key in ['q', 'Q']:
                    break
            except curses.error:
                pass
            time.sleep(0.5)
            continue

        # --- Header ---
        header = f"BENCHLAB Telemetry (TUI) v{__version__} - Press 'q' to quit, '?' for help"
        stdscr.addstr(0, 0, header.center(width), curses.A_BOLD | curses.color_pair(1))

        # --- Tabs ---
        tab_width = width // len(TAB_NAMES)
        for i, name in enumerate(TAB_NAMES):
            x = i * tab_width
            if i == current_tab:
                stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
                stdscr.addstr(2, x, f"[{name}]".center(tab_width))
                stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
            else:
                stdscr.addstr(2, x, f" {name} ".center(tab_width))

        # --- Serial connection management ---
        if last_active_device != active_device:
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            if active_device:
                ser = open_serial_connection(active_device)
            last_active_device = active_device
            connected = False

        sensor_data = None
        device_info, sensor_struct, uid = None, None, None

        if ser:
            try:
                device_info = read_device(ser)
                sensor_struct = read_sensors(ser)
                uid = read_uid(ser)
                connected = True
                sensor_data = translate_sensor_struct(sensor_struct)
            except Exception:
                connected = False
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
        else:
            connected = False

        # --- Telemetry processing & per-channel stats ---
        if connected and sensor_data and active_device:
            telemetry_history[active_device].append({
                'timestamp': time.time(),
                'data': sensor_data.copy()
            })

            # Initialise device-level record (connection time) once
            if active_device not in device_stats:
                device_stats[active_device] = {'connection_time': datetime.now()}

            # Update per-channel stats for every numeric sensor
            for key, val in sensor_data.items():
                if isinstance(val, (int, float)):
                    update_channel_stat(active_device, key, val)

        # ------------------------------------------------------------------ #
        #  Fleet tab                                                          #
        # ------------------------------------------------------------------ #
        if current_tab == 0:
            stdscr.addstr(4, 2, "## BENCHLAB Fleet ##", curses.color_pair(5))
            if not fleet_cache:
                stdscr.addstr(6, 4, "No connected devices found.", curses.color_pair(3))
            else:
                try:
                    stdscr.addstr(6, 2,
                                  f"{'':4} {'Port':<16} {'Firmware':<12} {'UID':<26} {'Status':<14} {'Active'}",
                                  curses.A_UNDERLINE | curses.color_pair(7))
                    for i, dev in enumerate(fleet_cache):
                        port     = dev.get('port', 'Unknown')
                        firmware = dev.get('firmware', 0)
                        dev_uid  = dev.get('uid', 'Unknown')

                        # Cursor: which row the user has highlighted with arrow keys
                        cursor   = "->" if i == active_device_index else "  "
                        # Active: which device is selected/connected
                        is_active    = (active_device is not None and port == active_device)
                        is_connected = is_active and connected
                        active_mark  = "[ACTIVE]" if is_active else ""

                        status       = "CONNECTED" if is_connected else "DISCONNECTED"
                        status_color = curses.color_pair(2) if is_connected else curses.color_pair(3)
                        row_color    = curses.color_pair(1) if i == active_device_index else curses.color_pair(7)

                        stdscr.addstr(8 + i, 2, f"{cursor} {port:<16} 0x{firmware:<10} {dev_uid:<26}", row_color)
                        stdscr.addstr(8 + i, 58, f"{status:<14}", status_color)
                        if is_active:
                            stdscr.addstr(f"{active_mark}", curses.color_pair(1) | curses.A_BOLD)
                except curses.error:
                    pass

            status_text = f"Status: {'CONNECTED' if connected else 'DISCONNECTED'}"
            status_color = curses.color_pair(2) if connected else curses.color_pair(3)
            try:
                stdscr.addstr(height - 3, 2, status_text, status_color | curses.A_BOLD)
                if connected and active_device and active_device in device_stats:
                    uptime = datetime.now() - device_stats[active_device]['connection_time']
                    uptime_str = str(uptime).split('.')[0]
                    stdscr.addstr(height - 2, 2, f"Uptime: {uptime_str}", curses.color_pair(4))
                else:
                    stdscr.addstr(height - 2, 2, "Uptime: N/A", curses.color_pair(3))
            except curses.error:
                pass

        # ------------------------------------------------------------------ #
        #  Device tab                                                         #
        # ------------------------------------------------------------------ #
        elif current_tab == 1:
            stdscr.addstr(4, 2, "## BENCHLAB Connection ##")
            if connected and device_info and sensor_struct is not None:
                try:
                    port_str = getattr(ser, 'port', 'Unknown')
                    stdscr.addstr(6, 4, f"{'Port':<20} {port_str}")

                    # VendorId / ProductId / FwVersion live on sensor_struct, not device_info
                    vendor_id  = getattr(sensor_struct, 'VendorId',  None) or getattr(device_info, 'VendorId',  0)
                    product_id = getattr(sensor_struct, 'ProductId', None) or getattr(device_info, 'ProductId', 0)
                    fw_version = getattr(sensor_struct, 'FwVersion', None) or getattr(device_info, 'FwVersion', 0)

                    stdscr.addstr(9, 2, "## BENCHLAB Device ##")
                    stdscr.addstr(11, 4, f"{'Vendor ID':<20} 0x{vendor_id:04X}")
                    stdscr.addstr(12, 4, f"{'Product ID':<20} 0x{product_id:04X}")
                    stdscr.addstr(13, 4, f"{'Device UID':<20} {uid}")
                    stdscr.addstr(14, 4, f"{'Firmware Version':<20} 0x{fw_version:04X}")

                    stdscr.addstr(17, 2, "## BENCHLAB Configuration ##")
                    fan_switch = getattr(sensor_struct, 'FanSwitchStatus', 'Unknown')
                    rgb_switch = getattr(sensor_struct, 'RGBSwitchStatus', 'Unknown')
                    rgb_ext    = getattr(sensor_struct, 'RGBExtStatus',    'Unknown')

                    stdscr.addstr(19, 4, f"{'Fan Switch':<20} {fan_switch}")
                    stdscr.addstr(20, 4, f"{'RGB Switch':<20} {rgb_switch}")
                    stdscr.addstr(21, 4, f"{'RGB Ext':<20} {rgb_ext}")

                    stdscr.addstr(24, 2, "## TUI Configuration ##")
                    stdscr.addstr(26, 4, f"{'TUI Refresh':<20} {tui_refresh_interval} sec")
                except curses.error:
                    pass
            else:
                stdscr.addstr(6, 4, "Device disconnected! Waiting to reconnect...", curses.color_pair(3))

        # ------------------------------------------------------------------ #
        #  Power tab                                                          #
        # ------------------------------------------------------------------ #
        elif current_tab == 2:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## Power Telemetry ##", curses.color_pair(5) | curses.A_BOLD)

                    power_channels = [
                        ('SYS_Power',    'SYS Power'),
                        ('CPU_Power',    'CPU Power'),
                        ('GPU_Power',    'GPU Power'),
                        ('MB_Power',     'MB Power'),
                        ('EPS1_Power',   'EPS1 Power'),
                        ('EPS2_Power',   'EPS2 Power'),
                        ('PCIE8_1_Power','PCIE8_1'),
                        ('PCIE8_2_Power','PCIE8_2'),
                        ('PCIE8_3_Power','PCIE8_3'),
                        ('HPWR1_Power',  'HPWR1'),
                        ('HPWR2_Power',  'HPWR2'),
                    ]

                    max_power = 500
                    row = 6
                    for key, label in power_channels:
                        val = sensor_data.get(key) or 0.0
                        draw_bar(stdscr, row, 2, label, val, 'W', max_power,
                                 curses.color_pair(5), decimals=0,
                                 device=active_device, stat_key=key)
                        row += 1

                except curses.error:
                    pass
            else:
                stdscr.addstr(4, 2, "Power telemetry unavailable - device disconnected!",
                              curses.color_pair(3))

        # ------------------------------------------------------------------ #
        #  Current tab                                                        #
        # ------------------------------------------------------------------ #
        elif current_tab == 3:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## Current Telemetry ##", curses.color_pair(5) | curses.A_BOLD)

                    current_channels = [
                        ('EPS1_Current',   'EPS1'),
                        ('EPS2_Current',   'EPS2'),
                        ('PCIE8_1_Current','PCIE8_1'),
                        ('PCIE8_2_Current','PCIE8_2'),
                        ('PCIE8_3_Current','PCIE8_3'),
                        ('HPWR1_Current',  'HPWR1'),
                        ('HPWR2_Current',  'HPWR2'),
                    ]

                    max_current = 50
                    row = 6
                    for key, label in current_channels:
                        val = sensor_data.get(key) or 0.0
                        draw_bar(stdscr, row, 2, label, val, 'A', max_current,
                                 curses.color_pair(5), decimals=1,
                                 device=active_device, stat_key=key)
                        row += 1

                except curses.error:
                    pass
            else:
                stdscr.addstr(4, 2, "Current telemetry unavailable - device disconnected!",
                              curses.color_pair(3))

        # ------------------------------------------------------------------ #
        #  Voltage tab                                                        #
        # ------------------------------------------------------------------ #
        elif current_tab == 4:
            if connected and sensor_data and sensor_struct is not None:
                try:
                    stdscr.addstr(4, 2, "## Voltage Monitoring ##",
                                  curses.color_pair(6) | curses.A_BOLD)

                    def fmt_voltage_row(y, x, name, val, key, ok_lo=None, ok_hi=None):
                        """Draw one voltage row: name  val V [STATUS]  ↓min ↑max ~avg"""
                        if val is None:
                            val = 0.0
                        if ok_lo is None:
                            ok_lo, ok_hi = 0.5, 15.0
                        if val == 0:
                            status, color = 'N/A', curses.color_pair(7)
                        elif val < ok_lo:
                            status, color = 'LOW', curses.color_pair(3)
                        elif val > ok_hi:
                            status, color = 'HIGH', curses.color_pair(4)
                        else:
                            status, color = 'OK ', curses.color_pair(2)
                        val_str = f"{val:>7.3f}" if val != 0 else "    N/A"
                        mn, mx, avg = get_channel_stat(active_device, key) if active_device else (None, None, None)
                        stat_str = f"\t↓{mn:.3f} V\t↑{mx:.3f} V\t~{avg:.3f} V" if mn is not None else ""
                        try:
                            stdscr.addstr(y, x, f"{name:<14}", color)
                            stdscr.addstr(f"{val_str} V ", color)
                            stdscr.addstr(f"[{status}]", color | curses.A_BOLD)
                            stdscr.addstr(stat_str, curses.color_pair(7))
                        except curses.error:
                            pass

                    row = 6

                    # --- Per-rail voltages (EPS / PCIE / HPWR) ---
                    stdscr.addstr(row, 2, "Rail Voltages", curses.color_pair(6) | curses.A_UNDERLINE)
                    row += 1
                    rail_voltage_channels = [
                        ('EPS1_Voltage',    'EPS1'),
                        ('EPS2_Voltage',    'EPS2'),
                        ('PCIE8_1_Voltage', 'PCIE8_1'),
                        ('PCIE8_2_Voltage', 'PCIE8_2'),
                        ('PCIE8_3_Voltage', 'PCIE8_3'),
                        ('HPWR1_Voltage',   'HPWR1'),
                        ('HPWR2_Voltage',   'HPWR2'),
                    ]
                    for key, label in rail_voltage_channels:
                        val = sensor_data.get(key) or 0.0
                        fmt_voltage_row(row, 2, label, val, key, ok_lo=10.0, ok_hi=14.0)
                        row += 1

                    row += 1  # blank separator

                    # --- Vdd and Vref ---
                    stdscr.addstr(row, 2, "System Rails", curses.color_pair(6) | curses.A_UNDERLINE)
                    row += 1
                    vdd  = sensor_data.get('Vdd')  or 0.0
                    vref = sensor_data.get('Vref') or 0.0
                    fmt_voltage_row(row,     2, 'Vdd',  vdd,  'Vdd',  ok_lo=3.0, ok_hi=3.6)
                    fmt_voltage_row(row + 1, 2, 'Vref', vref, 'Vref', ok_lo=1.6, ok_hi=2.0)
                    row += 3  # blank separator

                    # --- VIN channels: single column ---
                    stdscr.addstr(row, 2, "VIN Channels", curses.color_pair(6) | curses.A_UNDERLINE)
                    row += 1
                    vin_vals = sensor_struct.Vin if sensor_struct.Vin else []
                    for idx, _ in enumerate(vin_vals):
                        vin_key = f"VIN_{idx}"
                        vin_val = sensor_data.get(vin_key) or 0.0
                        fmt_voltage_row(row, 2, vin_key, vin_val, vin_key)
                        row += 1

                except curses.error:
                    pass
            else:
                stdscr.addstr(4, 2, "Voltage telemetry unavailable - device disconnected!",
                              curses.color_pair(3))

        # ------------------------------------------------------------------ #
        #  Temperature tab                                                    #
        # ------------------------------------------------------------------ #
        elif current_tab == 5:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## Temperature Monitoring ##",
                                  curses.color_pair(4) | curses.A_BOLD)

                    chip_temp    = sensor_data.get('Chip_Temp') or 0.0
                    ambient_temp = sensor_data.get('Ambient_Temp') or 0.0
                    humidity     = sensor_data.get('Humidity') or 0.0

                    temp_color = curses.color_pair(2) if chip_temp < 70 else curses.color_pair(3)

                    draw_bar(stdscr, 6, 2, 'Chip Temp', chip_temp, '°C', 100,
                             temp_color, decimals=1,
                             device=active_device, stat_key='Chip_Temp')

                    draw_bar(stdscr, 7, 2, 'Ambient Temp', ambient_temp, '°C', 60,
                             curses.color_pair(4), decimals=1,
                             device=active_device, stat_key='Ambient_Temp')

                    draw_bar(stdscr, 8, 2, 'Humidity', humidity, '%', 100,
                             curses.color_pair(5), decimals=1,
                             device=active_device, stat_key='Humidity')

                    stdscr.addstr(10, 2, '## Temperature Sensors ##', curses.color_pair(5))
                    row = 11
                    for i in range(4):
                        key = f'Temp_Sensor_{i+1}'
                        val = sensor_data.get(key) or 0.0
                        s_color = curses.color_pair(2) if 0 < val < 60 else (
                            curses.color_pair(3) if val >= 60 else curses.color_pair(7))
                        draw_bar(stdscr, row, 2, f'Sensor {i+1}', val, '°C', 100,
                                 s_color, decimals=1,
                                 device=active_device, stat_key=key)
                        row += 1

                except curses.error:
                    pass
            else:
                stdscr.addstr(4, 2, "Temperature telemetry unavailable - device disconnected!",
                              curses.color_pair(3))

        # ------------------------------------------------------------------ #
        #  Fans tab                                                           #
        # ------------------------------------------------------------------ #
        elif current_tab == 6:
            if connected and sensor_data and sensor_struct is not None:
                try:
                    stdscr.addstr(4, 2, "## Fan Control & Monitoring ##", curses.color_pair(5))
                    stdscr.addstr(6, 2,
                                  f"{'Fan':<8} {'Duty':<6} {'RPM':<8} {'Enabled':<10} {'Bar (3000 RPM max)'}",
                                  curses.A_UNDERLINE | curses.color_pair(7))

                    fans = sensor_struct.Fans if sensor_struct.Fans else []
                    for i, f in enumerate(fans):
                        duty    = getattr(f, 'Duty', 0) or 0
                        rpm     = getattr(f, 'Tach', 0) or 0
                        enabled = getattr(f, 'Enable', False)

                        rpm_key  = f'Fan{i+1}_RPM'
                        duty_key = f'Fan{i+1}_Duty'
                        update_channel_stat(active_device, rpm_key, rpm)
                        update_channel_stat(active_device, duty_key, duty)

                        max_rpm = 3000
                        rpm_bar = max(0, min(20, int((rpm / max_rpm) * 20)))
                        fan_color = curses.color_pair(2) if rpm > 0 else curses.color_pair(3)

                        row = 8 + i
                        try:
                            stdscr.addstr(row, 2,
                                          f"{'Fan'+str(i+1):<8} {duty:<6} {rpm:<8} {str(enabled):<10} ",
                                          fan_color)
                            stdscr.addstr("█" * rpm_bar + "░" * (20 - rpm_bar), fan_color)
                            # Inline stats: RPM then Duty
                            mn_r, mx_r, avg_r = get_channel_stat(active_device, rpm_key)
                            mn_d, mx_d, avg_d = get_channel_stat(active_device, duty_key)
                            if mn_r is not None:
                                stdscr.addstr(
                                    f"\t↓{mn_r:.0f} RPM\t↑{mx_r:.0f} RPM\t~{avg_r:.0f} RPM"
                                    f"\t|↓{mn_d:.0f}%\t↑{mx_d:.0f}%\t~{avg_d:.0f}%",
                                    curses.color_pair(7))
                        except curses.error:
                            pass

                    # External fan
                    ext_duty = sensor_data.get('FanExtDuty') or 0
                    ext_bar = max(0, min(20, int(ext_duty / 5)))
                    ext_row = 8 + len(fans) + 1
                    try:
                        stdscr.addstr(ext_row, 2,
                                      f"{'Ext Fan':<8} {ext_duty:<6} {'N/A':<8} {'N/A':<10} ",
                                      curses.color_pair(8))
                        stdscr.addstr("█" * ext_bar + "░" * (20 - ext_bar), curses.color_pair(8))
                    except curses.error:
                        pass

                    # Summary
                    active_count = sum(1 for f in fans if (getattr(f, 'Tach', 0) or 0) > 0)
                    summary_row = ext_row + 2
                    try:
                        stdscr.addstr(summary_row, 2,
                                      f"Active Fans: {active_count}/{len(fans)}",
                                      curses.color_pair(2))
                    except curses.error:
                        pass

                except curses.error:
                    pass
            else:
                stdscr.addstr(4, 2, "Fan telemetry unavailable - device disconnected!",
                              curses.color_pair(3))

        # ------------------------------------------------------------------ #
        #  Key handling                                                       #
        # ------------------------------------------------------------------ #
        try:
            key = stdscr.getkey()
            if key in ['q', 'Q']:
                break
            elif key == '?':
                show_help(stdscr, height, width)
            elif key in ['KEY_RIGHT', 'l']:
                current_tab = (current_tab + 1) % len(TAB_NAMES)
            elif key in ['KEY_LEFT', 'h']:
                current_tab = (current_tab - 1) % len(TAB_NAMES)
            elif key == "KEY_RESIZE":
                pass
            elif key in ['r', 'R']:
                if active_device:
                    reset_channel_stats(active_device)
            elif current_tab == 0 and fleet_cache:
                if key == 'KEY_UP':
                    active_device_index = (active_device_index - 1) % len(fleet_cache)
                elif key == 'KEY_DOWN':
                    active_device_index = (active_device_index + 1) % len(fleet_cache)
                elif key in ('\n', '\r', 'KEY_ENTER'):
                    if ser:
                        try:
                            ser.close()
                        except Exception:
                            pass
                        ser = None
                    active_device_info = fleet_cache[active_device_index]
                    active_device = active_device_info["port"]
                    ser = open_serial_connection(active_device)
        except KeyboardInterrupt:
            break
        except curses.error:
            pass

        stdscr.refresh()