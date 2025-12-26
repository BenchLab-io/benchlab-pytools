# benchlab_overview.py

import os
import signal
import time
import threading

from PIL import Image, ImageDraw, ImageFont

from benchlab.wigidash.benchlab_ui import load_fonts, draw_header, draw_footer, bind_button, load_logo, UIButton, UITheme, BUTTON_DEFS
from benchlab.wigidash.benchlab_utils import display_image, KeepAliveManager, get_logger, clear_display

logger = get_logger("BenchlabOverview")

class BenchlabOverview:
    SCREEN_WIDTH = UITheme.SCREEN_WIDTH
    SCREEN_HEIGHT = UITheme.SCREEN_HEIGHT
    HEADER_HEIGHT = UITheme.HEADER_HEIGHT
    FOOTER_HEIGHT = UITheme.FOOTER_HEIGHT
    PADDING = UITheme.PADDING
    COLOR_SECTION = UITheme.COLOR_SECTION
    COLOR_TEXT = UITheme.COLOR_TEXT

    def __init__(self, wigidash, wigi=None, telemetry_history=None, telemetry_context=None, manager=None):
        if not wigidash or not wigi:
            raise ValueError("wigidash and wigi cannot be None")

        # Wigi device & session
        self.wigidash = wigidash
        self.wigi = wigi
        self.telemetry_history = telemetry_history 
        self.telemetry_context = telemetry_context
        self.manager = manager
        self.keepalive = KeepAliveManager(self.wigidash)
        self.running = False

        # Fonts and logo from central UI
        self.ui_fonts = load_fonts()
        self.ui_logo = load_logo()

        # Device info & telemetry
        if self.telemetry_history:
            # Pull the latest snapshot from the shared telemetry
            self.sensor_data = self.telemetry_history.latest_snapshot()
        else:
            self.sensor_data = {}
        self.device_info = getattr(wigi, "device_info", {})
        self.uid = getattr(wigi, "uid", "N/A")
        self.ser = getattr(wigi, "ser", None)
        self.history = getattr(wigi, "history", None)

        # Touch controls
        self.footer_btn_config = None
        self.requested_graph_metrics = None
        self.x1 = self.x2 = self.x3 = 0
        self.col1_width = self.col2_width = self.col3_width = 0

    # -------------------------------
    # Page Handling
    # -------------------------------

    def start(self):
        logger.info("Starting Overview page")
        self.running = True

    # -------------------------------
    # Touch Handling
    # -------------------------------

    def check_touch(self, touch):
        if not getattr(self, "running", False):
            return

        now = int(time.monotonic() * 1000)
        if now - getattr(self, "last_touch_time", 0) < 0.1:
            return

        if touch is None or getattr(touch, "Type", 0) == 0:
            return

        x, y = getattr(touch, "X", 0), getattr(touch, "Y", 0)

        # -------------------------------------------------------
        # 1) CARD TOUCH HANDLING (Graph Page)
        # -------------------------------------------------------
        padding = self.PADDING
        header_h = self.HEADER_HEIGHT

        # --- Top cards ---
        top_y = header_h + padding
        top_h = 160

        cards = [
            (self.x1, top_y, self.x1 + self.col1_width, top_y + top_h,
                ["SYS_Power", "CPU_Power", "GPU_Power", "MB_Power"]),
            (self.x2, top_y, self.x2 + self.col2_width, top_y + top_h,
                ["Chip_Temp", "Ambient_Temp", "Humidity",
                 "Temp_Sensor_1","Temp_Sensor_2","Temp_Sensor_3","Temp_Sensor_4"]),
            (self.x3, top_y, self.x3 + self.col3_width, top_y + top_h,
                [k for k in self.sensor_data.keys() if k.startswith("Fan")]),
        ]

        # --- Bottom cards ---
        bottom_y = top_y + top_h + padding
        bottom_h = 305
        col_width = (self.SCREEN_WIDTH - 5*padding) // 4

        power_x = padding
        current_x = padding + col_width + padding
        voltage_x = padding + 2*(col_width + padding)

        all_rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2']

        cards += [
            (power_x, bottom_y, power_x + col_width, bottom_y + bottom_h,
                [f"{r}_Power" for r in all_rails if f"{r}_Power" in self.sensor_data]),
            (current_x, bottom_y, current_x + col_width, bottom_y + bottom_h,
                [f"{r}_Current" for r in all_rails if f"{r}_Current" in self.sensor_data]),
            (voltage_x, bottom_y, voltage_x + col_width, bottom_y + bottom_h,
                [f"{r}_Voltage" for r in all_rails if f"{r}_Voltage" in self.sensor_data]),
        ]

        vins_x = padding + 3*(col_width + padding)

        vin_keys = [k for k in self.sensor_data.keys() if k.startswith("VIN_")]
        cards += [
            (vins_x, bottom_y, vins_x + col_width, bottom_y + bottom_h,
                vin_keys + ["Vdd", "Vref"])
        ]


        # --- Detect card hit ---
        for (x0, y0, x1, y1, metrics) in cards:
            if x0 <= x <= x1 and y0 <= y <= y1:
                logger.info(f"Card pressed, opening graph for: {metrics}")
                self.running = False
                try:
                    self.stop()
                except:
                    pass
                self.requested_graph_metrics = metrics
                return

        # -------------------------------------------------------
        # 2) FOOTER BUTTON HANDLING
        # -------------------------------------------------------
        if not self.footer_btn_config:
            return

        for btn in self.footer_btn_config:
            if btn["x0"] <= x <= btn["x1"] and btn["y0"] <= y <= btn["y1"]:
                logger.info(f"Footer button pressed: {btn.get('text','')}")
                if btn.get("callback"):
                    btn["callback"]()

                return

    def return_to_fleet(self):
        """Signal launcher to switch back to fleet."""
        logger.info("Switching back to fleetSelect.")
        self.running = False

    # -------------------------------
    # Rendering
    # -------------------------------

    def render_and_display(self):
        """Main-thread-safe rendering call"""
        
        if self.telemetry_history:
            # Always use the latest sensor data from the shared telemetry history
            self.sensor_data = self.telemetry_history.latest_snapshot()
        else:
            self.sensor_data = {}
        
        self.device_info = getattr(self.wigi, "device_info", {})
        self.uid = getattr(self.wigi, "uid", "N/A")
        img = self.render_overview()
        display_image(self.wigidash, img)

    def render_overview(self):
        img = Image.new('RGB', (self.SCREEN_WIDTH, self.SCREEN_HEIGHT), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)

        padding = 8
        header_height = 60
        title_spacing = 2
        line_height = 18
        data = self.sensor_data or {}

        self.padding = padding
        self.header_height = header_height

        total_top_width = self.SCREEN_WIDTH - 4 * padding
        self.col1_width = self.col2_width = int(total_top_width * 0.25) - 3
        self.col3_width = total_top_width - self.col1_width - self.col2_width - 1

        self.x1 = padding
        self.x2 = self.x1 + self.col1_width + padding
        self.x3 = self.x2 + self.col2_width + padding

        # ---- Helper: draw rounded card ----
        def rounded_card(x, y, w, h, title):
            draw.rounded_rectangle(
                [x, y, x+w, y+h], radius=12, outline=(200,200,200), width=1, fill=(15,15,15)
            )
            draw.text((x+8, y+8),
                title,
                fill=self.COLOR_SECTION,
                font=self.ui_fonts["title"])

        # ---- Header ----
        draw_header(draw, img, self.ui_fonts, "BENCHLAB TELEMETRY", self.ui_logo)

        # ---- Top cards ----
        top_y = header_height + padding
        top_height = 160

        x1, x2, x3 = self.x1, self.x2, self.x3
        y1 = y2 = y3 = top_y

        # Summary
        rounded_card(self.x1, top_y, self.col1_width, top_height, "SUMMARY")
        ly = y1 + 28 + title_spacing
        for key in ["SYS_Power","CPU_Power","GPU_Power","MB_Power"]:
            draw.text((x1+10, ly),
                f"{key.split('_')[0]}: {data.get(key,0):.1f} W",
                fill=self.COLOR_TEXT,
                font=self.ui_fonts["text"])
            ly += line_height

        # Temperatures
        rounded_card(self.x2, top_y, self.col2_width, top_height, "TEMPERATURES")
        ly = y2 + 28 + title_spacing
        for key in ["Chip_Temp","Ambient_Temp","Humidity"]:
            val = data.get(key, 0)
            if key == "Humidity":
                draw.text((x2+10, ly),
                    f"{key}: {val:.1f}%",
                    fill=self.COLOR_TEXT,
                    font=self.ui_fonts["text"])
            else:
                draw.text((x2+10, ly),
                    f"{key}: {val}°C",
                    fill=self.COLOR_TEXT,
                    font=self.ui_fonts["text"])
            ly += line_height
        for i in range(1,5):
            draw.text((x2+10, ly),
                f"S{i}: {data.get(f'Temp_Sensor_{i}','N/A')}",
                fill=self.COLOR_TEXT,
                font=self.ui_fonts["text"])
            ly += line_height

        # Fans
        rounded_card(self.x3, top_y, self.col3_width, top_height, "FANS")
        fans = ["Fan1", "Fan2", "Fan3", "Fan4", "Fan5", "Fan6","Fan7","Fan8","Fan9","Ext Fan Duty"]
        half = (len(fans)+1)//2
        col_spacing = self.col3_width // 2
        for idx, name in enumerate(fans):
            ly = y3 + 28 + line_height*(idx % half) + title_spacing
            x_text = x3+10 if idx < half else x3+10+col_spacing
            if name.startswith("Fan") and name != "Ext Fan Duty":
                duty = data.get(f"{name}_Duty",0)
                rpm = data.get(f"{name}_RPM",0)
                status = "ON" if data.get(f"{name}_Status",0) else "OFF"
                draw.text((x_text, ly),
                    f"{name}: {duty}% | {rpm} RPM | {status}",
                    fill=self.COLOR_TEXT,
                    font=self.ui_fonts["text"])
            else:
                draw.text((x_text, ly),
                    f"{name}: {data.get('FanExtDuty',0)}",
                    fill=self.COLOR_TEXT,
                    font=self.ui_fonts["text"])


        # ---- Bottom cards ----
        bottom_y = top_y + top_height + padding
        bottom_height = 305
        col_width = (self.SCREEN_WIDTH - 5*padding) // 4

        all_rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2']
        sections = [("POWER","Power"),("CURRENT","Current"),("VOLTAGE","Voltage")]

        for i, (title, key_suffix) in enumerate(sections):
            x = padding + i*(col_width + padding)
            rounded_card(x, bottom_y, col_width, bottom_height, title)
            ly = bottom_y + 32

            # dynamically get rails for this section
            for r in all_rails:
                v = data.get(f"{r}_{key_suffix}", 0.0)  # ensure we build the full key
                unit = "W" if key_suffix=="Power" else ("A" if key_suffix=="Current" else "V")
                draw.text((x+12, ly),
                    f"{r}: {v:.2f} {unit}",
                    fill=self.COLOR_TEXT,
                    font=self.ui_fonts["text"])
                ly += line_height

        # VINs
        x4 = padding+3*(col_width+padding)
        rounded_card(x4, bottom_y, col_width, bottom_height, "VOLTAGE INPUTS")
        vin_keys = [f"VIN_{i}" for i in range(13)]
        ly = bottom_y + 28 + title_spacing
        for k in vin_keys:
            val = data.get(k, 0.0)
            draw.text((x4+10, ly),
                f"{k}: {val:.3f} V",
                fill=self.COLOR_TEXT,
                font=self.ui_fonts["text"])
            ly += line_height

        # Always draw Vdd and Vref
        draw.text((x4+10, ly),
            f"Vdd: {data.get('Vdd',0.0):.3f} V",
            fill=self.COLOR_TEXT,
            font=self.ui_fonts["text"])
        draw.text((x4+10, ly + line_height),
            f"Vref: {data.get('Vref',0.0):.3f} V",
            fill=self.COLOR_TEXT,
            font=self.ui_fonts["text"])

        # ---- Footer ----
        btns = [
            bind_button(UIButton.SHUTDOWN, self.manager.graceful_shutdown),
            bind_button(UIButton.SELECT_DEVICE, self.return_to_fleet),
        ]

        # Safely determine context for port/UID
        ctx = getattr(self, "telemetry_context", None)

        if ctx:
            port = getattr(ctx, "port", "N/A")
            uid = getattr(ctx, "uid", "N/A")
            device_info = getattr(ctx, "device_info", {})
            vid = device_info.get("VendorId", 0)
            pid = device_info.get("ProductId", 0)
            fw = device_info.get("FwVersion", 0)

            info_parts = [
                f"Port: {port}",
                f"Vendor ID: 0x{vid:02X}",
                f"Product ID: 0x{pid:02X}",
                f"FW: 0x{fw:02X}",
                f"UID: {uid or 'N/A'}"
            ]
            info_line = " | ".join(info_parts)
        else:
            info_line = "Port: N/A | UID: N/A"

        # Draw footer using correct fonts
        self.footer_btn_config = draw_footer(draw, self.ui_fonts, info_line, btns)


        return img
