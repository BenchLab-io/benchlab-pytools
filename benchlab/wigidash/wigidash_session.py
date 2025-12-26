# wigidash_session.py

from PIL import Image, ImageDraw, ImageFont

import os
import time
import threading

from benchlab.wigidash.benchlab_fleet import BenchlabFleetSelect
from benchlab.wigidash.benchlab_overview import BenchlabOverview
from benchlab.wigidash.benchlab_graph import BenchlabGraph
from benchlab.wigidash.benchlab_ui import load_fonts, draw_header, draw_footer, bind_button, load_logo, UIButton, UITheme, BUTTON_DEFS
from benchlab.wigidash.benchlab_utils import display_image, get_logger, KeepAliveManager

from benchlab.wigidash.wigidash_device import WigidashDevice
from benchlab.wigidash.wigidash_widget import WidgetConfig


logger = get_logger("WigidashSession")


class BenchlabWigiSession:
    """
    UI session for a single Wigidash device.
    """

    SCREEN_WIDTH = UITheme.SCREEN_WIDTH
    SCREEN_HEIGHT = UITheme.SCREEN_HEIGHT
    SPLASH_TIME = 3.0
    SCREEN_KEEPALIVE_INTERVAL = 5.0

    def __init__(self, usb_device, telemetry_history=None, manager=None):
        self.usb_device = usb_device
        self.telemetry_history = telemetry_history
        self.history = telemetry_history
        self.telemetry_context = None
        self.manager = manager
        self.shutdown_event = threading.Event()

        self.device_info = None
        self.ser = None
        self.uid = None
        self.selected_port = None
        self.sensor_data = {}

        self.wigidash = None
        self.keepalive_manager = None
        self.app_running = True
        self.cleanup_done = threading.Event()

        self.fleet_page = None
        self.overview_page = None
        self.graph_page = None
        self.graph_metrics = []

        self.sensor_units = {
            # --- High-level power ---
            "SYS_Power": "W",
            "CPU_Power": "W",
            "GPU_Power": "W",
            "MB_Power": "W",

            # --- EPS Rails ---
            "EPS1_Voltage": "V",
            "EPS1_Current": "A",
            "EPS1_Power":   "W",
            "EPS2_Voltage": "V",
            "EPS2_Current": "A",
            "EPS2_Power":   "W",

            # --- ATX Rails ---
            "12V_Voltage": "V",
            "12V_Current": "A",
            "12V_Power":   "W",
            "5V_Voltage":  "V",
            "5V_Current":  "A",
            "5V_Power":    "W",
            "5VSB_Voltage": "V",
            "5VSB_Current": "A",
            "5VSB_Power":   "W",
            "3.3V_Voltage": "V",
            "3.3V_Current": "A",
            "3.3V_Power":   "W",

            # --- PCIe Rails ---
            "PCIE8_1_Voltage": "V",
            "PCIE8_1_Current": "A",
            "PCIE8_1_Power":   "W",
            "PCIE8_2_Voltage": "V",
            "PCIE8_2_Current": "A",
            "PCIE8_2_Power":   "W",
            "PCIE8_3_Voltage": "V",
            "PCIE8_3_Current": "A",
            "PCIE8_3_Power":   "W",
            "HPWR1_Voltage": "V",
            "HPWR1_Current": "A",
            "HPWR1_Power":   "W",
            "HPWR2_Voltage": "V",
            "HPWR2_Current": "A",
            "HPWR2_Power":   "W",

            # --- VIN ---
            # VIN_0 .. VIN_(SENSOR_VIN_NUM-1)
            **{f"VIN_{i}": "V" for i in range(16)},   # safe default—adjust if SENSOR_VIN_NUM changes

            # --- Other voltages ---
            "Vdd": "V",
            "Vref": "V",

            # --- Temps ---
            "Chip_Temp": "°C",
            "Ambient_Temp": "°C",
            "Humidity": "%",

            # Temp sensors
            **{f"Temp_Sensor_{i+1}": "°C" for i in range(16)},

            # --- Fans ---
            **{f"Fan{i+1}_Duty": "%" for i in range(16)},
            **{f"Fan{i+1}_RPM": "RPM" for i in range(16)},
            **{f"Fan{i+1}_Status": "" for i in range(16)},  # logical flag, unitless

            "FanExtDuty": "%"
        }

    # ----------------- INIT ----------------- #
    def connect_wigidash(self):
        """Initialize the Wigidash device and UI."""
        logger.info(f"Initializing Wigidash device {self.usb_device.serial} ...")
        try:
            self.usb_device.connect()
            self.wigidash = WigidashDevice(self.usb_device)
            self.wigidash.init_device()
            self.wigidash.clear_page(0)
            self.wigidash.change_page(0)

            widget = WidgetConfig.create_fullscreen()
            self.wigidash.add_widget(widget)

            self.keepalive_manager = KeepAliveManager(self.wigidash, interval=self.SCREEN_KEEPALIVE_INTERVAL)
            self.keepalive_manager.start()

            logger.info(f"Wigidash device {self.usb_device.serial} initialized successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Wigidash {self.usb_device.serial}: {e}")
            return False


    # ----------------- SPLASH ----------------- #
    def show_splash(self):
        """Display splash screen with logo."""
        img = Image.new('RGB', (self.SCREEN_WIDTH, self.SCREEN_HEIGHT), color=(41, 39, 38))
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            assets_path = os.path.join(base_dir, "assets", "benchlab.png")
            logo = Image.open(assets_path).convert('RGBA')
            logo.thumbnail((500, 500), Image.Resampling.LANCZOS)
            x = (self.SCREEN_WIDTH - logo.width) // 2
            y = (self.SCREEN_HEIGHT - logo.height) // 2
            img.paste(logo, (x, y), logo)
        except Exception as e:
            logger.warning(f"Failed to load splash logo: {e}")

        display_image(self.wigidash, img)
        logger.info(f"Splash screen displayed on {self.usb_device.serial}.")
        time.sleep(self.SPLASH_TIME)


    # ----------------- HELPER ----------------- #
    def create_overview_page(self):
        ov = BenchlabOverview(
            self.wigidash,
            wigi=self,
            telemetry_history=self.telemetry_history,
            telemetry_context=self.telemetry_context,
            manager=self.manager
        )
        ov.start()
        return ov


    # ----------------- MAIN LOOP ----------------- #
    def run(self):
        """Run UI loop for Wigidash pages."""
        logger.info(f"Session {self.usb_device.serial} started with manager={self.manager}")
        if not self.connect_wigidash():
            return

        self.show_splash()
        self.next_page = "fleet"

        while self.app_running and not self.shutdown_event.is_set():
            try:
                touch, _, _ = self.wigidash.get_click_info()
            except Exception:
                touch = None

            if not self.app_running or self.shutdown_event.is_set():
                break 

            # --- Fleet Page ---
            if self.next_page == "fleet":
                if self.fleet_page is None:
                    self.fleet_page = BenchlabFleetSelect(
                        self.wigidash,
                        fleet_manager=self.manager,
                        wigi=self
                    )
                    self.fleet_page.start()
                    logger.info(f"Fleet page started for {self.usb_device.serial}.")

                self.fleet_page.check_touch(touch)
                self.fleet_page.render_and_display()

                if not self.fleet_page.running and self.fleet_page.selected_port:
                    # Copy the selected port from fleet page into the session
                    self.selected_port = self.fleet_page.selected_port

                    # Fetch the telemetry context for that port
                    if self.manager:
                        self.telemetry_context = self.manager.telemetry_contexts.get(self.selected_port)

                    # Now create the overview page with a valid context
                    self.overview_page = BenchlabOverview(
                        self.wigidash,
                        wigi=self,
                        telemetry_history=self.telemetry_history,
                        telemetry_context=self.telemetry_context,
                        manager=self.manager or self
                    )
                    self.overview_page.start()
                    self.next_page = "overview"
                    self.fleet_page = None
                    logger.info(f"Fleet selection done on {self.usb_device.serial}, opening Overview.")

            # --- Overview Page ---
            elif self.next_page == "overview":
                if self.overview_page:
                    self.overview_page.check_touch(touch)
                    self.overview_page.render_and_display()

                    if not self.overview_page.running:
                        requested_metrics = getattr(self.overview_page, "requested_graph_metrics", [])
                        self.overview_page = None

                        if requested_metrics:
                            self.graph_metrics = requested_metrics
                            self.next_page = "graph"
                            logger.info(f"Overview requested graph metrics: {self.graph_metrics}")
                        else:
                            self.next_page = "fleet"

            # --- Graph Page ---
            elif self.next_page == "graph":
                if self.graph_page is None:
                    self.graph_page = BenchlabGraph(
                        self.wigidash,
                        self, 
                        metrics=self.graph_metrics,
                        telemetry_history=self.telemetry_history,
                        telemetry_context=self.telemetry_context,
                        manager=self.manager or self
                    )
                    self.graph_page.start()
                    logger.info(f"Graph page started for {self.usb_device.serial} with metrics {self.graph_metrics}.")

                self.graph_page.check_touch(touch)
                self.graph_page.render_and_display()

                if not self.graph_page.running:
                    self.graph_page = None
                    self.next_page = "overview"
                    logger.info(f"Graph page exited for {self.usb_device.serial}, returning to Overview.")

            time.sleep(0.05)


    # ----------------- STOP / CLEANUP ----------------- #
    def cleanup(self):
        """Stop all pages and keepalive manager."""
        if self.keepalive_manager:
            self.keepalive_manager.stop()
        if self.wigidash:
            try:
                self.wigidash.clear_page(0)
            except Exception:
                pass
        logger.info(f"Wigidash session {self.usb_device.serial} cleaned up.")


    def shutdown_session(self):
        """Show synchronized shutdown splash, then stop pages/keepalive."""
        logger.info(f"Stopping Wigidash session {self.usb_device.serial}")
        self.app_running = False
        self.shutdown_event.set()

        # Wait at barrier if it exists
        barrier = getattr(self.manager, "shutdown_barrier", None)
        if barrier:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                logger.warning(f"Shutdown barrier broken for {self.usb_device.serial}")

        # Show splash
        if self.wigidash:
            try:
                self.show_splash()
            except Exception as e:
                logger.warning(f"Failed to show shutdown splash: {e}")

        self.cleanup()
        self.cleanup_done.set()

