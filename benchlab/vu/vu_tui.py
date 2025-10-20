# benchlab/vu/vu_tui.py
import curses
import json
import logging
import requests
import time
import signal
import sys
import threading
from pathlib import Path

from benchlab.core.sensor_translation import translate_sensor_struct
from benchlab.vu import devices
from benchlab.vu.vu_server_manager import start_vu_server, check_vu_server, terminate_vu_server, forward_logs

# --- Logger setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Make sure logs go to console
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)

VU_SERVER_CONFIG = Path(__file__).parent / "vu_server.config"
VU_DIAL_CONFIG = Path(__file__).parent / "vu_dial.config"

def load_json(path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_available_sensors():
    class DummyStruct:
        PowerReadings = [type("P", (), {"Power": 0, "Voltage":0, "Current":0})() for _ in range(12)]
        Vin = [0]*16
        Vdd = 0
        Vref = 0
        Tchip = 0
        Tamb = 0
        Hum = 0
        Ts = [0]*4
        Fans = [type("F", (), {"Duty":0, "Tach":0, "Enable":0})() for _ in range(4)]
        FanExtDuty = 0
    return list(translate_sensor_struct(DummyStruct()).keys())

SENSORS = get_available_sensors()

class VUTUI:
    def __init__(self, stdscr, server_proc=None):
        self.stdscr = stdscr
        self.running = True
        self.server_proc = server_proc

        try:
            curses.curs_set(0)
        except:
            pass
        self.stdscr.nodelay(False)

        # Tabs
        self.tab_index = 0
        self.tab2_cursor = 0
        self.tab2_start_line = 0
        self.tab3_cursor = 0

        # Colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_YELLOW)  # Active tab
        curses.init_pair(2, curses.COLOR_GREEN, -1)                   # Status OK
        curses.init_pair(3, curses.COLOR_RED, -1)                     # Warnings/errors
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)                 # Temperature
        curses.init_pair(6, curses.COLOR_WHITE, -1)                   # Inactive tab
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Title bar

        # Load configs & devices
        self.reload_configs()

    # -------------------- CLEANUP --------------------
    def cleanup(tui: "VUTUI", server_proc=None):
        logger.info("Starting cleanup...")

        # Close benchlab ports
        for b in getattr(tui, "benchlabs", []):
            try:
                if hasattr(b, "close") and callable(b.close):
                    b.close()
                    logger.info(f"Closed serial port for benchlab {b.get('port','?')}")
            except Exception as e:
                logger.warning(f"Failed to close port {b.get('port','?')}: {e}")

        # Terminate VU server if it was started
        if server_proc:
            logger.info("Terminating local VU server...")
            terminate_vu_server(server_proc)
            logger.info("Local VU server terminated.")

        logger.info("Cleanup complete.")

    # -------------------- CONFIG / DIALS --------------------
    def reload_configs(self):
        self.server_cfg = load_json(VU_SERVER_CONFIG, {})
        self.dial_cfg = load_json(VU_DIAL_CONFIG, [])
        self.benchlabs = devices.get_benchlab_devices()

        self.vu_server_running = devices.vu_server_check(
            self.server_cfg.get("vu_server_url", "http://localhost:5340"),
            self.server_cfg.get("api_key", "")
        )

        if self.vu_server_running:
            self.vu_dials = devices.get_vu_dials(
                self.server_cfg.get("vu_server_url", "http://localhost:5340"),
                self.server_cfg.get("api_key", "")
            )
        else:
            self.vu_dials = []

        # Default config for unconfigured dials
        for uid, name in self.vu_dials:
            if not any(d.get("dial_uid") == uid for d in self.dial_cfg):
                self.dial_cfg.append({
                    "dial_uid": uid,
                    "dial_name": name,
                    "benchlab_port": "Unknown",
                    "benchlab_uid": "",
                    "sensor": "",
                    "min": 0,
                    "max": 100,
                    "backlight": [100, 100, 100],
                    "easing_dial": [50, 5],
                    "easing_backlight": [50, 5]
                })

    def update_dial_name_on_server(self, dial_uid, new_name):
        url = f"{self.server_cfg.get('vu_server_url','http://localhost:5340')}/api/v0/dial/{dial_uid}/name"
        params = {
            "key": self.server_cfg.get("api_key", ""),
            "name": new_name
        }
        try:
            resp = requests.get(url, params=params, timeout=3)
            if resp.status_code in (200, 201):
                return True
            else:
                import logging
                logging.warning(f"Failed to update dial {dial_uid} name on server: {resp.status_code} {resp.text}")
        except Exception as e:
            import logging
            logging.warning(f"Exception updating dial {dial_uid} name on server: {e}")
        return False

    # -------------------- HELPERS --------------------
    def get_or_create_cfg_entry(self, dial):
        cfg_entry = next((d for d in self.dial_cfg if d['dial_uid'] == dial['uid']), None)
        if cfg_entry:
            return cfg_entry
        cfg_entry = {
            "dial_uid": dial['uid'],
            "dial_name": dial['name'],
            "benchlab_port": dial.get("benchlab_port", "Unknown"),
            "benchlab_uid": dial.get("benchlab_uid", ""),
            "sensor": dial.get("sensor", "Not configured"),
            "min": 0,
            "max": 100,
            "backlight": [100, 100, 100],
            "easing_dial": [50, 5],
            "easing_backlight": [50, 5]
        }
        self.dial_cfg.append(cfg_entry)
        return cfg_entry

    # -------------------- TABS --------------------
    def draw_tabs(self):
        tabs = ["Overview", "Server Config", "Dial Mapping"]
        x = 0
        y = 2  # always draw tabs on line 2
        self.h, self.w = self.stdscr.getmaxyx()

        for idx, name in enumerate(tabs):
            if idx == self.tab_index:
                self.stdscr.attron(curses.color_pair(1))
                self.stdscr.addstr(y, x, f" {name} ")
                self.stdscr.attroff(curses.color_pair(1))
            else:
                self.stdscr.attron(curses.color_pair(6))
                self.stdscr.addstr(y, x, f" {name} ")
                self.stdscr.attroff(curses.color_pair(6))
            x += len(name) + 3  # spacing

    # -------------------- TAB 1 --------------------
    def draw_tab1(self):
        self.stdscr.clear()
        self.h, self.w = self.stdscr.getmaxyx()
        y_start = 4
        footer_height = 1
        y = y_start

        # --- Header ---
        self.stdscr.attron(curses.color_pair(7))
        self.stdscr.addstr(0, 0, " BENCHLAB VU CONFIGURATION ".center(self.w))
        self.stdscr.attroff(curses.color_pair(7))

        self.draw_tabs()

        # --- VU Server Config ---
        self.stdscr.addstr(y, 2, "VU Server Config:", curses.A_UNDERLINE)
        y += 1
        keys = list(self.server_cfg.keys())
        key_col_width = max((len(k) for k in keys), default=0) + 2
        for k, v in self.server_cfg.items():
            try:
                self.stdscr.addstr(y, 4, f"{k}: ")
                self.stdscr.addstr(y, 4 + key_col_width, f"{v}")
            except curses.error:
                break
            y += 1
        y += 1

        # --- Benchlab devices ---
        self.stdscr.addstr(y, 2, "Available Benchlab Devices:", curses.A_UNDERLINE)
        y += 1
        for b in self.benchlabs:
            try:
                self.stdscr.addstr(y, 4, f"{b['uid']} - {b['port']}")
            except curses.error:
                break
            y += 1
        y += 1

        # --- VU Dials ---
        self.stdscr.addstr(y, 2, "Available VU Dials:", curses.A_UNDERLINE)
        y += 1
        if self.vu_server_running:
            for uid, name in self.vu_dials:
                try:
                    self.stdscr.addstr(y, 4, f"{uid} - {name}")
                except curses.error:
                    break
                y += 1
        else:
            try:
                self.stdscr.addstr(y, 4, "N/A: VU server is not running!", curses.color_pair(3))
            except curses.error:
                pass
            y += 1
        y += 1

        # --- VU Dial Config Table ---
        self.stdscr.addstr(y, 2, "VU Dial Config:", curses.A_UNDERLINE)
        y += 1

        if not self.dial_cfg:
            try:
                self.stdscr.addstr(y, 4, "No VU dials configured", curses.color_pair(3))
            except curses.error:
                pass
            y += 1
        else:
            # Table headers
            headers = ["UID", "Name", "Sensor", "Min/Max", "Benchlab", "Backlight", "Easing"]
            col_widths = [24, 10, 10, 11, 12, 15, 10]  # widths for alignment only
            x = 4
            for i, h in enumerate(headers):
                try:
                    self.stdscr.addstr(y, x, h, curses.A_UNDERLINE)
                except curses.error:
                    pass
                x += col_widths[i] + 1
            y += 1

            # Table rows
            for dial in self.dial_cfg:
                x = 4
                row = [
                    str(dial.get("dial_uid", "")),
                    str(dial.get("dial_name", "")),
                    str(dial.get("sensor", "")),
                    f"{dial.get('min', '')}/{dial.get('max', '')}",
                    str(dial.get("benchlab_port", "")),
                    str(dial.get("backlight", "")),
                    f"{dial.get('easing_dial', '')}",
                ]
                for i, cell in enumerate(row):
                    try:
                        self.stdscr.addstr(y, x, cell.ljust(col_widths[i]))
                    except curses.error:
                        pass
                    x += col_widths[i] + 1
                y += 1

        # --- Footer with Provisioning ---
        self.stdscr.addstr(self.h-1, 0, "TAB: Switch tabs | r: Reload | p: Provision new devices | q: Quit")


    def handle_tab1_input(self, key):
        if key in (ord('r'), ord('R')):
            self.reload_configs()
        elif key in (ord('p'), ord('P')):
            self.stdscr.addstr(self.h-3, 0, "Scanning and provisioning new devices...".ljust(self.w))
            self.stdscr.refresh()
            newly_provisioned = devices.provision_missing_vu_dials(
                self.server_cfg.get("vu_server_url", "http://localhost:5340"),
                self.server_cfg.get("api_key", "")
            )
            self.vu_dials = devices.get_vu_dials(
                self.server_cfg.get("vu_server_url", "http://localhost:5340"),
                self.server_cfg.get("api_key", "")
            )
            msg = f"New devices provisioned: {newly_provisioned}" if newly_provisioned else "No new devices found."
            self.stdscr.addstr(self.h-3, 0, msg.ljust(self.w))
            self.stdscr.refresh()
            curses.napms(1000)

    # -------------------- TAB 2 --------------------
    def draw_tab2(self):
        self.stdscr.erase()  # erase instead of clear; safer
        h, w = self.stdscr.getmaxyx()

        # --- Header ---
        self.stdscr.attron(curses.color_pair(7))
        self.stdscr.addstr(0, 0, " BENCHLAB VU CONFIGURATION ".center(w))
        self.stdscr.attroff(curses.color_pair(7))

        # --- Tabs ---
        self.draw_tabs()

        y = 4
        keys = list(self.server_cfg.keys())
        key_col_width = max((len(k) for k in keys), default=0) + 2
        visible_lines = self.h - y - 4

        if self.tab2_cursor < self.tab2_start_line:
            self.tab2_start_line = self.tab2_cursor
        elif self.tab2_cursor >= self.tab2_start_line + visible_lines:
            self.tab2_start_line = self.tab2_cursor - visible_lines + 1

        for idx, key in enumerate(keys[self.tab2_start_line:self.tab2_start_line + visible_lines]):
            val = str(self.server_cfg[key])
            line_y = y + idx
            key_text = f"{key}:".ljust(key_col_width)
            self.stdscr.addstr(line_y, 4, key_text)
            if idx + self.tab2_start_line == self.tab2_cursor:
                self.stdscr.addstr(line_y, 4 + key_col_width, val, curses.color_pair(1))
            else:
                self.stdscr.addstr(line_y, 4 + key_col_width, val)

        self.stdscr.addstr(self.h-1, 0, "↑/↓: Navigate | Enter: Edit | s: Save | TAB: Switch tabs")

    def handle_tab2_input(self, key):
        keys = list(self.server_cfg.keys())
        max_idx = len(keys) - 1

        if key == curses.KEY_UP:
            self.tab2_cursor = max(0, self.tab2_cursor - 1)
        elif key == curses.KEY_DOWN:
            self.tab2_cursor = min(max_idx, self.tab2_cursor + 1)
        elif key in (curses.KEY_ENTER, ord('\n')):
            key_name = keys[self.tab2_cursor]
            old_val = str(self.server_cfg[key_name])
            curses.echo()
            prompt = f"Enter new value for {key_name}: "
            self.stdscr.addstr(self.h-2, 0, " " * (self.w-1))
            self.stdscr.addstr(self.h-2, 0, prompt)
            new_val = self.stdscr.getstr(self.h-2, len(prompt), 60).decode()
            curses.noecho()
            if new_val:
                if old_val.isdigit():
                    try:
                        new_val = int(new_val)
                    except ValueError:
                        new_val = old_val
                self.server_cfg[key_name] = new_val
        elif key in (ord('s'), ord('S')):
            save_json(VU_SERVER_CONFIG, self.server_cfg)
            self.stdscr.addstr(self.h-2, 0, "Config saved!".ljust(self.w), curses.color_pair(2))
            self.stdscr.refresh()
            time.sleep(1)

    # -------------------- TAB 3 --------------------
    def draw_tab3(self):
        self.stdscr.erase()  # erase instead of clear; safer
        h, w = self.stdscr.getmaxyx()

        # --- Header ---
        self.stdscr.attron(curses.color_pair(7))
        self.stdscr.addstr(0, 0, " BENCHLAB VU CONFIGURATION ".center(w))
        self.stdscr.attroff(curses.color_pair(7))

        # --- Tabs ---
        self.draw_tabs()

        y = 4
        # Benchlab devices
        self.stdscr.addstr(y, 2, "Available Benchlab Devices:", curses.A_UNDERLINE)
        y += 1
        for b in self.benchlabs:
            self.stdscr.addstr(y, 2, f"{b['port']} - {b['uid']}")
            y += 1
        y += 1

        # Build combined dial list
        self.all_dials = []
        for uid, name in self.vu_dials:
            cfg_entry = next((d for d in self.dial_cfg if d.get("dial_uid") == uid), None)
            benchlab_port = cfg_entry.get("benchlab_port","Unknown") if cfg_entry else "Unknown"
            sensor = cfg_entry.get("sensor","Not configured") if cfg_entry else "Not configured"
            min_val = cfg_entry.get("min",0) if cfg_entry else 0
            max_val = cfg_entry.get("max",100) if cfg_entry else 100
            self.all_dials.append({
                "uid": uid,
                "name": name,
                "benchlab_port": benchlab_port,
                "sensor": sensor,
                "min": min_val,
                "max": max_val
            })

        # Column widths
        col_uid_width = max((len(d["uid"]) for d in self.all_dials), default=0) + 2
        col_name_width = max((len(d["name"]) for d in self.all_dials), default=0) + 2
        col_port_width = max((len(d["benchlab_port"]) for d in self.all_dials), default=0) + 2
        col_min_width = 5
        col_max_width = 5

        self.stdscr.addstr(y, 2, f"{'Dial UID':<{col_uid_width}} {'Dial Name':<{col_name_width}} "
                                   f"{'Benchlab':<{col_port_width}} {'Min':<{col_min_width}} "
                                   f"{'Max':<{col_max_width}} Sensor", curses.A_UNDERLINE)
        y += 1

        for idx, dial in enumerate(self.all_dials):
            line = f"{dial['uid']:<{col_uid_width}} {dial['name']:<{col_name_width}} " \
                   f"{dial['benchlab_port']:<{col_port_width}} {dial['min']:<{col_min_width}} " \
                   f"{dial['max']:<{col_max_width}} {dial['sensor']}"
            self.stdscr.addstr(y + idx, 2, line[:self.w-3], curses.color_pair(1) if idx == self.tab3_cursor else 0)

        self.stdscr.addstr(self.h-1, 0, "Enter: Configure Dial | ↑/↓: Navigate | TAB: Switch tabs | r: Reload | q: Quit"[:self.w-1])

    def handle_tab3_input(self, key):
        if not self.all_dials:
            return
        if key == curses.KEY_UP:
            self.tab3_cursor = max(0, self.tab3_cursor - 1)
        elif key == curses.KEY_DOWN:
            self.tab3_cursor = min(len(self.all_dials)-1, self.tab3_cursor + 1)
        elif key in (ord('\n'), curses.KEY_ENTER):
            self.dial_mapping()
        elif key == ord('r'):
            self.reload_configs()

    # -------------------- DIAL MAPPING --------------------
    def dial_mapping(self):
        if not self.all_dials:
            h, w = self.stdscr.getmaxyx()
            self.stdscr.addstr(h-2, 0, "No VU dials available!", curses.color_pair(3))
            self.stdscr.refresh()
            curses.napms(1000)
            return

        h, w = self.stdscr.getmaxyx()
        dial = self.all_dials[self.tab3_cursor]

        # --- Step 1: Dial Name ---
        current_name = dial.get("name", "")
        prompt = f"Set Dial Name [{current_name}]: "
        self.stdscr.move(h-3, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addstr(h-3, 0, prompt[:w-1])
        curses.echo()
        inp = self.stdscr.getstr(h-3, len(prompt), 60).decode().strip()
        curses.noecho()
        if inp:
            dial["name"] = inp

        # Ensure cfg_entry exists or update
        cfg_entry = next((d for d in self.dial_cfg if d['dial_uid'] == dial['uid']), None)
        if not cfg_entry:
            cfg_entry = {
                "dial_uid": dial['uid'],
                "dial_name": dial.get("name", ""),
                "benchlab_port": dial.get("benchlab_port", "Unknown"),
                "benchlab_uid": dial.get("benchlab_uid", ""),
                "sensor": dial.get("sensor", "Not configured"),
                "min": 0,
                "max": 100,
                "backlight": [100, 100, 100],
                "easing_dial": [50, 5],
                "easing_backlight": [50, 5]
            }
            self.dial_cfg.append(cfg_entry)
        else:
            if inp:
                cfg_entry["dial_name"] = inp

        success = self.update_dial_name_on_server(dial['uid'], dial["name"])
        if success:
            self.stdscr.addstr(h-2, 0, "Dial name updated on server!".ljust(w), curses.color_pair(2))
        else:
            self.stdscr.addstr(h-2, 0, "Failed to update dial name on server!".ljust(w), curses.color_pair(3))
        self.stdscr.refresh()
        curses.napms(1000)

        # --- Step 2: Benchlab Port ---
        current_port = dial.get("benchlab_port", "")
        ports = [b["port"] for b in self.benchlabs]
        prompt = f"Select Benchlab Port [{current_port}]: "
        self.stdscr.move(h-3, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addstr(h-3, 0, prompt[:w-1])
        curses.echo()
        choice = self.stdscr.getstr(h-3, len(prompt), 20).decode().strip()
        curses.noecho()
        if choice and choice in ports:
            dial["benchlab_port"] = choice
            dial["benchlab_uid"] = next((b["uid"] for b in self.benchlabs if b["port"] == choice), "")
            cfg_entry["benchlab_port"] = choice
            cfg_entry["benchlab_uid"] = dial["benchlab_uid"]

        # --- Step 3: Sensor Selection ---
        max_display_rows = h - 6
        num_sensors = len(SENSORS)
        col_width = max(len(s) for s in SENSORS) + 4
        num_cols = max(1, w // col_width)
        num_rows = (num_sensors + num_cols - 1) // num_cols

        while True:
            # Clear sensor display area
            for row in range(num_rows):
                self.stdscr.move(h - num_rows - 3 + row, 0)
                self.stdscr.clrtoeol()

            # Display sensors
            for idx, sensor in enumerate(SENSORS):
                row = idx % num_rows
                col = idx // num_rows
                x = col * col_width
                s_str = f"{idx + 1}. {sensor}"
                self.stdscr.addstr(h - num_rows - 3 + row, x, s_str[:col_width-1])

            prompt = f"Select sensor number for {dial['name']} or 'q' to skip: "
            self.stdscr.move(h-4, 0)
            self.stdscr.clrtoeol()
            self.stdscr.addstr(h-3, 0, prompt[:w-1])
            curses.echo()
            inp = self.stdscr.getstr(h-3, len(prompt), 5).decode().strip()
            curses.noecho()

            if inp.lower() == 'q':
                break

            try:
                sel = int(inp) - 1
                if 0 <= sel < len(SENSORS):
                    dial['sensor'] = SENSORS[sel]
                    cfg_entry['sensor'] = SENSORS[sel]
                    break
            except ValueError:
                continue

        # --- Step 4: Min / Max ---
        current_min = cfg_entry.get("min", 0)
        current_max = cfg_entry.get("max", 100)

        # MIN
        prompt = f"Set MIN [{current_min}]: "
        self.stdscr.move(h-3, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addstr(h-3, 0, prompt[:w-1])
        curses.echo()
        inp = self.stdscr.getstr(h-3, len(prompt), 10).decode().strip()
        curses.noecho()
        val_min = float(inp) if inp else current_min

        # MAX
        prompt = f"Set MAX [{current_max}]: "
        self.stdscr.move(h-3, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addstr(h-3, 0, prompt[:w-1])
        curses.echo()
        inp = self.stdscr.getstr(h-3, len(prompt), 10).decode().strip()
        curses.noecho()
        val_max = float(inp) if inp else current_max

        if val_max <= val_min:
            self.stdscr.addstr(h-2, 0, "MAX must be greater than MIN!", curses.color_pair(3))
            self.stdscr.refresh()
            curses.napms(1500)
        else:
            cfg_entry["min"] = val_min
            cfg_entry["max"] = val_max
            save_json(VU_DIAL_CONFIG, self.dial_cfg)
            self.stdscr.addstr(h-2, 0, "Dial mapping saved!".ljust(w), curses.color_pair(2))
            self.stdscr.refresh()
            curses.napms(1000)

    # -------------------- MAIN LOOP --------------------
    def run(self):
        while self.running:
            if self.tab_index == 0:
                self.draw_tab1()
            elif self.tab_index == 1:
                self.draw_tab2()
            elif self.tab_index == 2:
                self.draw_tab3()

            self.stdscr.refresh()
            key = self.stdscr.getch()

            if key == ord('\t'):
                self.tab_index = (self.tab_index + 1) % 3
            elif key in (ord('q'), ord('Q')):
                break
            else:
                if self.tab_index == 0:
                    self.handle_tab1_input(key)
                elif self.tab_index == 1:
                    self.handle_tab2_input(key)
                elif self.tab_index == 2:
                    self.handle_tab3_input(key)

# -------------------- RUN TUI --------------------
def run_vu_tui(stdscr, server_proc=None):
    tui = VUTUI(stdscr, server_proc=server_proc)

    def sigint_handler(sig, frame):
        tui.running = False
        logger.info("Ctrl+C detected. Exiting TUI...")

    signal.signal(signal.SIGINT, sigint_handler)

    try:
        tui.run()
    finally:
        logger.info("Cleaning up TUI and server...")
        if tui:
            tui.cleanup()
        if server_proc:
            terminate_vu_server(server_proc)
            logger.info("VU server terminated.")

# -------------------- LAUNCH FUNCTION --------------------
def launch_vu_config():
    # Pre-TUI messages
    logger.info("Launching the BENCHLAB VU Server & Dials Configuration")
    time.sleep(1)
    logger.info("Launch updater with -vu")
    time.sleep(1)
    logger.info("Checking for VU server ... ")
    time.sleep(1)

    server_cfg = load_json(VU_SERVER_CONFIG, {})
    server_url = server_cfg.get("vu_server_url", "http://localhost:5340")
    api_key = server_cfg.get("api_key", "")

    server_proc = None
    if not check_vu_server(server_url, api_key):
        logger.info("No server detected — starting local VU server...")
        server_proc = start_vu_server()
        if server_proc:
            logger.info(f"Local VU server started at {server_url}")
            threading.Thread(target=forward_logs, args=(server_proc,), daemon=True).start()
        else:
            logger.warning("Failed to start local VU server.")
    else:
        logger.info(f"VU server already running at {server_url}")

    time.sleep(1)

    # Launch curses TUI
    curses.wrapper(run_vu_tui, server_proc)

if __name__ == "__main__":
    main()
