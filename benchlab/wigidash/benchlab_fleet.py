# benchlab_fleet.py

import os
import sys
import threading
import time
from PIL import Image, ImageDraw, ImageFont

from benchlab.core import serial_io
from benchlab.core.serial_io import open_serial_connection

from benchlab.wigidash.benchlab_utils import display_image, image_to_rgb565, get_logger, KeepAliveManager
from benchlab.wigidash.benchlab_telemetry import TelemetryHistory, telemetry_step


base_dir = os.path.dirname(os.path.abspath(__file__))
logger = get_logger("BenchlabFleet")


class BenchlabFleetSelect:
    """Fleet selection page, styled like graph/overview pages."""
    SCREEN_WIDTH = 1016
    SCREEN_HEIGHT = 592
    HEADER_HEIGHT = 60
    BUTTON_HEIGHT = 60
    BUTTON_MARGIN = 20
    START_Y = HEADER_HEIGHT + 20

    COLOR_BG = (41, 39, 38)
    COLOR_HEADER = (252, 228, 119)
    COLOR_BUTTON = (60, 60, 60)
    COLOR_BUTTON_OUTLINE = (200, 200, 200)
    COLOR_TEXT = (255, 255, 255)

    def __init__(self, wigidash, fleet=None, wigi=None, on_exit=None):
        self.wigidash = wigidash
        self.wigi = wigi
        self.running = False
        self.selected_port = None
        self.fleet = fleet if fleet is not None else serial_io.get_fleet_info()
        self.on_exit = on_exit
        self.keepalive = KeepAliveManager(self.wigidash)

        font_header_path = os.path.join(base_dir, "assets", "Inter-Bold.ttf")
        font_button_path = os.path.join(base_dir, "assets", "Inter.ttf")
        logo_path = os.path.join(base_dir, "assets", "benchlab.png")

        try:
            self.font_header = ImageFont.truetype(font_header_path, 30)
            self.font_button = ImageFont.truetype(font_button_path, 18)
        except Exception:
            self.font_header = self.font_button = ImageFont.load_default()
            logger.warning(f"Default font loaded due to missing assets.")

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
        logger.info("Starting FleetSelect page")
        self.running = True

    def stop(self):
        logger.info("Stopping FleetSelect page")
        self.running = False

        # Stop screen keepalive
        if self.keepalive:
            self.keepalive.stop()
            self.keepalive = None

        # Clear screen if desired
        if self.wigidash:
            try:
                self.wigidash.clear_page(0)
            except Exception as e:
                logger.warning(f"Error clearing fleet page display: {e}")

        # Call on_exit callback if defined
        if self.on_exit:
            try:
                self.on_exit()
            except Exception as e:
                logger.warning(f"Error in on_exit callback: {e}")

    # -------------------------------
    # Telemetry Handling
    # -------------------------------

    def start_telemetry(self):
        """Start the background telemetry thread, stoppable via self.telemetry_running."""
        if getattr(self, "telemetry_thread", None) and self.telemetry_thread.is_alive():
            return  # Already running

        self.telemetry_running = True

        def telemetry_loop():
            logger.info("Telemetry thread started.")
            while self.telemetry_running:
                try:
                    telemetry_step(self.wigi)
                except Exception as e:
                    logger.error(f"Telemetry thread error: {e}")
                time.sleep(0.25)

            logger.info("Telemetry thread stopped.")

        self.telemetry_thread = threading.Thread(target=telemetry_loop, daemon=True)
        self.telemetry_thread.start()


    def stop_telemetry(self):
        """Stop the background telemetry thread safely."""
        if getattr(self, "telemetry_thread", None):
            self.telemetry_running = False
            self.telemetry_thread.join(timeout=1)
            self.telemetry_thread = None
            logger.info("Telemetry stopped.")


    def check_touch(self, touch):
        if touch is None or getattr(touch, "Type", 0) == 0:
            return
        x, y = getattr(touch, "X", 0), getattr(touch, "Y", 0)
        now = int(time.monotonic() * 1000)

        for idx, dev in enumerate(self.fleet):
            y0 = self.START_Y + idx*(self.BUTTON_HEIGHT+self.BUTTON_MARGIN)
            y1 = y0 + self.BUTTON_HEIGHT
            if 50 <= x <= self.SCREEN_WIDTH-50 and y0 <= y <= y1:
                self.selected_port = dev["port"]
                self.running = False
                self.wigi.selected_com_port = self.selected_port
                logger.info("Selected Benchlab device on port %s", self.selected_port)
                break

        self.last_tap_time = now


    def render_and_display(self):
        img = self.render()
        display_image(self.wigidash, img)

    def handle_touch(self, touch):
        if touch is None or getattr(touch, "Type", 0) == 0:
            return None
        x, y = getattr(touch, "X", 0), getattr(touch, "Y", 0)

        for idx, dev in enumerate(self.fleet):
            y0 = self.START_Y + idx*(self.BUTTON_HEIGHT+self.BUTTON_MARGIN)
            y1 = y0 + self.BUTTON_HEIGHT
            if y0 <= y <= y1:
                self.wigi.selected_com_port = dev["port"]
                return "overview"  # signal launcher to switch page
        return None

    def render(self):
        img = Image.new("RGB", (self.SCREEN_WIDTH, self.SCREEN_HEIGHT), self.COLOR_BG)
        draw = ImageDraw.Draw(img)

        # Header
        draw.rectangle([0, 0, self.SCREEN_WIDTH, self.HEADER_HEIGHT], fill=self.COLOR_HEADER)

        # Logo left/right
        if self.logo:
            logo_y = (self.HEADER_HEIGHT - self.logo.height)//2
            img.paste(self.logo, (10, logo_y), self.logo)
            img.paste(self.logo, (self.SCREEN_WIDTH - self.logo.width - 10, logo_y), self.logo)

        # Center title
        header_text = "SELECT BENCHLAB DEVICE"
        bbox = draw.textbbox((0,0), header_text, font=self.font_header)
        title_x = (self.SCREEN_WIDTH - (bbox[2]-bbox[0]))//2
        title_y = (self.HEADER_HEIGHT - (bbox[3]-bbox[1]))//2
        draw.text((title_x, title_y), header_text, fill=(0,0,0), font=self.font_header)

        # Fleet buttons
        for idx, dev in enumerate(self.fleet):
            y0 = self.START_Y + idx*(self.BUTTON_HEIGHT+self.BUTTON_MARGIN)
            y1 = y0 + self.BUTTON_HEIGHT
            draw.rectangle([50, y0, self.SCREEN_WIDTH-50, y1],
                           fill=self.COLOR_BUTTON,
                           outline=self.COLOR_BUTTON_OUTLINE, width=2)
            draw.text((60, y0+15), f"Port: {dev['port']} | UID: {dev['uid']}",
                      fill=self.COLOR_TEXT, font=self.font_button)

        return img
