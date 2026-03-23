"""
Enhanced Curses-based TUI for BENCHLAB telemetry
"""

import curses
import io
import logging
import sys
import time
import threading
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
        "  h, k, ←, ↑    - Move to previous tab",
        "  l, j, →, ↓    - Move to next tab",
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
        "  Shows real-time power consumption with progress bars",
        "  Displays efficiency and statistics",
        "",
        "Voltage Tab (Tab 3):",
        "  Shows Vdd, Vref, and VIN channel voltages",
        "  Color-coded status indicators",
        "",
        "Temperature Tab (Tab 4):",
        "  Shows chip and ambient temperatures",
        "  Temperature progress bars and thermal status",
        "",
        "Fans Tab (Tab 5):",
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
    
    # Create help window
    help_height = min(len(help_text) + 4, height - 2)
    help_width = min(70, width - 4)
    help_start_y = (height - help_height) // 2
    help_start_x = (width - help_width) // 2
    
    # Draw help window
    help_win = curses.newwin(help_height, help_width, help_start_y, help_start_x)
    help_win.attron(curses.color_pair(7) | curses.A_BOLD)
    help_win.border()
    help_win.attroff(curses.color_pair(7) | curses.A_BOLD)
    
    # Add help content
    for i, line in enumerate(help_text):
        if i < help_height - 4:  # Leave space for border
            try:
                help_win.addstr(i + 1, 2, line[:help_width - 4])
            except curses.error:
                pass
    
    help_win.refresh()
    
    # Wait for key press
    help_win.nodelay(False)
    help_win.getch()
    help_win.nodelay(True)

# Enhanced telemetry data storage
telemetry_history = defaultdict(lambda: deque(maxlen=100))
device_stats = defaultdict(lambda: {
    'min_power': float('inf'), 'max_power': 0, 'avg_power': 0,
    'min_temp': float('inf'), 'max_temp': 0, 'avg_temp': 0,
    'connection_time': None, 'disconnect_count': 0
})
last_update_time = {}

def tui_main(stdscr, _unused, args):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    
    # Simplified color scheme
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)    # Active tab background
    curses.init_pair(2, curses.COLOR_GREEN, -1)                   # Status OK / Connected
    curses.init_pair(3, curses.COLOR_RED, -1)                     # Warnings/errors / Disconnected
    curses.init_pair(4, curses.COLOR_YELLOW, -1)                  # Power readings / Warnings
    curses.init_pair(5, curses.COLOR_CYAN, -1)                    # Temperature readings
    curses.init_pair(6, curses.COLOR_BLUE, -1)                    # Voltage readings / Info
    curses.init_pair(7, curses.COLOR_WHITE, -1)                   # Inactive tab text
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Title bar background

    tui_refresh_interval = args.interval
    stdscr.nodelay(True)
    stdscr.timeout(500)

    TAB_NAMES = ["Fleet", "Device", "Power", "Current", "Voltage", "Temperature", "Fans"]
    current_tab = 0

    global fleet_cache, active_device, active_device_info, active_device_index, last_active_device, ser, connected

    # Initialize fleet cache
    detected_fleet = get_fleet_info()
    fleet_cache = sorted(detected_fleet, key=lambda d: d["port"])

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        MIN_ROWS, MIN_COLS = 35, 100

        # --- Terminal size check ---
        if height < MIN_ROWS or width < MIN_COLS:
            msg = f"[!] Terminal too small ({width}x{height}) - resize to at least {MIN_COLS}x{MIN_ROWS}"
            stdscr.addstr(0, 0, msg, curses.A_BOLD | curses.color_pair(3))
            stdscr.refresh()
            try:
                key = stdscr.getkey()
                if key in ['q', 'Q']:
                    break
                elif key == "KEY_RESIZE":
                    continue
            except curses.error:
                pass
            time.sleep(0.5)
            continue

        # Draw header
        header = f"BENCHLAB Telemetry (TUI) v{__version__} - Press 'q' to quit"
        stdscr.addstr(0, 0, header.center(width), curses.A_BOLD | curses.color_pair(1))

        # Draw tabs
        for i, name in enumerate(TAB_NAMES):
            x = i * (width // len(TAB_NAMES))
            if i == current_tab:
                stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
                stdscr.addstr(2, x, f"[{name}]")
                stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
            else:
                stdscr.addstr(2, x, f" {name} ")

        # --- Ensure serial connection ---
        if last_active_device != active_device:
            if ser:
                try: ser.close()
                except: pass
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
                try: ser.close() 
                except: pass
                ser = None
        else:
            connected = False

        # --- Enhanced telemetry processing ---
        if connected and sensor_data and active_device:
            # Store telemetry history
            telemetry_history[active_device].append({
                'timestamp': time.time(),
                'data': sensor_data.copy()
            })
            
            # Update statistics
            if active_device not in device_stats:
                device_stats[active_device]['connection_time'] = datetime.now()
            
            # Power statistics
            total_power = sensor_data.get('SYS_Power', 0)
            stats = device_stats[active_device]
            stats['min_power'] = min(stats['min_power'], total_power)
            stats['max_power'] = max(stats['max_power'], total_power)
            
            # Temperature statistics (use chip temp as representative)
            chip_temp = sensor_data.get('Chip_Temp', 0)
            stats['min_temp'] = min(stats['min_temp'], chip_temp)
            stats['max_temp'] = max(stats['max_temp'], chip_temp)
            
            # Debug: Print available sensor keys once when connected
            if not hasattr(tui_main, '_debug_printed') and sensor_data:
                print("Available sensor keys:", sorted(sensor_data.keys()))
                tui_main._debug_printed = True

        # --- Fleet tab ---
        if current_tab == 0:
            stdscr.addstr(4, 2, "## BENCHLAB Fleet ##", curses.color_pair(5))
            if not fleet_cache:
                stdscr.addstr(6, 4, "No connected devices found.", curses.color_pair(3))
            else:
                try:
                    stdscr.addstr(6, 2, f"{'':<4} {'Port':<12} {'Firmware':<10} {'UID':<24} {'Status':<12}", curses.A_UNDERLINE | curses.color_pair(7))
                    for i, dev in enumerate(fleet_cache):
                        prefix = "->" if i == active_device_index else "  "
                        active_mark = " [ACTIVE]" if active_device and dev.get('port') == active_device else ""
                        status = "CONNECTED" if (active_device == dev.get('port') and connected) else "DISCONNECTED"
                        status_color = curses.color_pair(2) if status == "CONNECTED" else curses.color_pair(3)
                        
                        port = dev.get('port', 'Unknown')
                        firmware = dev.get('firmware', 0)
                        uid = dev.get('uid', 'Unknown')
                        
                        stdscr.addstr(8 + i, 4, f"{prefix} {port:<12} 0x{firmware:<8} {uid:<24}", curses.color_pair(7))
                        stdscr.addstr(f"{active_mark}", curses.color_pair(1))
                        stdscr.addstr(8 + i, 61, f"{status:<12}", status_color)
                except curses.error:
                    pass  # Skip if terminal size issues

            # Connection status indicator
            status_text = f"Status: {'CONNECTED' if connected else 'DISCONNECTED'}"
            status_color = curses.color_pair(2) if connected else curses.color_pair(3)
            try:
                stdscr.addstr(height - 3, 2, status_text, status_color | curses.A_BOLD)
                if connected and active_device and active_device in device_stats:
                    uptime = datetime.now() - device_stats[active_device]['connection_time']
                    uptime_str = str(uptime).split('.')[0]  # Remove microseconds
                    stdscr.addstr(height - 2, 2, f"Uptime: {uptime_str}", curses.color_pair(4))
                else:
                    stdscr.addstr(height - 2, 2, "Uptime: N/A", curses.color_pair(3))
            except curses.error:
                pass  # Skip if terminal size issues

        # --- Device tab ---
        elif current_tab == 1:
            stdscr.addstr(4, 2, "## BENCHLAB Connection ##")
            if connected and device_info:
                try:
                    port_str = getattr(ser, 'port', 'Unknown')
                    stdscr.addstr(6, 4, f"{'Port':<20} {port_str}")
                    
                    vendor_id = getattr(device_info, 'VendorId', 0)
                    product_id = getattr(device_info, 'ProductId', 0)
                    fw_version = getattr(device_info, 'FwVersion', 0)
                    
                    stdscr.addstr(9, 2, "## BENCHLAB Device ##")
                    stdscr.addstr(11, 4, f"{'Vendor ID':<20} 0x{vendor_id:03X}")
                    stdscr.addstr(12, 4, f"{'Product ID':<20} 0x{product_id:03X}")
                    stdscr.addstr(13, 4, f"{'Device UID':<20} {uid}")
                    stdscr.addstr(14, 4, f"{'Firmware Version':<20} 0x{fw_version:02X}")

                    stdscr.addstr(17, 2, "## BENCHLAB Configuration ##")
                    fan_switch = getattr(sensor_struct, 'FanSwitchStatus', 'Unknown')
                    rgb_switch = getattr(sensor_struct, 'RGBSwitchStatus', 'Unknown')
                    rgb_ext = getattr(sensor_struct, 'RGBExtStatus', 'Unknown')
                    
                    stdscr.addstr(19, 4, f"{'Fan Switch':<20} {fan_switch}")
                    stdscr.addstr(20, 4, f"{'RGB Switch':<20} {rgb_switch}")
                    stdscr.addstr(21, 4, f"{'RGB Ext':<20} {rgb_ext}")

                    stdscr.addstr(24, 2, "## TUI Configuration ##")
                    stdscr.addstr(26, 4, f"{'TUI Refresh':<20} {tui_refresh_interval} sec")
                except curses.error:
                    pass  # Skip if terminal size issues
            else:
                stdscr.addstr(6, 4, "Device disconnected! Waiting to reconnect...", curses.color_pair(3))

        # --- Enhanced Power tab ---
        elif current_tab == 2:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## System Telemetry ##", curses.color_pair(5) | curses.A_BOLD)
                    
                    # Current power readings with safe access
                    sys_power = sensor_data.get('SYS_Power', 0)
                    cpu_power = sensor_data.get('CPU_Power', 0)
                    gpu_power = sensor_data.get('GPU_Power', 0)
                    mb_power = sensor_data.get('MB_Power', 0)
                    
                    # Additional power rails
                    vdd_power = sensor_data.get('Vdd_Power', 0)
                    vref_power = sensor_data.get('Vref_Power', 0)
                    
                    # Additional power metrics
                    eps1_power = sensor_data.get('EPS1_Power', 0)
                    eps2_power = sensor_data.get('EPS2_Power', 0)
                    pcie8_1_power = sensor_data.get('PCIE8_1_Power', 0)
                    pcie8_2_power = sensor_data.get('PCIE8_2_Power', 0)
                    pcie8_3_power = sensor_data.get('PCIE8_3_Power', 0)
                    hpwr1_power = sensor_data.get('HPWR1_Power', 0)
                    hpwr2_power = sensor_data.get('HPWR2_Power', 0)
                    
                    # Power progress bars (assuming max 500W for visualization)
                    max_power = 500
                    sys_bar = int((sys_power / max_power) * 20) if 0 <= sys_power <= max_power else 0
                    cpu_bar = int((cpu_power / max_power) * 20) if 0 <= cpu_power <= max_power else 0
                    gpu_bar = int((gpu_power / max_power) * 20) if 0 <= gpu_power <= max_power else 0
                    mb_bar = int((mb_power / max_power) * 20) if 0 <= mb_power <= max_power else 0
                    eps1_bar = int((eps1_power / max_power) * 20) if 0 <= eps1_power <= max_power else 0
                    eps2_bar = int((eps2_power / max_power) * 20) if 0 <= eps2_power <= max_power else 0
                    pcie8_1_bar = int((pcie8_1_power / max_power) * 20) if 0 <= pcie8_1_power <= max_power else 0
                    pcie8_2_bar = int((pcie8_2_power / max_power) * 20) if 0 <= pcie8_2_power <= max_power else 0
                    pcie8_3_bar = int((pcie8_3_power / max_power) * 20) if 0 <= pcie8_3_power <= max_power else 0
                    hpwr1_bar = int((hpwr1_power / max_power) * 20) if 0 <= hpwr1_power <= max_power else 0
                    hpwr2_bar = int((hpwr2_power / max_power) * 20) if 0 <= hpwr2_power <= max_power else 0
                    
                    # Draw power bars
                    stdscr.addstr(6, 2, f"{'SYS Power':<12} {sys_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * sys_bar + "░" * (20 - sys_bar), curses.color_pair(5))
                    
                    stdscr.addstr(7, 2, f"{'CPU Power':<12} {cpu_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * cpu_bar + "░" * (20 - cpu_bar), curses.color_pair(5))
                    
                    stdscr.addstr(8, 2, f"{'GPU Power':<12} {gpu_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * gpu_bar + "░" * (20 - gpu_bar), curses.color_pair(5))
                    
                    stdscr.addstr(9, 2, f"{'MB Power':<12} {mb_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * mb_bar + "░" * (20 - mb_bar), curses.color_pair(5))
                    
                    stdscr.addstr(10, 2, f"{'EPS1 Power':<12} {eps1_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * eps1_bar + "░" * (20 - eps1_bar), curses.color_pair(5))
                    
                    stdscr.addstr(11, 2, f"{'EPS2 Power':<12} {eps2_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * eps2_bar + "░" * (20 - eps2_bar), curses.color_pair(5))
                    
                    stdscr.addstr(12, 2, f"{'PCIE8_1 Pwr':<12} {pcie8_1_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * pcie8_1_bar + "░" * (20 - pcie8_1_bar), curses.color_pair(5))
                    
                    stdscr.addstr(13, 2, f"{'PCIE8_2 Pwr':<12} {pcie8_2_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * pcie8_2_bar + "░" * (20 - pcie8_2_bar), curses.color_pair(5))
                    
                    stdscr.addstr(14, 2, f"{'PCIE8_3 Pwr':<12} {pcie8_3_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * pcie8_3_bar + "░" * (20 - pcie8_3_bar), curses.color_pair(5))
                    
                    stdscr.addstr(15, 2, f"{'HPWR1 Power':<12} {hpwr1_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * hpwr1_bar + "░" * (20 - hpwr1_bar), curses.color_pair(5))
                    
                    stdscr.addstr(16, 2, f"{'HPWR2 Power':<12} {hpwr2_power:>6.0f} W ", curses.color_pair(5))
                    stdscr.addstr("█" * hpwr2_bar + "░" * (20 - hpwr2_bar), curses.color_pair(5))
                    
                    # Statistics section with Min/Max/Avg
                    stats = device_stats.get(active_device, {})
                    stdscr.addstr(17, 2, "## Statistics ##", curses.color_pair(5))
                    stdscr.addstr(19, 4, f"{'Min Power':<12} {stats.get('min_power', 0):.0f} W", curses.color_pair(4))
                    stdscr.addstr(20, 4, f"{'Max Power':<12} {stats.get('max_power', 0):.0f} W", curses.color_pair(3))
                    stdscr.addstr(21, 4, f"{'Avg Power':<12} N/A W", curses.color_pair(5))
                except curses.error:
                    pass  # Skip if terminal size issues
            else:
                stdscr.addstr(4, 2, "Power telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Current tab (same as Power but with current telemetry) ---
        elif current_tab == 3:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## Current Telemetry ##", curses.color_pair(5) | curses.A_BOLD)
                    
                    # Current readings from actual sensor data
                    eps1_current = sensor_data.get('EPS1_Current', 0)
                    eps2_current = sensor_data.get('EPS2_Current', 0)
                    pcie8_1_current = sensor_data.get('PCIE8_1_Current', 0)
                    pcie8_2_current = sensor_data.get('PCIE8_2_Current', 0)
                    pcie8_3_current = sensor_data.get('PCIE8_3_Current', 0)
                    hpwr1_current = sensor_data.get('HPWR1_Current', 0)
                    hpwr2_current = sensor_data.get('HPWR2_Current', 0)
                    
                    # Additional current rails
                    vdd_current = sensor_data.get('Vdd_Current', 0)
                    vref_current = sensor_data.get('Vref_Current', 0)
                    
                    # Current progress bars (assuming max 50A for visualization)
                    max_current = 50
                    eps1_bar = int((eps1_current / max_current) * 20) if 0 <= eps1_current <= max_current else 0
                    eps2_bar = int((eps2_current / max_current) * 20) if 0 <= eps2_current <= max_current else 0
                    pcie8_1_bar = int((pcie8_1_current / max_current) * 20) if 0 <= pcie8_1_current <= max_current else 0
                    pcie8_2_bar = int((pcie8_2_current / max_current) * 20) if 0 <= pcie8_2_current <= max_current else 0
                    pcie8_3_bar = int((pcie8_3_current / max_current) * 20) if 0 <= pcie8_3_current <= max_current else 0
                    hpwr1_bar = int((hpwr1_current / max_current) * 20) if 0 <= hpwr1_current <= max_current else 0
                    hpwr2_bar = int((hpwr2_current / max_current) * 20) if 0 <= hpwr2_current <= max_current else 0
                    vdd_bar = int((vdd_current / max_current) * 20) if 0 <= vdd_current <= max_current else 0
                    vref_bar = int((vref_current / max_current) * 20) if 0 <= vref_current <= max_current else 0
                    
                    # Draw current bars
                    stdscr.addstr(6, 2, f"{'EPS1 Current':<12} {eps1_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * eps1_bar + "░" * (20 - eps1_bar), curses.color_pair(5))
                    
                    stdscr.addstr(7, 2, f"{'EPS2 Current':<12} {eps2_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * eps2_bar + "░" * (20 - eps2_bar), curses.color_pair(5))
                    
                    stdscr.addstr(8, 2, f"{'PCIE8_1 Cur':<12} {pcie8_1_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * pcie8_1_bar + "░" * (20 - pcie8_1_bar), curses.color_pair(5))
                    
                    stdscr.addstr(9, 2, f"{'PCIE8_2 Cur':<12} {pcie8_2_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * pcie8_2_bar + "░" * (20 - pcie8_2_bar), curses.color_pair(5))
                    
                    stdscr.addstr(10, 2, f"{'PCIE8_3 Cur':<12} {pcie8_3_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * pcie8_3_bar + "░" * (20 - pcie8_3_bar), curses.color_pair(5))
                    
                    stdscr.addstr(11, 2, f"{'HPWR1 Current':<12} {hpwr1_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * hpwr1_bar + "░" * (20 - hpwr1_bar), curses.color_pair(5))
                    
                    stdscr.addstr(12, 2, f"{'HPWR2 Current':<12} {hpwr2_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * hpwr2_bar + "░" * (20 - hpwr2_bar), curses.color_pair(5))
                    
                    stdscr.addstr(13, 2, f"{'Vdd Current':<12} {vdd_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * vdd_bar + "░" * (20 - vdd_bar), curses.color_pair(5))
                    
                    stdscr.addstr(14, 2, f"{'Vref Current':<12} {vref_current:>6.1f} A ", curses.color_pair(5))
                    stdscr.addstr("█" * vref_bar + "░" * (20 - vref_bar), curses.color_pair(5))
                    
                    # Statistics section with Min/Max/Avg
                    stats = device_stats.get(active_device, {})
                    stdscr.addstr(17, 2, "## Statistics ##", curses.color_pair(5))
                    stdscr.addstr(19, 4, f"{'Min Current':<12} N/A A", curses.color_pair(4))
                    stdscr.addstr(20, 4, f"{'Max Current':<12} N/A A", curses.color_pair(3))
                    stdscr.addstr(21, 4, f"{'Avg Current':<12} N/A A", curses.color_pair(5))
                except curses.error:
                    pass  # Skip if terminal size issues
            else:
                stdscr.addstr(4, 2, "Current telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Enhanced Voltage tab ---
        elif current_tab == 4:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## Voltage Monitoring ##", curses.color_pair(6) | curses.A_BOLD)
                    
                    # Vdd and Vref with status indicators
                    vdd = sensor_data.get('Vdd', 0)
                    vref = sensor_data.get('Vref', 0)
                    
                    # Handle None or invalid values
                    if vdd is None:
                        vdd = 0
                    if vref is None:
                        vref = 0
                    
                    # Voltage status (assuming 3.3V for Vdd and 1.8V for Vref as examples)
                    if vdd == 0:
                        vdd_status = "N/A"
                        vdd_color = curses.color_pair(7)
                    else:
                        vdd_status = "OK" if 3.0 <= vdd <= 3.6 else "LOW" if vdd < 3.0 else "HIGH"
                        vdd_color = curses.color_pair(2) if vdd_status == "OK" else curses.color_pair(3)
                    
                    if vref == 0:
                        vref_status = "N/A"
                        vref_color = curses.color_pair(7)
                    else:
                        vref_status = "OK" if 1.6 <= vref <= 2.0 else "LOW" if vref < 1.6 else "HIGH"
                        vref_color = curses.color_pair(2) if vref_status == "OK" else curses.color_pair(3)
                    
                    # Safe string formatting
                    vdd_str = f"{vdd:>8.3f}" if vdd != 0 else "N/A     "
                    vref_str = f"{vref:>8.3f}" if vref != 0 else "N/A     "
                    
                    stdscr.addstr(6, 2, f"{'Vdd':<10} {vdd_str} V ", vdd_color)
                    stdscr.addstr(f"[{vdd_status}]", vdd_color | curses.A_BOLD)
                    
                    stdscr.addstr(7, 2, f"{'Vref':<10} {vref_str} V ", vref_color)
                    stdscr.addstr(f"[{vref_status}]", vref_color | curses.A_BOLD)
                    
                    # Condensed VIN channels (4 per line)
                    stdscr.addstr(9, 2, "## VIN Channels ##", curses.color_pair(6))
                    y = 11
                    for i in range(0, len(sensor_struct.Vin), 4):
                        # Display up to 4 VIN channels per line
                        for j in range(4):
                            vin_index = i + j
                            if vin_index < len(sensor_struct.Vin):
                                name = f"VIN_{vin_index}"
                                vin = sensor_data.get(name, 0.0)
                                
                                # Handle None or invalid values
                                if vin is None:
                                    vin = 0.0
                                
                                # Voltage status
                                if vin == 0:
                                    vin_status = "N/A"
                                    vin_color = curses.color_pair(7)
                                else:
                                    vin_status = "OK" if 0.5 <= vin <= 12.0 else "LOW" if vin < 0.5 else "HIGH"
                                    vin_color = curses.color_pair(2) if vin_status == "OK" else curses.color_pair(3)
                                
                                # Safe string formatting
                                vin_str = f"{vin:>8.3f}" if vin != 0 else "N/A     "
                                
                                try:
                                    # Display VIN channel in columns
                                    x_pos = j * 20
                                    stdscr.addstr(y, 2 + x_pos, f"{name:<10} {vin_str} V ", vin_color)
                                    stdscr.addstr(f"[{vin_status}]", vin_color | curses.A_BOLD)
                                except curses.error:
                                    pass  # Skip if terminal size issues
                        y += 2
                    
                    # Voltage summary
                    stdscr.addstr(y, 2, "## Voltage Summary ##", curses.color_pair(6))
                    avg_vin = sum(sensor_struct.Vin) / len(sensor_struct.Vin) if sensor_struct.Vin else 0
                    stdscr.addstr(y + 2, 4, f"{'Avg VIN':<12} {avg_vin:.3f} V", curses.color_pair(4))
                except curses.error:
                    pass  # Skip if terminal size issues
            else:
                stdscr.addstr(4, 2, "Voltage telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Enhanced Temperature tab ---
        elif current_tab == 5:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## Temperature Monitoring ##", curses.color_pair(4) | curses.A_BOLD)
                    
                    # Chip temperature with progress bar
                    chip_temp = sensor_data.get('Chip_Temp', 0)
                    ambient_temp = sensor_data.get('Ambient_Temp', 0)
                    humidity = sensor_data.get('Humidity', 0)
                    
                    # Handle None or invalid values
                    if chip_temp is None:
                        chip_temp = 0
                    if ambient_temp is None:
                        ambient_temp = 0
                    if humidity is None:
                        humidity = 0
                    
                    # Temperature progress bars (0-100°C range)
                    chip_bar = int(chip_temp) if 0 <= chip_temp <= 100 else 0
                    ambient_bar = int(ambient_temp) if 0 <= ambient_temp <= 100 else 0
                    
                    # Temperature color coding
                    temp_color = curses.color_pair(2) if chip_temp < 70 else curses.color_pair(3)
                    
                    # Safe string formatting
                    chip_str = f"{chip_temp:>6.1f}" if chip_temp != 0 else "N/A   "
                    ambient_str = f"{ambient_temp:>6.1f}" if ambient_temp != 0 else "N/A   "
                    humidity_str = f"{humidity:>6.1f}" if humidity != 0 else "N/A   "
                    
                    stdscr.addstr(6, 2, f"{'Chip Temp':<12} {chip_str}°C ", temp_color)
                    stdscr.addstr("█" * (chip_bar // 5) + "░" * (20 - chip_bar // 5), temp_color)
                    
                    stdscr.addstr(7, 2, f"{'Ambient Temp':<12} {ambient_str}°C ", curses.color_pair(4))
                    stdscr.addstr("█" * (ambient_bar // 5) + "░" * (20 - ambient_bar // 5), curses.color_pair(4))
                    
                    stdscr.addstr(8, 2, f"{'Humidity':<12} {humidity_str} % ", curses.color_pair(8))
                    
                    # Temperature sensors with individual min/max
                    stdscr.addstr(10, 2, "## Temperature Sensors ##", curses.color_pair(5))
                    for i in range(4):
                        sensor_temp = sensor_data.get(f'Temp_Sensor_{i+1}', None)
                        
                        # Handle None or invalid sensor data
                        if sensor_temp is None:
                            sensor_temp = 0.0
                            sensor_color = curses.color_pair(7)  # White for N/A
                            temp_str = "N/A     "
                        else:
                            sensor_color = curses.color_pair(2) if sensor_temp < 60 else curses.color_pair(3)
                            temp_str = f"{sensor_temp:>6.1f}"
                        
                        try:
                            stdscr.addstr(12+i, 4, f"{'Sensor ' + str(i+1):<12} {temp_str}°C ", sensor_color)
                        except curses.error:
                            pass  # Skip if terminal size issues
                    
                    # Statistics section with individual min/max
                    stats = device_stats.get(active_device, {})
                    stdscr.addstr(17, 2, "## Temperature Stats ##", curses.color_pair(5))
                    stdscr.addstr(19, 4, f"{'Chip Min/Max':<12} {stats.get('min_temp', 0):.1f}/{stats.get('max_temp', 0):.1f}°C", curses.color_pair(4))
                    stdscr.addstr(20, 4, f"{'Ambient Min/Max':<12} N/A", curses.color_pair(4))
                    stdscr.addstr(21, 4, f"{'Humidity Min/Max':<12} N/A", curses.color_pair(4))
                    stdscr.addstr(22, 4, f"{'Avg Temp':<12} N/A°C", curses.color_pair(5))
                except curses.error:
                    pass  # Skip if terminal size issues
            else:
                stdscr.addstr(4, 2, "Temperature telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Enhanced Fans tab ---
        elif current_tab == 6:
            if connected and sensor_data:
                try:
                    stdscr.addstr(4, 2, "## Fan Control & Monitoring ##", curses.color_pair(5))
                    
                    # Fan headers
                    stdscr.addstr(6, 2, f"{'Fan':<8} {'Duty (%)':<10} {'RPM':<10} {'Status':<10} {'Bar'}", curses.A_UNDERLINE)
                    
                    # Draw fan information with progress bars
                    for i, f in enumerate(sensor_struct.Fans):
                        fan_num = i + 1
                        duty = getattr(f, 'Duty', 0)
                        rpm = getattr(f, 'Tach', 0)
                        enabled = getattr(f, 'Enable', False)
                        
                        # Handle None or invalid values
                        if duty is None:
                            duty = 0
                        if rpm is None:
                            rpm = 0
                        
                        # Fan RPM progress bar (assuming max 3000 RPM for visualization)
                        max_rpm = 3000
                        rpm_bar = int((rpm / max_rpm) * 20) if 0 <= rpm <= max_rpm else 0
                        
                        # Color coding based on RPM (active if RPM > 0)
                        fan_color = curses.color_pair(2) if rpm > 0 else curses.color_pair(3)
                        
                        try:
                            stdscr.addstr(8+i, 2, f"{'Fan'+str(fan_num):<8} {duty:<10} {rpm:<10} {enabled:<10} ", fan_color)
                            stdscr.addstr("█" * rpm_bar + "░" * (20 - rpm_bar), fan_color)
                        except curses.error:
                            pass  # Skip if terminal size issues
                    
                    # External fan duty
                    ext_duty = sensor_data.get('FanExtDuty', 0)
                    
                    # Handle None or invalid values
                    if ext_duty is None:
                        ext_duty = 0
                    
                    ext_bar = int(ext_duty / 5) if 0 <= ext_duty <= 100 else 0
                    try:
                        stdscr.addstr(8+len(sensor_struct.Fans)+1, 2, f"{'Ext Fan':<8} {ext_duty:<10} {'N/A':<10} {'N/A':<10} ", curses.color_pair(8))
                        stdscr.addstr("█" * ext_bar + "░" * (20 - ext_bar), curses.color_pair(8))
                    except curses.error:
                        pass  # Skip if terminal size issues
                    
                    # Fan statistics
                    stdscr.addstr(12+len(sensor_struct.Fans), 2, "## Fan Statistics ##", curses.color_pair(5))
                    if sensor_struct.Fans:
                        valid_fans = [f for f in sensor_struct.Fans if getattr(f, 'Duty', 0) is not None and getattr(f, 'Tach', 0) is not None]
                        if valid_fans:
                            avg_duty = sum(getattr(f, 'Duty', 0) for f in valid_fans) / len(valid_fans)
                            max_rpm = max(getattr(f, 'Tach', 0) for f in valid_fans)
                        else:
                            avg_duty = 0
                            max_rpm = 0
                    else:
                        avg_duty = 0
                        max_rpm = 0
                    
                    try:
                        stdscr.addstr(14+len(sensor_struct.Fans), 4, f"{'Avg Duty':<12} {avg_duty:.1f}%", curses.color_pair(4))
                        stdscr.addstr(15+len(sensor_struct.Fans), 4, f"{'Max RPM':<12} {max_rpm}", curses.color_pair(5))
                    except curses.error:
                        pass  # Skip if terminal size issues
                    
                    # Fan status summary (active if RPM > 0)
                    if sensor_struct.Fans:
                        active_count = sum(1 for f in sensor_struct.Fans if getattr(f, 'Tach', 0) > 0)
                        total_fans = len(sensor_struct.Fans)
                    else:
                        active_count = 0
                        total_fans = 0
                    
                    try:
                        stdscr.addstr(17+len(sensor_struct.Fans), 4, f"{'Active Fans':<12} {active_count}/{total_fans}", curses.color_pair(2))
                    except curses.error:
                        pass  # Skip if terminal size issues
                except curses.error:
                    pass  # Skip if terminal size issues
            else:
                stdscr.addstr(4, 2, "Fan telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Enhanced Key handling ---
        try:
            key = stdscr.getkey()
            if key in ['KEY_RIGHT', 'l', 'j']:
                current_tab = (current_tab + 1) % len(TAB_NAMES)
            elif key in ['KEY_LEFT', 'h', 'k']:
                current_tab = (current_tab - 1) % len(TAB_NAMES)
            elif key in ['q', 'Q']:
                break
            elif key == "KEY_RESIZE":
                continue
            elif key in ['?', 'H', 'h']:  # Help
                show_help(stdscr, height, width)
            elif key in ['r', 'R']:  # Refresh stats
                if active_device and active_device in device_stats:
                    stats = device_stats[active_device]
                    stats['min_power'] = float('inf')
                    stats['max_power'] = 0
                    stats['min_temp'] = float('inf')
                    stats['max_temp'] = 0
            elif current_tab == 0 and fleet_cache:
                if key == 'KEY_UP':
                    active_device_index = (active_device_index - 1) % len(fleet_cache)
                elif key == 'KEY_DOWN':
                    active_device_index = (active_device_index + 1) % len(fleet_cache)
                elif key in ('\n', '\r', 'KEY_ENTER'):
                    if ser:
                        ser.close()
                        ser = None
                    active_device_info = fleet_cache[active_device_index]
                    active_device = active_device_info["port"]
                    ser = open_serial_connection(active_device)
        except KeyboardInterrupt:
            break
        except curses.error:
            pass

        # Double-clear to remove any stray logger output from serial_io on Linux
        stdscr.erase()
        stdscr.refresh()
        stdscr.erase()
        stdscr.refresh()
