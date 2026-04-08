"""
Enhanced Curses-based TUI for BENCHLAB telemetry
Refactored: background serial thread, class-based state, extracted tab renderers
"""

import curses
import logging
import sys
import time
import threading
from collections import deque, defaultdict
from datetime import datetime

# Benchlab imports
from benchlab.tui.__init__ import __version__
from benchlab_pycore.core import read_sensors, translate_sensor_struct
from benchlab_pycore.core.serial_io import get_benchlab_ports, open_serial_connection
from benchlab_pycore.core import read_device, read_uid


def get_fleet_info(datasource=None):
    """Scan for Benchlab devices.
    
    If datasource is provided and connected, use it instead of local serial scan.
    This avoids conflicts when the serial port is held by a FastAPI/MQTT server.
    """
    # Prefer datasource if available
    if datasource is not None and datasource.is_connected():
        devices = datasource.list_devices()
        fleet = []
        for dev in devices:
            fw = dev.get("FwVersion", "?")
            # Firmware may be a string from remote datasources (FastAPI/MQTT)
            if isinstance(fw, str):
                try:
                    fw = int(fw, 16)
                except (ValueError, TypeError):
                    fw = 0
            fleet.append({
                "port": dev.get("port", "?"),
                "firmware": fw,
                "uid": dev.get("uid", "?"),
            })
        return fleet

    # Fall back to local serial scan (for direct mode)
    fleet = []
    ports = get_benchlab_ports()
    for port_info in ports:
        portname = port_info.get("port", "Unknown")
        try:
            ser = open_serial_connection(portname)
            if ser is None:
                # Port exists but is held by another process
                fleet.append({
                    "port": portname,
                    "firmware": "?",
                    "uid": "BUSY",
                })
                continue
            device_info = read_device(ser)
            uid = read_uid(ser)
            fleet.append({
                "port": portname,
                "firmware": device_info.get("FwVersion") if device_info else "?",
                "uid": uid,
            })
            ser.close()
        except Exception as e:
            err_str = str(e).lower()
            if "permission" in err_str or "access" in err_str or err_str == "":
                fleet.append({
                    "port": portname,
                    "firmware": "?",
                    "uid": "BUSY",
                })
    return fleet


from benchlab.core import create_datasource

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Data source configuration
# ─────────────────────────────────────────────────────────────────────────────

def get_default_datasource(args):
    """Get default data source based on command-line arguments."""
    if hasattr(args, 'source'):
        return args.source
    return 'direct'  # Default for backward compatibility

# ─────────────────────────────────────────────────────────────────────────────
#  Data source worker
# ─────────────────────────────────────────────────────────────────────────────

class DataSourceWorker(threading.Thread):
    """
    Background thread that polls a DataSource for telemetry data
    and exposes a snapshot compatible with SerialWorker.
    """

    def __init__(self, datasource, uid: str, interval: float, stats=None):
        super().__init__(daemon=True)
        self.datasource = datasource
        self.uid = uid
        self.interval = interval
        self.stats = stats
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.connected = False
        self.sensor_data = None
        self.device_info = None
        self.sensor_struct = None
        self.last_error = None
        self.connection_time = None

    def run(self):
        self.connection_time = datetime.now()
        while not self._stop_event.is_set():
            try:
                if self.datasource.is_connected():
                    data = self.datasource.get_telemetry(self.uid)
                    info = self.datasource.get_device_info(self.uid)
                    with self._lock:
                        self.connected = True
                        self.sensor_data = data
                        self.device_info = info
                        self.sensor_struct = None  # Not available via MQTT/FastAPI
                        self.last_error = None

                    # Update stats if available
                    if self.stats and data:
                        for key, val in data.items():
                            if isinstance(val, (int, float)):
                                self.stats.update(self.uid, key, val)
                else:
                    with self._lock:
                        self.connected = False
                        self.last_error = "Data source not connected"
            except Exception as e:
                with self._lock:
                    self.connected = False
                    self.last_error = str(e)

            self._stop_event.wait(self.interval)

    def snapshot(self):
        with self._lock:
            return {
                'connected': self.connected,
                'port': getattr(self.datasource, 'broker', getattr(self.datasource, 'base_url', 'unknown')),
                'sensor_data': self.sensor_data,
                'device_info': self.device_info,
                'sensor_struct': self.sensor_struct,
                'uid': self.uid,
                'connection_time': self.connection_time,
                'last_error': self.last_error,
            }

    def stop(self):
        self._stop_event.set()


class ConnectError(Exception):
    """Raised when a connection attempt fails."""
    pass


class DataSourceWorkerWrapper:
    """
    Unified wrapper for both direct serial (SerialWorker) and
    network datasources (DataSourceWorker).  Provides a common
    ``snapshot()`` API that the TUI render loop relies on.
    """

    # Human-readable descriptions for every data-source type
    _SOURCE_META = {
        'direct':  'Serial',
        'fastapi': 'FastAPI',
        'mqtt':    'MQTT',
    }

    def __init__(self, args, stats=None):
        self.args         = args
        self.stats        = stats         # SerialWorker | DataSourceWorker
        self._worker      = None          # SerialWorker | DataSourceWorker
        self._datasource  = None          # DataSource instance (MQTT / FastAPI)
        self._source_type = 'direct'      # current source type string
        self._port        = None          # device port string for display
        self._uid         = None

    # ----- public helpers --------------------------------------------------

    @property
    def source_type(self):
        return self._source_type

    def _build_source_desc(self) -> str:
        """Build a human description like 'COM4, via 1883' or 'COM4'."""
        src = self._source_type
        if src == 'direct':
            return self._port or 'direct'
        if src == 'fastapi':
            port = getattr(self.args, 'api_port', 8000)
            return f"{self._port or '?'}, via {port}"
        if src == 'mqtt':
            port = getattr(self.args, 'mqtt_port', 1883)
            return f"{self._port or '?'}, via {port}"
        return self._port or 'unknown'

    # ----- lifecycle --------------------------------------------------------

    def connect_direct(self, port: str):
        """Open a direct serial connection to *port*."""
        self.stop()
        self._source_type = 'direct'
        self._port = port
        self._uid = None
        self._worker = SerialWorker(port, self.args.interval, self.stats)
        self._worker.start()

    def connect_datasource(self):
        """Connect via FastAPI or MQTT (source type comes from ``args.source``)."""
        source_type = get_default_datasource(self.args)
        self.stop()
        self._source_type = source_type

        if source_type == 'direct':
            # Fall back to serial if no port specified in fleet flow
            port = getattr(self.args, 'port', None)
            if port:
                self.connect_direct(port)
                return True
            raise ConnectError("No serial port specified for direct mode.")

        try:
            self._datasource = create_datasource(source_type)
            ok = self._datasource.connect()
            if not ok:
                raise ConnectError(f"Failed to connect to {source_type.upper()} broker.")

            # Give the datasource a moment to pick up device info
            devices = self._datasource.list_devices()
            if not devices:
                raise ConnectError(f"No devices available via {source_type.upper()}.")

            self._uid = devices[0]['uid']
            self._port = devices[0].get('port', '?')
            self._worker = DataSourceWorker(
                self._datasource,
                self._uid,
                self.args.interval,
                self.stats,
            )
            self._worker.start()
            return True
        except ConnectError:
            raise
        except Exception as exc:
            raise ConnectError(f"{source_type} connection failed: {exc}")

    def snapshot(self) -> dict:
        ws = self._worker.snapshot() if self._worker else {
            'connected': False, 'sensor_data': None,
            'device_info': None, 'sensor_struct': None,
            'uid': None, 'connection_time': None, 'last_error': 'Not connected',
        }
        ws['source_type']     = self._SOURCE_META.get(self._source_type, self._source_type)
        ws['source_desc']     = self._build_source_desc()
        ws['port']            = self._port
        return ws

    def stop(self):
        if self._worker:
            self._worker.stop()
            self._worker.join(timeout=2)
            self._worker = None
        if self._datasource:
            try:
                self._datasource.disconnect()
            except Exception:
                pass
            self._datasource = None
        self._uid = None

#  Bar scale configuration  ← edit these to change bar ranges
# ─────────────────────────────────────────────────────────────────────────────

BAR_SCALES = {
    # Power (Watts)
    'power_max':          1000.0,  # fixed bar ceiling (W); set to None to auto-scale
    'power_auto_floor':   10.0,    # minimum ceiling used when auto-scaling

    # Current (Amps)
    'current_max':        100.0,   # fixed bar ceiling (A); set to None to auto-scale
    'current_auto_floor': 0.0,     # minimum ceiling used when auto-scaling

    # Temperature (°C)
    'chip_temp_max':      100.0,
    'ambient_temp_max':   70.0,
    'sensor_temp_max':    100.0,
    'chip_temp_warn':     70.0,    # above this → bar turns red
    'sensor_temp_warn':   60.0,    # above this → bar turns red

    # Humidity (%)
    'humidity_max':       100.0,

    # Fans
    'fan_rpm_max':        3000,    # RPM at which the bar is full
}

# Voltage OK bands (volts) — [lo, hi] inclusive
VOLTAGE_BANDS = {
    'rail':   (10.0, 14.0),   # EPS / PCIE / HPWR rails
    'Vdd':    (3.0,  3.6),
    'Vref':   (1.6,  2.0),
    'VIN':    (0.5,  15.0),   # generic VIN channels
}

# ─────────────────────────────────────────────────────────────────────────────
#  Channel stats helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_stat():
    return {'min': float('inf'), 'max': float('-inf'), 'sum': 0.0, 'count': 0}


class ChannelStats:
    """Thread-safe per-device, per-channel running statistics."""

    def __init__(self):
        self._lock = threading.Lock()
        # device -> channel -> stat dict
        self._data: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(_make_stat))

    def update(self, device: str, channel: str, value: float):
        if value is None:
            return
        with self._lock:
            s = self._data[device][channel]
            if value < s['min']:
                s['min'] = value
            if value > s['max']:
                s['max'] = value
            s['sum'] += value
            s['count'] += 1

    def get(self, device: str, channel: str):
        """Return (min, max, avg) or (None, None, None)."""
        with self._lock:
            s = self._data[device][channel]
            if s['count'] == 0:
                return None, None, None
            return s['min'], s['max'], s['sum'] / s['count']

    def reset(self, device: str):
        with self._lock:
            self._data[device].clear()


# ─────────────────────────────────────────────────────────────────────────────
#  Serial I/O background thread
# ─────────────────────────────────────────────────────────────────────────────

class SerialWorker(threading.Thread):
    """
    Reads sensor data in the background at `interval` seconds.
    Stores the latest snapshot; the render loop reads it without blocking.
    """

    def __init__(self, port: str, interval: float, stats: ChannelStats):
        super().__init__(daemon=True)
        self.port = port
        self.interval = interval
        self.stats = stats

        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Shared state written by this thread, read by render
        self.connected = False
        self.sensor_data: dict | None = None
        self.device_info = None
        self.sensor_struct = None
        self.uid = None
        self.connection_time: datetime | None = None
        self.last_error: str | None = None

    # ── public API ────────────────────────────────────────────────────────────

    def snapshot(self):
        """Return a consistent copy of the latest telemetry."""
        with self._lock:
            return {
                'connected':     self.connected,
                'port':          self.port,
                'sensor_data':   self.sensor_data,
                'device_info':   self.device_info,
                'sensor_struct': self.sensor_struct,
                'uid':           self.uid,
                'connection_time': self.connection_time,
                'last_error':    self.last_error,
            }

    def stop(self):
        self._stop_event.set()

    # ── thread body ───────────────────────────────────────────────────────────

    def run(self):
        ser = None
        RECONNECT_DELAY = 2.0

        while not self._stop_event.is_set():
            # --- (Re)connect ---
            if ser is None:
                try:
                    ser = open_serial_connection(self.port)
                    with self._lock:
                        self.connection_time = datetime.now()
                        self.last_error = None
                except Exception as exc:
                    with self._lock:
                        self.connected = False
                        self.last_error = str(exc)
                    self._stop_event.wait(RECONNECT_DELAY)
                    continue

            # --- Read ---
            try:
                device_info  = read_device(ser)
                sensor_struct = read_sensors(ser)
                uid          = read_uid(ser)
                sensor_data  = translate_sensor_struct(sensor_struct)

                # update stats
                for key, val in sensor_data.items():
                    if isinstance(val, (int, float)):
                        self.stats.update(self.port, key, val)

                with self._lock:
                    self.connected    = True
                    self.sensor_data  = sensor_data
                    self.device_info  = device_info
                    self.sensor_struct = sensor_struct
                    self.uid          = uid
                    self.last_error   = None

            except Exception as exc:
                log.warning("Serial read error on %s: %s", self.port, exc)
                with self._lock:
                    self.connected   = False
                    self.last_error  = str(exc)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                self._stop_event.wait(RECONNECT_DELAY)
                continue

            self._stop_event.wait(self.interval)

        # cleanup
        if ser:
            try:
                ser.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
#  Drawing primitives
# ─────────────────────────────────────────────────────────────────────────────

STAT_W = 10   # fixed width for each stat column

def _stat_str(mn, mx, avg, decimals, unit):
    """Return fixed-width stat columns: ↓min  ↑max  ~avg"""
    if mn is None:
        return ""
    w = STAT_W
    lo  = f"↓{mn:.{decimals}f}{unit}"
    hi  = f"↑{mx:.{decimals}f}{unit}"
    av  = f"~{avg:.{decimals}f}{unit}"
    return f"  {lo:<{w}} {hi:<{w}} {av:<{w}}"


def draw_bar(stdscr, y, x, label, value, unit, max_val, color,
             bar_width=20, decimals=1, stat=None):
    """
    One row:  label(14)  value(7) unit(3)  [bar]  stats
    `stat` is (min, max, avg) or None.
    """
    filled = 0
    if max_val > 0 and 0 <= value <= max_val:
        filled = max(0, min(bar_width, int((value / max_val) * bar_width)))

    val_str = f"{value:>7.{decimals}f}"
    bar     = "█" * filled + "░" * (bar_width - filled)

    try:
        stdscr.addstr(y, x, f"{label:<14} {val_str} {unit:<3} ", color)
        stdscr.addstr(bar, color)
        if stat and stat[0] is not None:
            stdscr.addstr(
                _stat_str(stat[0], stat[1], stat[2], decimals, unit),
                curses.color_pair(7)
            )
    except curses.error:
        pass


def draw_voltage_row(stdscr, y, x, name, val, stat,
                     ok_lo=0.5, ok_hi=15.0, decimals=3):
    """name(14)  val V  [STATUS]  stats"""
    if val is None:
        val = 0.0
    if val == 0:
        status, color = 'N/A', curses.color_pair(7)
    elif val < ok_lo:
        status, color = 'LOW ', curses.color_pair(3)
    elif val > ok_hi:
        status, color = 'HIGH', curses.color_pair(4)
    else:
        status, color = 'OK  ', curses.color_pair(2)

    val_str = f"{val:>8.{decimals}f} V" if val != 0 else "     N/A  "
    try:
        stdscr.addstr(y, x, f"{name:<14}", color)
        stdscr.addstr(f"{val_str}  ", color)
        stdscr.addstr(f"[{status}]", color | curses.A_BOLD)
        if stat and stat[0] is not None:
            stdscr.addstr(
                _stat_str(stat[0], stat[1], stat[2], decimals, 'V'),
                curses.color_pair(7)
            )
    except curses.error:
        pass


def section(stdscr, y, x, title):
    """All ┌─ section headers are yellow (company colour), bold."""
    try:
        stdscr.addstr(y, x, f"┌─ {title} ", curses.color_pair(4) | curses.A_BOLD)
    except curses.error:
        pass


def disconnected(stdscr, tab_name):
    try:
        stdscr.addstr(6, 4,
                      f"{tab_name} telemetry unavailable — device disconnected.",
                      curses.color_pair(3))
    except curses.error:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Tab renderers
# ─────────────────────────────────────────────────────────────────────────────

def render_fleet(stdscr, height, width, state, fleet_cache,
                 active_device_index, active_device, connected, source_type='direct'):
    if source_type == 'direct':
        title = "BENCHLAB Fleet"
    else:
        title = f"BENCHLAB Fleet (via {source_type.upper()})"
    section(stdscr, 4, 2, title)

    if not fleet_cache:
        try:
            stdscr.addstr(6, 4, "No connected devices found.", curses.color_pair(3))
        except curses.error:
            pass
    else:
        # All column positions defined once — used by both header and data rows
        COL_SEL    = 2
        COL_PORT   = 5
        COL_FW     = 25
        COL_UID    = 38
        COL_STATUS = 66
        COL_ACTIVE = 80

        W_PORT = 18
        W_FW   = 11   # "0x" + up to 8 hex digits
        W_UID  = 26
        W_ST   = 12

        hdr_attr = curses.A_UNDERLINE | curses.color_pair(7)
        try:
            stdscr.addstr(6, COL_SEL,    f"{'':2}",           hdr_attr)
            stdscr.addstr(6, COL_PORT,   f"{'Port':<{W_PORT}}", hdr_attr)
            stdscr.addstr(6, COL_FW,     f"{'Firmware':<{W_FW}}", hdr_attr)
            stdscr.addstr(6, COL_UID,    f"{'UID':<{W_UID}}", hdr_attr)
            stdscr.addstr(6, COL_STATUS, f"{'Status':<{W_ST}}", hdr_attr)
            stdscr.addstr(6, COL_ACTIVE, "Active",             hdr_attr)
        except curses.error:
            pass

        for i, dev in enumerate(fleet_cache):
                port     = dev.get('port', 'Unknown')
                firmware = dev.get('firmware', 0)
                dev_uid  = dev.get('uid', 'Unknown')
                is_busy  = dev_uid == "BUSY"

                cursor = "->" if i == active_device_index else "  "

                is_active    = active_device is not None and port == active_device
                is_connected = is_active and connected

                if is_busy:
                    status = "BUSY"
                    status_color = curses.color_pair(4)
                elif is_connected:
                    status = "CONNECTED"
                    status_color = curses.color_pair(2)
                else:
                    status = "DISCONNECTED"
                    status_color = curses.color_pair(3)

                row_color = curses.color_pair(1) | curses.A_BOLD if i == active_device_index else curses.color_pair(7)

                row = 8 + i
                try:
                    stdscr.addstr(row, COL_SEL,    cursor,                        row_color)
                    stdscr.addstr(row, COL_PORT,   f"{port:<{W_PORT}}",           row_color)
                    if is_busy:
                        stdscr.addstr(row, COL_FW,     f"{'N/A':<{W_FW}}",          row_color)
                        stdscr.addstr(row, COL_UID,    f"{dev_uid:<{W_UID}}",       curses.color_pair(4) | curses.A_BOLD)
                    else:
                        try:
                            fw_int = int(firmware) if firmware is not None else 0
                            fw_str = f"0x{fw_int:08X}"
                        except (TypeError, ValueError):
                            fw_str = "0x????????"
                        stdscr.addstr(row, COL_FW,     f"{fw_str:<{W_FW}}",   row_color)
                        stdscr.addstr(row, COL_UID,    f"{dev_uid:<{W_UID}}",         row_color)
                    stdscr.addstr(row, COL_STATUS, f"{status:<{W_ST}}",           status_color)
                    if is_active:
                        stdscr.addstr(row, COL_ACTIVE, "*", curses.color_pair(2) | curses.A_BOLD)
                except curses.error:
                    pass

    # footer
    status_text  = "CONNECTED" if connected else "DISCONNECTED"
    status_color = curses.color_pair(2) if connected else curses.color_pair(3)
    try:
        stdscr.addstr(height - 3, 2, f"Status: {status_text}",
                      status_color | curses.A_BOLD)
        ct = state.get('connection_time')
        if connected and ct:
            uptime = str(datetime.now() - ct).split('.')[0]
            stdscr.addstr(height - 2, 2, f"Uptime: {uptime}", curses.color_pair(4))
        else:
            stdscr.addstr(height - 2, 2, "Uptime: —", curses.color_pair(3))
    except curses.error:
        pass


def render_device(stdscr, state, tui_refresh_interval):
    sd   = state.get('sensor_data') or {}
    si   = state.get('sensor_struct')
    di   = state.get('device_info') or {}
    uid  = state.get('uid')

    # Source type and description — always show, even if not yet connected
    src_type   = state.get('source_type', 'Unknown')   # "Serial", "FastAPI", "MQTT"
    src_desc   = state.get('source_desc', '')           # "COM4" or "COM4, via 8000"
    port_str   = state.get('port', 'Unknown')

    try:
        section(stdscr, 4, 2, "Connection")
        stdscr.addstr(6, 4, f"{'Data Source':<22} {src_type}")
        stdscr.addstr(7, 4, f"{'Connection':<22} {src_desc}")
        stdscr.addstr(8, 4, f"{'Device Port':<22} {port_str}")

        if not state['connected']:
            # Show a brief disconnected message
            stdscr.addstr(10, 4, "Not connected to device.", curses.color_pair(3))
            return

        # sensor_struct may be None for MQTT/FastAPI — handle gracefully
        def _field(attr, default=0):
            v = getattr(si, attr, None)
            if v is None:
                v = di.get(attr) if isinstance(di, dict) else getattr(di, attr, None)
            return v if v is not None else default

        vendor_id  = _field('VendorId')
        product_id = _field('ProductId')
        fw_version = _field('FwVersion')

        section(stdscr, 11, 2, "Device")
        stdscr.addstr(13, 4, f"{'Vendor ID':<22} 0x{vendor_id:02X}")
        stdscr.addstr(14, 4, f"{'Product ID':<22} 0x{product_id:02X}")
        stdscr.addstr(15, 4, f"{'Device UID':<22} {uid or 'N/A'}")
        stdscr.addstr(16, 4, f"{'Firmware Version':<22} 0x{fw_version:02X}")

        if si:
            # Configuration and TUI rows only when sensor_struct is available (direct mode)
            section(stdscr, 18, 2, "Configuration")
            fan_sw = getattr(si, 'FanSwitchStatus', 'Unknown')
            rgb_sw = getattr(si, 'RGBSwitchStatus', 'Unknown')
            rgb_ex = getattr(si, 'RGBExtStatus',    'Unknown')
            stdscr.addstr(20, 4, f"{'Fan Switch':<22} {fan_sw}")
            stdscr.addstr(21, 4, f"{'RGB Switch':<22} {rgb_sw}")
            stdscr.addstr(22, 4, f"{'RGB Ext':<22} {rgb_ex}")

            section(stdscr, 24, 2, "TUI")
            stdscr.addstr(26, 4, f"{'Refresh Interval':<22} {tui_refresh_interval} s")
    except curses.error:
        pass


def render_system(stdscr, state, stats, active_device):
    """Tab 2 — System: Summary + Power + Current + Voltage for the rail channels."""
    if not state['connected'] or not state['sensor_data']:
        disconnected(stdscr, "System")
        return

    sd = state['sensor_data']

    # color_pair(4)=yellow → watts/power   (bars + section headers)
    # color_pair(5)=cyan   → amps/current  (bars)
    # color_pair(7)=white  → volts/voltage (bars — light and readable)
    COLOR_PWR = curses.color_pair(4)
    COLOR_CUR = curses.color_pair(5)
    COLOR_VLT = curses.color_pair(7)

    # Shared rail channel list: (sensor_key_prefix, display_label)
    RAIL_CHANNELS = [
        ('EPS1',    'EPS_1'),
        ('EPS2',    'EPS_2'),
        ('PCIE8_1', 'PCIE8_1'),
        ('PCIE8_2', 'PCIE8_2'),
        ('PCIE8_3', 'PCIE8_3'),
        ('HPWR1',   '12V_HPWR_1'),
        ('HPWR2',   '12V_HPWR_2'),
    ]

    # ── Summary ───────────────────────────────────────────────────────────────
    row = 4
    section(stdscr, row, 2, "Summary")
    row += 1
    summary_channels = [
        ('SYS_Power', 'SYS Power'),
        ('CPU_Power', 'CPU Power'),
        ('GPU_Power', 'GPU Power'),
        ('MB_Power',  'MB Power'),
    ]
    sum_vals = [sd.get(k) or 0.0 for k, _ in summary_channels]
    max_sum = BAR_SCALES['power_max'] or max(BAR_SCALES['power_auto_floor'], max(sum_vals) * 1.2)
    for (key, label), val in zip(summary_channels, sum_vals):
        draw_bar(stdscr, row, 4, label, val, 'W', max_sum,
                 COLOR_PWR, bar_width=20, decimals=0,
                 stat=stats.get(active_device, key))
        row += 1

    # ── Power Telemetry ───────────────────────────────────────────────────────
    row += 1
    section(stdscr, row, 2, "Power Telemetry")
    row += 1
    pwr_vals = [sd.get(f'{k}_Power') or 0.0 for k, _ in RAIL_CHANNELS]
    max_pwr = BAR_SCALES['power_max'] or max(BAR_SCALES['power_auto_floor'], max(pwr_vals) * 1.2)
    for (key_pfx, label), val in zip(RAIL_CHANNELS, pwr_vals):
        draw_bar(stdscr, row, 4, label, val, 'W', max_pwr,
                 COLOR_PWR, bar_width=20, decimals=0,
                 stat=stats.get(active_device, f'{key_pfx}_Power'))
        row += 1

    # ── Current Telemetry ─────────────────────────────────────────────────────
    row += 1
    section(stdscr, row, 2, "Current Telemetry")
    row += 1
    cur_vals = [sd.get(f'{k}_Current') or 0.0 for k, _ in RAIL_CHANNELS]
    max_cur = BAR_SCALES['current_max'] or max(BAR_SCALES['current_auto_floor'], max(cur_vals) * 1.2)
    for (key_pfx, label), val in zip(RAIL_CHANNELS, cur_vals):
        draw_bar(stdscr, row, 4, label, val, 'A', max_cur,
                 COLOR_CUR, bar_width=20, decimals=2,
                 stat=stats.get(active_device, f'{key_pfx}_Current'))
        row += 1

    # ── Voltage Telemetry ─────────────────────────────────────────────────────
    row += 1
    section(stdscr, row, 2, "Voltage Telemetry")
    row += 1
    vlt_vals = [sd.get(f'{k}_Voltage') or 0.0 for k, _ in RAIL_CHANNELS]
    # Rail voltages are nominally 12 V; use a fixed 15 V scale so bars are meaningful
    max_vlt = 15.0
    for (key_pfx, label), val in zip(RAIL_CHANNELS, vlt_vals):
        draw_bar(stdscr, row, 4, label, val, 'V', max_vlt,
                 COLOR_VLT, bar_width=20, decimals=2,
                 stat=stats.get(active_device, f'{key_pfx}_Voltage'))
        row += 1


def render_voltage(stdscr, state, stats, active_device):
    """Tab 3 — Voltage: Board rails (Vdd/Vref) + VIN_0…VIN_12."""
    if not state['connected'] or not state['sensor_data']:
        disconnected(stdscr, "Voltage")
        return

    COLOR_VLT = curses.color_pair(7)   # white — light, readable on dark terminal

    sd = state['sensor_data']
    row = 4

    # ── Board ─────────────────────────────────────────────────────────────────
    section(stdscr, row, 2, "Board")
    row += 1
    for key, label, max_v in [
        ('Vdd',  'Vdd',  5.0),
        ('Vref', 'Vref', 5.0),
    ]:
        val = sd.get(key) or 0.0
        st  = stats.get(active_device, key)
        draw_bar(stdscr, row, 4, label, val, 'V', max_v,
                 COLOR_VLT, bar_width=20, decimals=3, stat=st)
        row += 1

    # ── Voltage Measurements ──────────────────────────────────────────────────
    row += 1
    section(stdscr, row, 2, "Voltage Measurements")
    row += 1
    max_vin  = VOLTAGE_BANDS['VIN'][1]   # hi end of band as bar ceiling
    for idx in range(13):   # VIN_0 … VIN_12
        key = f"VIN_{idx}"
        val = sd.get(key) or 0.0
        st  = stats.get(active_device, key)
        draw_bar(stdscr, row, 4, key, val, 'V', max_vin,
                 COLOR_VLT, bar_width=20, decimals=3, stat=st)
        row += 1


def render_temperature(stdscr, state, stats, active_device):
    """Tab 4 — Temperature: Board chip temp + System ambient/humidity + Sensors 1-4."""
    if not state['connected'] or not state['sensor_data']:
        disconnected(stdscr, "Temperature")
        return

    # color_pair(5)=cyan  → base temperature color
    # color_pair(2)=green → normal (below warn threshold)
    # color_pair(3)=red   → hot (above warn threshold)
    COLOR_TEMP = curses.color_pair(5)

    sd = state['sensor_data']
    row = 4

    # ── Board ─────────────────────────────────────────────────────────────────
    section(stdscr, row, 2, "Board")
    row += 1
    chip_temp  = sd.get('Chip_Temp') or 0.0
    chip_color = curses.color_pair(2) if chip_temp < BAR_SCALES['chip_temp_warn'] else curses.color_pair(3)
    draw_bar(stdscr, row, 4, 'Chip Temp', chip_temp, '°C', BAR_SCALES['chip_temp_max'],
             chip_color, decimals=1, stat=stats.get(active_device, 'Chip_Temp'))
    row += 2

    # ── System ────────────────────────────────────────────────────────────────
    section(stdscr, row, 2, "System")
    row += 1
    ambient_temp = sd.get('Ambient_Temp') or 0.0
    humidity     = sd.get('Humidity')     or 0.0
    draw_bar(stdscr, row, 4, 'Ambient Temp', ambient_temp, '°C', BAR_SCALES['ambient_temp_max'],
             COLOR_TEMP, decimals=1, stat=stats.get(active_device, 'Ambient_Temp'))
    row += 1
    draw_bar(stdscr, row, 4, 'Humidity', humidity, '%', BAR_SCALES['humidity_max'],
             COLOR_TEMP, decimals=1, stat=stats.get(active_device, 'Humidity'))
    row += 2

    # ── Sensors ───────────────────────────────────────────────────────────────
    section(stdscr, row, 2, "Sensors")
    row += 1
    for i in range(4):
        key = f'Temp_Sensor_{i+1}'
        val = sd.get(key) or 0.0
        s_color = (curses.color_pair(2) if 0 < val < BAR_SCALES['sensor_temp_warn']
                   else curses.color_pair(3) if val >= BAR_SCALES['sensor_temp_warn']
                   else COLOR_TEMP)
        draw_bar(stdscr, row, 4, f'Sensor {i+1}', val, '°C', BAR_SCALES['sensor_temp_max'],
                 s_color, decimals=1, stat=stats.get(active_device, key))
        row += 1


def render_fans(stdscr, state, stats, active_device):
    section(stdscr, 4, 2, "Fan Control & Monitoring")
    if not state['connected'] or not state['sensor_data']:
        disconnected(stdscr, "Fan")
        return

    sd = state['sensor_data']

    # Fixed column positions — every addstr uses (row, col) explicitly
    # so Unicode arrows in stats never throw off cursor tracking
    COL_NAME  = 2
    COL_DUTY  = 10   # "  42%"
    COL_RPM   = 17   # "  1200"
    COL_EN    = 25   # "Yes"
    COL_BAR   = 30   # 20-char block bar
    COL_STATS = 52   # stats text starts here

    rpm_max_str = str(BAR_SCALES['fan_rpm_max'])
    hdr = (f"{'Fan':<8} {'Duty':>5}%  {'RPM':>6}  {'On':<3}  "
           f"{'Bar ('+rpm_max_str+' RPM)':<20}  Stats")
    try:
        stdscr.addstr(6, COL_NAME, hdr, curses.A_UNDERLINE | curses.color_pair(7))
    except curses.error:
        pass

    # Fans via sensor_data dict keys: Fan{N}_Duty, Fan{N}_RPM — infer count
    # FastAPI/MQTT: only dict available. Direct mode may also use si.Fans for "On" status.
    num_fans = 0
    while sd.get(f'Fan{num_fans+1}_Duty') is not None:
        num_fans += 1

    for i in range(1, num_fans + 1):
        duty    = sd.get(f'Fan{i}_Duty') or 0
        rpm     = sd.get(f'Fan{i}_RPM') or 0
        enabled = True   # "On" status not available via dict — assume True

        rpm_key  = f'Fan{i}_RPM'
        duty_key = f'Fan{i}_Duty'
        stats.update(active_device, rpm_key,  rpm)
        stats.update(active_device, duty_key, duty)

        bar_len   = max(0, min(20, int((rpm / BAR_SCALES['fan_rpm_max']) * 20)))
        fan_color = curses.color_pair(2) if rpm > 0 else curses.color_pair(3)
        en_str    = "Yes" if enabled else "No"
        bar       = "█" * bar_len + "░" * (20 - bar_len)

        mn_r, mx_r, avg_r = stats.get(active_device, rpm_key)
        mn_d, mx_d, avg_d = stats.get(active_device, duty_key)

        row = 8 + i - 1
        try:
            stdscr.addstr(row, COL_NAME,  f"Fan{i:<5}", fan_color)
            stdscr.addstr(row, COL_DUTY,  f"{duty:>5}%", fan_color)
            stdscr.addstr(row, COL_RPM,   f"{rpm:>6}", fan_color)
            stdscr.addstr(row, COL_EN,    f"{en_str:<3}", fan_color)
            stdscr.addstr(row, COL_BAR,   bar, fan_color)
            if mn_r is not None:
                stat_str = (f"  {mn_r:.0f}-{mx_r:.0f} ~{avg_r:.0f} RPM"
                            f"  {mn_d:.0f}-{mx_d:.0f} ~{avg_d:.0f}%")
                stdscr.addstr(row, COL_STATS, stat_str, curses.color_pair(7))
        except curses.error:
            pass

    # External fan
    ext_duty = sd.get('FanExtDuty') or 0
    ext_bar  = max(0, min(20, int(ext_duty / 5)))
    ext_row  = 8 + num_fans + 1
    try:
        stdscr.addstr(ext_row, COL_NAME,  "Ext Fan ", curses.color_pair(8))
        stdscr.addstr(ext_row, COL_DUTY,  f"{ext_duty:>5}%", curses.color_pair(8))
        stdscr.addstr(ext_row, COL_RPM,   f"{'N/A':>6}", curses.color_pair(8))
        stdscr.addstr(ext_row, COL_EN,    f"{'N/A':<3}", curses.color_pair(8))
        stdscr.addstr(ext_row, COL_BAR,   "█" * ext_bar + "░" * (20 - ext_bar),
                      curses.color_pair(8))
    except curses.error:
        pass

    if num_fans > 0:
        active_count = sum(1 for i in range(1, num_fans+1) if (sd.get(f'Fan{i}_RPM') or 0) > 0)
        try:
            stdscr.addstr(ext_row + 2, COL_NAME,
                          f"Active: {active_count}/{num_fans} fans running",
                          curses.color_pair(2))
        except curses.error:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Help modal
# ─────────────────────────────────────────────────────────────────────────────

HELP_LINES = [
    "BENCHLAB TUI — Help",
    "─" * 44,
    "",
    "Navigation",
    "  ← / →  or  h / l    Switch tabs",
    "  0 – 5               Jump to tab directly",
    "  q / Q               Quit",
    "  ?                   This help",
    "",
    "Fleet tab (0)",
    "  ↑ / ↓               Highlight device",
    "  Enter               Connect to highlighted device",
    "  f                   Re-scan fleet",
    "",
    "System tab (2)",
    "  Summary: SYS/CPU/GPU/MB power",
    "  Power / Current / Voltage per rail",
    "",
    "Voltage tab (3)",
    "  Board: Vdd, Vref",
    "  Measurements: VIN_0 to VIN_12",
    "",
    "Temperature tab (4)",
    "  Board: chip temp",
    "  System: ambient temp & humidity",
    "  Sensors: Sensor_1 to Sensor_4",
    "",
    "Global",
    "  r / R               Reset min/max/avg stats",
    "",
    "Colour key",
    "  Green               OK / Connected / Normal",
    "  Red                 Error / Disconnected / High temp",
    "  Yellow              Caution / Power data",
    "  Cyan                Temperature / Fan data",
    "  Blue                Voltage / Info",
    "",
    "Press any key to close",
]

def show_help(stdscr, height, width):
    h = min(len(HELP_LINES) + 4, height - 2)
    w = min(60, width - 4)
    sy = (height - h) // 2
    sx = (width  - w) // 2
    win = curses.newwin(h, w, sy, sx)
    win.attron(curses.color_pair(1) | curses.A_BOLD)
    win.border()
    win.attroff(curses.color_pair(1) | curses.A_BOLD)
    for i, line in enumerate(HELP_LINES):
        if i < h - 2:
            try:
                win.addstr(i + 1, 2, line[:w - 4])
            except curses.error:
                pass
    win.refresh()
    win.nodelay(False)
    win.getch()


# ─────────────────────────────────────────────────────────────────────────────
#  Main TUI entry point
# ─────────────────────────────────────────────────────────────────────────────

TAB_NAMES = ["Fleet", "Device", "System", "Voltage", "Temperature", "Fans"]

def tui_main(stdscr, _unused, args):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    curses.init_pair(1, curses.COLOR_WHITE,  curses.COLOR_BLUE)   # active tab / header
    curses.init_pair(2, curses.COLOR_GREEN,  -1)                  # OK / connected
    curses.init_pair(3, curses.COLOR_RED,    -1)                  # error / warning
    curses.init_pair(4, curses.COLOR_YELLOW, -1)                  # caution / power
    curses.init_pair(5, curses.COLOR_CYAN,   -1)                  # temp / fans
    curses.init_pair(6, curses.COLOR_BLUE,   -1)                  # voltage / info
    curses.init_pair(7, curses.COLOR_WHITE,  -1)                  # default text
    curses.init_pair(8, curses.COLOR_BLACK,  curses.COLOR_CYAN)   # ext / highlight

    stdscr.nodelay(True)
    stdscr.timeout(100)   # 100 ms poll — UI stays responsive

    # ── State ─────────────────────────────────────────────────────────────────
    stats          = ChannelStats()
    fleet_cache    = []  # Populated after datasource connect
    current_tab    = 0
    fleet_index    = 0
    active_device  = None    # port string of connected device
    wrapper        = DataSourceWorkerWrapper(args, stats)
    source_type    = get_default_datasource(args)  # 'direct', 'fastapi', or 'mqtt'
    status_msg     = ""      # transient footer message
    status_msg_expires = 0.0

    def set_status(msg: str, secs: float = 2.0):
        nonlocal status_msg, status_msg_expires
        status_msg = msg
        status_msg_expires = time.monotonic() + secs

    def connect_to(port: str):
        nonlocal wrapper, active_device
        try:
            wrapper.connect_direct(port)
            active_device = port
            set_status(f"Connecting to {port}…")
        except Exception as exc:
            set_status(f"Connect failed: {exc}", 4.0)

    def connect_datasource_source():
        """Connect via the data source selected at launch (FastAPI or MQTT)."""
        nonlocal wrapper, active_device
        try:
            wrapper.connect_datasource()
            active_device = wrapper._uid or wrapper._port
            set_status(f"Connected via {wrapper.source_type}")
        except ConnectError as exc:
            set_status(str(exc), 5.0)

    def refresh_fleet_cache():
        """Refresh fleet list using datasource if available, otherwise local scan."""
        nonlocal fleet_cache
        if wrapper._datasource is not None and source_type != 'direct':
            fleet_cache = sorted(get_fleet_info(wrapper._datasource), key=lambda d: d["port"])
        else:
            fleet_cache = sorted(get_fleet_info(), key=lambda d: d["port"])

    # Auto-connect for non-direct sources (FastAPI / MQTT)
    if source_type != 'direct':
        connect_datasource_source()

    # Initial fleet scan using appropriate method (datasource or local)
    refresh_fleet_cache()

    # ── Render loop ───────────────────────────────────────────────────────────
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        MIN_ROWS, MIN_COLS = 35, 100

        # Size guard
        if height < MIN_ROWS or width < MIN_COLS:
            msg = (f" Terminal too small ({width}×{height})"
                   f" — resize to at least {MIN_COLS}×{MIN_ROWS} ")
            try:
                stdscr.addstr(0, 0, msg.center(width), curses.A_BOLD | curses.color_pair(3))
            except curses.error:
                pass
            stdscr.refresh()
            try:
                if stdscr.getkey() in ['q', 'Q']:
                    break
            except curses.error:
                pass
            time.sleep(0.2)
            continue

        # ── Header ────────────────────────────────────────────────────────────
        header = f" BENCHLAB Telemetry v{__version__} "
        try:
            stdscr.addstr(0, 0, header.center(width), curses.A_BOLD | curses.color_pair(1))
        except curses.error:
            pass

        # ── Tab bar ───────────────────────────────────────────────────────────
        tab_x = 2
        for i, name in enumerate(TAB_NAMES):
            label = f" {i}:{name} "
            try:
                if i == current_tab:
                    stdscr.addstr(2, tab_x, label, curses.color_pair(1) | curses.A_BOLD)
                else:
                    stdscr.addstr(2, tab_x, label, curses.color_pair(7))
            except curses.error:
                pass
            tab_x += len(label) + 1

        # Separator line
        try:
            stdscr.addstr(3, 0, "─" * width, curses.color_pair(7))
        except curses.error:
            pass

        # ── Get current snapshot ──────────────────────────────────────────────
        state = wrapper.snapshot()

        # ── Dispatch to tab renderer ───────────────────────────────────────────
        try:
            if current_tab == 0:
                src_type = wrapper._source_type if wrapper._source_type else 'direct'
                render_fleet(stdscr, height, width, state, fleet_cache,
                             fleet_index, active_device, state['connected'], src_type)
            elif current_tab == 1:
                render_device(stdscr, state, args.interval)
            elif current_tab == 2:
                render_system(stdscr, state, stats, active_device)
            elif current_tab == 3:
                render_voltage(stdscr, state, stats, active_device)
            elif current_tab == 4:
                render_temperature(stdscr, state, stats, active_device)
            elif current_tab == 5:
                render_fans(stdscr, state, stats, active_device)
        except curses.error:
            pass

        # ── Status bar ────────────────────────────────────────────────────────
        try:
            stdscr.addstr(height - 2, 0, "─" * width, curses.color_pair(7))
        except curses.error:
            pass

        # Left: transient message or last error
        if status_msg and time.monotonic() < status_msg_expires:
            left_msg = status_msg
            left_col = curses.color_pair(4)
        elif state.get('last_error'):
            left_msg = f"! {state['last_error']}"
            left_col = curses.color_pair(3)
        else:
            left_msg = "q=quit  ?=help  r=reset stats  f=rescan"
            left_col = curses.color_pair(7)

        # Right side: uptime | conn status | port
        ct = state.get('connection_time')
        if state['connected'] and ct:
            uptime_str = str(datetime.now() - ct).split('.')[0]
            uptime_part = f"up {uptime_str}  "
        else:
            uptime_part = ""
        dev_str = active_device or "no device"
        con_str = "CONN" if state['connected'] else "DISC"
        con_col = curses.color_pair(2) if state['connected'] else curses.color_pair(3)
        right_msg = f"{uptime_part}{con_str}  {dev_str}"

        try:
            stdscr.addstr(height - 1, 2, left_msg[:width - len(right_msg) - 4], left_col)
            stdscr.addstr(height - 1, width - len(right_msg) - 2,
                          right_msg, con_col)
        except curses.error:
            pass

        stdscr.refresh()

        # ── Key handling ──────────────────────────────────────────────────────
        try:
            key = stdscr.getkey()
        except curses.error:
            continue

        if key in ('q', 'Q'):
            break
        elif key == '?':
            show_help(stdscr, height, width)
        elif key in ('KEY_RIGHT', 'l'):
            current_tab = (current_tab + 1) % len(TAB_NAMES)
        elif key in ('KEY_LEFT', 'h'):
            current_tab = (current_tab - 1) % len(TAB_NAMES)
        elif key.isdigit() and int(key) < len(TAB_NAMES):
            current_tab = int(key)
        elif key in ('r', 'R'):
            if active_device:
                stats.reset(active_device)
                set_status("Stats reset.")
        elif key == 'f':
            refresh_fleet_cache()
            fleet_index = 0
            ds_label = wrapper._source_type.upper() if wrapper._datasource else 'serial'
            set_status(f"Fleet rescanned ({ds_label}) — {len(fleet_cache)} device(s) found.")
        elif key == "KEY_RESIZE":
            pass
        elif current_tab == 0 and fleet_cache:
            if key == 'KEY_UP':
                fleet_index = (fleet_index - 1) % len(fleet_cache)
            elif key == 'KEY_DOWN':
                fleet_index = (fleet_index + 1) % len(fleet_cache)
            elif key in ('\n', '\r', 'KEY_ENTER'):
                selected_port = fleet_cache[fleet_index]["port"]
                if source_type != 'direct':
                    # When using FastAPI/MQTT, connect via datasource (not direct serial)
                    selected_uid = fleet_cache[fleet_index].get("uid", "")
                    try:
                        wrapper._uid = selected_uid
                        wrapper._port = selected_port
                        active_device = selected_port
                        set_status(f"Selected {selected_uid} via {wrapper.source_type}")
                    except Exception as exc:
                        set_status(f"Select failed: {exc}", 4.0)
                else:
                    connect_to(selected_port)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    wrapper.stop()
