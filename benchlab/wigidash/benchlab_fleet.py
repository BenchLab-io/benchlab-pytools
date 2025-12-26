# benchlab_fleet.py

from PIL import Image, ImageDraw, ImageFont
import os
import time

from benchlab.wigidash.benchlab_ui import load_fonts, draw_header, draw_footer, bind_button, load_logo, UIButton, UITheme, BUTTON_DEFS
from benchlab.wigidash.benchlab_utils import display_image, get_logger, KeepAliveManager

logger = get_logger("BenchlabFleet")


class BenchlabFleetSelect:

    def __init__(self, wigidash, fleet_manager=None, wigi=None, on_exit=None):
        self.wigidash = wigidash
        self.wigi = wigi
        self.running = False
        self.selected_port = None
        self.manager = fleet_manager
        self.on_exit = on_exit
        self.last_tap_time = 0
        self.keepalive = KeepAliveManager(self.wigidash)

        # Fonts and logo from central UI
        self.ui_fonts = load_fonts()
        self.ui_logo = load_logo()

        # Build fleet cache
        self.fleet_cache = []
        if self.manager:
            for port, info in self.manager.benchlab_devices.items():
                self.fleet_cache.append({
                    "port": port,
                    "uid": info.get("uid", "?"),
                    "firmware": info.get("firmware", "?"),
                    "in_use": info.get("in_use", False)
                })
        self.fleet = self.fleet_cache.copy()

        # Footer buttons (centralized)
        self.footer_btns = [
            bind_button(UIButton.SHUTDOWN, self.manager.graceful_shutdown if self.manager else None)
        ]
        self.footer_hitboxes = []

        # Fleet button layout
        self.button_height = 60
        self.button_margin = 20
        self.header_height = 60
        self.start_y = self.header_height + 20

    # -------------------------------
    # Page Lifecycle
    # -------------------------------
    def start(self):
        logger.info("Starting FleetSelect page")
        self.running = True
        self.start_time = int(time.monotonic() * 1000)


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

        # Ignore touches immediately after page start
        if now - getattr(self, "start_time", now) < 500:
            return

        # Footer buttons
        for btn in self.footer_hitboxes:
            if btn["x0"] <= x <= btn["x1"] and btn["y0"] <= y <= btn["y1"]:
                logger.info(f"Footer button pressed: {btn['text']}")
                if btn.get("callback"):
                    btn["callback"]()
                return

        # Fleet selection buttons
        for idx, dev in enumerate(self.fleet):
            y0 = self.start_y + idx*(self.button_height+self.button_margin)
            y1 = y0 + self.button_height
            if 50 <= x <= 966 and y0 <= y <= y1:
                self.selected_port = dev["port"]
                logger.info(f"Fleet selection done on {self.selected_port}, opening Overview.")
                if self.manager and self.wigi:
                    self.manager.start_telemetry(self.selected_port, self.wigi)

                self.running = False
                break

    # -------------------------------
    # Rendering
    # -------------------------------
    def render_and_display(self):
        img = self.render()
        display_image(self.wigidash, img)

    def render(self):
        img = Image.new("RGB", (1016, 592), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        # ---- Header ----
        draw_header(draw, img, self.ui_fonts, "SELECT BENCHLAB DEVICE", self.ui_logo)

        # ---- Fleet Buttons ----
        for idx, dev in enumerate(self.fleet):
            y0 = self.start_y + idx*(self.button_height+self.button_margin)
            y1 = y0 + self.button_height
            draw.rectangle([50, y0, 966, y1], fill=(60, 60, 60), outline=(200, 200, 200), width=2)
            draw.text((60, y0+15),
                f"Port: {dev['port']} | UID: {dev['uid']}",
                fill=(255,255,255),
                font=self.ui_fonts["title"])

        # ---- Footer ----
        info_text = f"Available BENCHLABs: {len(self.fleet)}"
        self.footer_hitboxes = draw_footer(draw, self.ui_fonts, info_text, self.footer_btns)

        return img
