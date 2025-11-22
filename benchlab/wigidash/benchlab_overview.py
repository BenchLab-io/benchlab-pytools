# benchlab_overview.py

import os
import signal
import time
import threading
from PIL import Image, ImageDraw, ImageFont

from benchlab.wigidash.benchlab_telemetry import TelemetryHistory
from benchlab.wigidash.benchlab_utils import display_image, KeepAliveManager, get_logger, clear_display

logger = get_logger("BenchlabOverview")

class BenchlabOverview:
    SCREEN_WIDTH = 1016
    SCREEN_HEIGHT = 592
    HEADER_HEIGHT = 60
    FOOTER_HEIGHT = 60

    COLOR_TITLE = (255, 255, 255)
    COLOR_LABEL = (200, 200, 200)
    COLOR_VALUE = (238,238,238)
    COLOR_SECTION = (252, 228, 119)
    COLOR_FOOTER = (128, 128, 128)
    PADDING = 8

    def __init__(self, wigidash, wigi):
        if not wigidash or not wigi:
            raise ValueError("wigidash and wigi cannot be None")

        # Wigi device & session
        self.wigidash = wigidash
        self.wigi = wigi
        self.keepalive = KeepAliveManager(self.wigidash)
        self.running = False

        # Device info & telemetry
        self.sensor_data = getattr(wigi, "sensor_data", {})
        self.device_info = getattr(wigi, "device_info", {})
        self.uid = getattr(wigi, "uid", "N/A")
        self.ser = getattr(wigi, "ser", None)
        self.history = getattr(wigi, "history", None)
        self.telemetry_thread = None
        self.telemetry_running = False

        # Touch controls
        self.footer_btn_config = None
        self.requested_graph_metrics = None
        self.x1 = self.x2 = self.x3 = 0
        self.col1_width = self.col2_width = self.col3_width = 0

        # Assets
        base_dir = os.path.dirname(os.path.abspath(__file__))

        font_button_path = os.path.join(base_dir, "assets", "Inter.ttf")
        font_header_path = os.path.join(base_dir, "assets", "Inter-Bold.ttf")
        font_text_path = os.path.join(base_dir, "assets", "Inter.ttf")
        font_title_path = os.path.join(base_dir, "assets", "Inter.ttf")
        logo_path = os.path.join(base_dir, "assets", "benchlab.png")

        try:
            self.font_button = ImageFont.truetype(font_button_path, 30)
            self.font_header = ImageFont.truetype(font_header_path, 30)
            self.font_text = ImageFont.truetype(font_text_path, 14)
            self.font_title = ImageFont.truetype(font_title_path, 18)
        except Exception:
            self.font_button = self.font_header = self.font_title = self.font_text = ImageFont.load_default()
            logger.warning("Default font loaded due to missing assets.")

        try:
            logo_img = Image.open(logo_path).convert("RGBA")
            logo_img.thumbnail((60, 60), Image.Resampling.LANCZOS)
            self.logo = logo_img
        except Exception as e:
            logger.warning("Logo load error: %s", e)
            self.logo = None

    # -------------------------------
    # Page Handling
    # -------------------------------

    def start(self):
        logger.info("Starting Overview page")
        self.running = True

    def stop(self):
        logger.info("Stopping Overview page")
        self.running = False
        #clear_display(self.wigidash, self.SCREEN_WIDTH, self.SCREEN_HEIGHT)


    # -------------------------------
    # Touch Handling
    # -------------------------------

    def check_touch(self, touch):
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

                try:
                    self.stop()
                except Exception as e:
                    logger.exception(f"Error during Overview shutdown: {e}")

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
        self.sensor_data = getattr(self.wigi, "sensor_data", {})
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
            draw.rounded_rectangle([x, y, x+w, y+h], radius=12, outline=(200,200,200), width=1, fill=(15,15,15))
            draw.text((x+8, y+8), title, fill=self.COLOR_SECTION, font=self.font_title)

        # ---- Header ----
        draw.rectangle([0, 0, self.SCREEN_WIDTH, header_height], fill=self.COLOR_SECTION)

        if self.logo:
            logo_y = (self.HEADER_HEIGHT - self.logo.height)//2
            img.paste(self.logo, (10, logo_y), self.logo)
            img.paste(self.logo, (self.SCREEN_WIDTH - self.logo.width - 10, logo_y), self.logo)


        header_text = "BENCHLAB TELEMETRY"
        bbox = draw.textbbox((0, 0), header_text, font=self.font_header)
        title_x = (self.SCREEN_WIDTH - (bbox[2]-bbox[0])) // 2
        title_y = (header_height - (bbox[3]-bbox[1])) // 2
        draw.text((title_x, title_y), header_text, fill=(0,0,0), font=self.font_header)

        # ---- Top cards ----
        top_y = header_height + padding
        top_height = 160

        x1, x2, x3 = self.x1, self.x2, self.x3
        y1 = y2 = y3 = top_y

        # Summary
        rounded_card(self.x1, top_y, self.col1_width, top_height, "SUMMARY")
        ly = y1 + 28 + title_spacing
        for key in ["SYS_Power","CPU_Power","GPU_Power","MB_Power"]:
            draw.text((x1+10, ly), f"{key.split('_')[0]}: {data.get(key,0):.1f} W", fill=self.COLOR_VALUE, font=self.font_text)
            ly += line_height

        # Temperatures
        rounded_card(self.x2, top_y, self.col2_width, top_height, "TEMPERATURES")
        ly = y2 + 28 + title_spacing
        for key in ["Chip_Temp","Ambient_Temp","Humidity"]:
            val = data.get(key, 0)
            if key == "Humidity":
                draw.text((x2+10, ly), f"{key}: {val:.1f}%", fill=self.COLOR_VALUE, font=self.font_text)
            else:
                draw.text((x2+10, ly), f"{key}: {val}°C", fill=self.COLOR_VALUE, font=self.font_text)
            ly += line_height
        for i in range(1,5):
            draw.text((x2+10, ly), f"S{i}: {data.get(f'Temp_Sensor_{i}','N/A')}", fill=self.COLOR_VALUE, font=self.font_text)
            ly += line_height

        # Fans
        rounded_card(self.x3, top_y, self.col3_width, top_height, "FANS")
        fans = sorted([k.replace("_RPM","") for k in data if k.startswith("Fan") and "_RPM" in k])
        fans.append("Ext Fan Duty")
        half = (len(fans)+1)//2
        col_spacing = self.col3_width // 2
        for idx, name in enumerate(fans):
            ly = y3 + 28 + line_height*(idx % half) + title_spacing
            x_text = x3+10 if idx < half else x3+10+col_spacing
            if name.startswith("Fan") and name != "Ext Fan Duty":
                duty = data.get(f"{name}_Duty",0)
                rpm = data.get(f"{name}_RPM",0)
                status = "ON" if data.get(f"{name}_Status",0) else "OFF"
                draw.text((x_text, ly), f"{name}: {duty}% | {rpm} RPM | {status}", fill=self.COLOR_VALUE, font=self.font_text)
            else:
                draw.text((x_text, ly), f"{name}: {data.get('FanExtDuty',0)}", fill=self.COLOR_VALUE, font=self.font_text)

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
                draw.text((x+12, ly), f"{r}: {v:.2f} {unit}", fill=self.COLOR_VALUE, font=self.font_text)
                ly += line_height

        # VINs
        x4 = padding+3*(col_width+padding)
        rounded_card(x4, bottom_y, col_width, bottom_height, "VOLTAGE INPUTS")
        vin_keys = sorted(
            [k for k in data.keys() if k.startswith("VIN_")],
            key=lambda x: int(x.split("_")[1])
        )
        ly = bottom_y + 28 + title_spacing  # start below the title
        for idx, k in enumerate(vin_keys):
            val = data.get(k, 0.0)
            draw.text((x4+10, ly), f"{k}: {val:.3f} V", fill=self.COLOR_VALUE, font=self.font_text)
            ly += line_height

        # Always draw Vdd and Vref
        draw.text((x4+10, ly), f"Vdd: {data.get('Vdd',0.0):.3f} V", fill=self.COLOR_VALUE, font=self.font_text)
        draw.text((x4+10, ly + line_height), f"Vref: {data.get('Vref',0.0):.3f} V", fill=self.COLOR_VALUE, font=self.font_text)

        # ---- Footer ----
        footer_height = 40
        y_footer = self.SCREEN_HEIGHT - footer_height
        btn_width, btn_height = 120, 40
        btn_spacing = padding  # space between buttons

        # Info box takes all space minus buttons area
        footer_rect = [padding, y_footer, self.SCREEN_WIDTH - (padding + (btn_width + btn_spacing) * 2), self.SCREEN_HEIGHT - padding]
        draw.rectangle(footer_rect, outline=(200, 200, 200), fill=(20, 20, 20))

        # --- Footer info text ---
        info_parts = [f"Port: {self.ser.port if self.ser else 'N/A'}"]
        if self.device_info:
            info_parts.append(f"Vendor ID: 0x{self.device_info.get('VendorId',0):03X}")
            info_parts.append(f"Product ID: 0x{self.device_info.get('ProductId',0):03X}")
            info_parts.append(f"FW Version: 0x{self.device_info.get('FwVersion',0):02X}")
        info_parts.append(f"UID: {self.uid or 'N/A'}")
        info_line = " | ".join(info_parts)

        # Center text vertically inside footer box
        text_bbox = self.font_text.getbbox(info_line)
        text_height = text_bbox[3] - text_bbox[1]
        text_y = y_footer + (footer_height - text_height) // 2
        draw.text((padding + 8, text_y), info_line, fill=self.COLOR_VALUE, font=self.font_text)

        # ---- Footer Buttons ----
        btns = [
            {"text": "Shutdown", "callback": lambda: signal.raise_signal(signal.SIGINT), "color": (255, 99, 71)},
            {"text": "Select Device", "callback": self.return_to_fleet, "color": (252, 228, 119)},
        ]

        self.footer_btn_config = []

        # Start button placement from the right
        btn_x = self.SCREEN_WIDTH - padding - btn_width
        for btn in btns:
            btn_y = y_footer + (footer_height - btn_height) // 2
            btn_x1, btn_y1 = btn_x + btn_width, btn_y + btn_height

            # Draw button
            draw.rectangle([btn_x, btn_y, btn_x1, btn_y1], fill=btn["color"], outline=(200, 200, 200))

            # Center text inside button
            text_bbox = self.font_text.getbbox(btn["text"])
            text_height = text_bbox[3] - text_bbox[1]
            text_width = text_bbox[2] - text_bbox[0]
            text_x = btn_x + (btn_width - text_width) // 2
            text_y = btn_y + (btn_height - text_height) // 2
            draw.text((text_x, text_y), btn["text"], fill=(0, 0, 0), font=self.font_text)

            # Save button config for touch events
            self.footer_btn_config.append({
                "x0": btn_x,
                "y0": btn_y,
                "x1": btn_x1,
                "y1": btn_y1,
                "callback": btn["callback"],
                "text": btn["text"]
            })

            # Move left for next button
            btn_x -= (btn_width + btn_spacing)


        return img
