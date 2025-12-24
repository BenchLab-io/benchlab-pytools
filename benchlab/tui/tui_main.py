"""
Curses-based TUI for BENCHLAB telemetry
"""

import curses
import io
import logging
import sys
import time

# Benchlab imports
from benchlab.tui.__init__ import __version__
from benchlab.core import serial_io
from benchlab.core.sensor_translation import translate_sensor_struct

# Silence benchlab.core.serial_io
class NullLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

serial_io_logger_name = "benchlab.core.serial_io"
logger = logging.getLogger(serial_io_logger_name)
logger.__class__ = NullLogger
logger.handlers.clear()
logger.propagate = False


# Setup fleet and device
fleet_cache = []
active_device = None
active_device_info = None
active_device_index = 0
last_active_device = None
ser = None
connected = False

def tui_main(stdscr, _unused, args):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_YELLOW)  # Active tab
    curses.init_pair(2, curses.COLOR_GREEN, -1)                   # Status OK
    curses.init_pair(3, curses.COLOR_RED, -1)                     # Warnings/errors
    curses.init_pair(4, curses.COLOR_MAGENTA, -1)                 # Temperature
    curses.init_pair(6, curses.COLOR_WHITE, -1)                   # Inactive tab
    curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Title bar

    tui_refresh_interval = args.interval
    stdscr.nodelay(True)
    stdscr.timeout(500)

    TAB_NAMES = ["Fleet", "Device", "Power", "Voltage", "Temperature", "Fans"]
    current_tab = 0

    global fleet_cache, active_device, active_device_info, active_device_index, last_active_device, ser, connected

    # Initialize fleet cache
    detected_fleet = serial_io.get_fleet_info()
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
                ser = serial_io.open_serial_connection(active_device)
            last_active_device = active_device
            connected = False

        sensor_data = None
        device_info, sensor_struct, uid = None, None, None

        if ser:
            try:
                device_info = serial_io.read_device(ser)
                sensor_struct = serial_io.read_sensors(ser)
                uid = serial_io.read_uid(ser)
                connected = True
                sensor_data = translate_sensor_struct(sensor_struct)
            except Exception:
                connected = False
                try: ser.close() 
                except: pass
                ser = None
        else:
            connected = False

        # --- Fleet tab ---
        if current_tab == 0:
            stdscr.addstr(4, 2, "## BENCHLAB Fleet ##")
            if not fleet_cache:
                stdscr.addstr(6, 4, "No connected devices found.", curses.color_pair(3))
            else:
                stdscr.addstr(6, 2, f"{'':<4} {'Port':<12} {'Firmware':<10} {'UID':<15}", curses.A_UNDERLINE)
                for i, dev in enumerate(fleet_cache):
                    prefix = "->" if i == active_device_index else "  "
                    active_mark = " [ACTIVE]" if active_device and dev['port'] == active_device else ""
                    stdscr.addstr(8 + i, 4, f"{prefix} {dev['port']:<12} 0x{dev['firmware']:<8} {dev['uid']:<15}{active_mark}")

            bottom_text = f"Active device: {active_device_info['port'] if active_device_info else 'None'}"
            stdscr.addstr(height - 2, 2, bottom_text[:width-4])

        # --- Device tab ---
        elif current_tab == 1:
            stdscr.addstr(4, 2, "## BENCHLAB Connection ##")
            if connected and device_info:
                stdscr.addstr(6, 4, f"{'Port':<20} {ser.port}")
                stdscr.addstr(9, 2, "## BENCHLAB Device ##")
                stdscr.addstr(11, 4, f"{'Vendor ID':<20} 0x{device_info['VendorId']:03X}")
                stdscr.addstr(12, 4, f"{'Product ID':<20} 0x{device_info['ProductId']:03X}")
                stdscr.addstr(13, 4, f"{'Device UID':<20} {uid}")
                stdscr.addstr(14, 4, f"{'Firmware Version':<20} 0x{device_info['FwVersion']:02X}")

                stdscr.addstr(17, 2, "## BENCHLAB Configuration ##")
                stdscr.addstr(19, 4, f"{'Fan Switch':<20} {sensor_struct.FanSwitchStatus}")
                stdscr.addstr(20, 4, f"{'RGB Switch':<20} {sensor_struct.RGBSwitchStatus}")
                stdscr.addstr(21, 4, f"{'RGB Ext':<20} {sensor_struct.RGBExtStatus}")

                stdscr.addstr(24, 2, "## TUI Configuration ##")
                stdscr.addstr(26, 4, f"{'TUI Refresh':<20} {tui_refresh_interval} sec")
            else:
                stdscr.addstr(6, 4, "Device disconnected! Waiting to reconnect...", curses.color_pair(3))

        # --- Power tab ---
        elif current_tab == 2:
            if connected and sensor_data:
                stdscr.addstr(4, 2, "## System Telemetry ##")
                stdscr.addstr(6, 2, f"{'SYS Power':<12} {sensor_data['SYS_Power']:.0f} W")
                stdscr.addstr(7, 2, f"{'CPU Power':<12} {sensor_data['CPU_Power']:.0f} W")
                stdscr.addstr(8, 2, f"{'GPU Power':<12} {sensor_data['GPU_Power']:.0f} W")
                stdscr.addstr(9, 2, f"{'MB Power':<12} {sensor_data['MB_Power']:.0f} W")
            else:
                stdscr.addstr(4, 2, "Power telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Voltage tab ---
        elif current_tab == 3:
            if connected and sensor_data:
                stdscr.addstr(4, 2, f"{'Vdd':<10} {sensor_data['Vdd']} V")
                stdscr.addstr(5, 2, f"{'Vref':<10} {sensor_data['Vref']} V")
                y = 7
                for name in [f"VIN_{i}" for i in range(len(sensor_struct.Vin))]:
                    stdscr.addstr(y, 2, f"{name:<10} {sensor_data[name]:.3f} V")
                    y += 1
            else:
                stdscr.addstr(4, 2, "Voltage telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Temperature tab ---
        elif current_tab == 4:
            if connected and sensor_data:
                stdscr.addstr(4, 2, f"{'Chip Temp':<15} {sensor_data['Chip_Temp']}")
                stdscr.addstr(6, 2, f"{'Ambient Temp':<15} {sensor_data['Ambient_Temp']}")
                stdscr.addstr(7, 2, f"{'Humidity':<15} {sensor_data['Humidity']:.1f} %")
                for i in range(4):
                    stdscr.addstr(9+i, 2, f"{'Temp Sensor ' + str(i+1):<15} {sensor_data[f'Temp_Sensor_{i+1}']}")
            else:
                stdscr.addstr(4, 2, "Temperature telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Fans tab ---
        elif current_tab == 5:
            if connected and sensor_data:
                stdscr.addstr(4, 2, f"{'':<12} {'Duty (%)':<10} {'RPM':<10} {'Status':<10}")
                for i, f in enumerate(sensor_struct.Fans):
                    stdscr.addstr(5+i, 2, f"{'Fan'+str(i+1):<12} {f.Duty:<10} {f.Tach:<10} {f.Enable}")
                stdscr.addstr(5+len(sensor_struct.Fans)+1, 2, f"{'FanExtDuty':<12} {sensor_data['FanExtDuty']}")
            else:
                stdscr.addstr(4, 2, "Fan telemetry unavailable - device disconnected!", curses.color_pair(3))

        # --- Key handling ---
        try:
            key = stdscr.getkey()
            if key in ['KEY_RIGHT', 'l']:
                current_tab = (current_tab + 1) % len(TAB_NAMES)
            elif key in ['KEY_LEFT', 'h']:
                current_tab = (current_tab - 1) % len(TAB_NAMES)
            elif key in ['q', 'Q']:
                break
            elif key == "KEY_RESIZE":
                continue
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
                    ser = serial_io.open_serial_connection(active_device)
        except KeyboardInterrupt:
            break
        except curses.error:
            pass

        stdscr.refresh()
        time.sleep(0.1)
