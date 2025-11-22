# wigidash_launcher.py

from PIL import Image
import os
import threading
import time

from benchlab.core.serial_io import open_serial_connection, read_uid

from benchlab.wigidash.benchlab_fleet import BenchlabFleetSelect
from benchlab.wigidash.benchlab_graph import BenchlabGraph
from benchlab.wigidash.benchlab_overview import BenchlabOverview
from benchlab.wigidash.benchlab_telemetry import telemetry_step, TelemetryHistory
from benchlab.wigidash.benchlab_utils import display_image, KeepAliveManager, get_logger, shutdown_wigidash

from benchlab.wigidash.wigidash_widget import WidgetConfig
from benchlab.wigidash.wigidash_device import WigidashDevice
from benchlab.wigidash.wigidash_usb import USBDevice

logger = get_logger("WigidashLauncher")


class WigiSession:
    def __init__(self):
        self.ser = None
        self.history = None
        self.selected_com_port = None
        self.device_info = None
        self.uid = None
        self.sensor_data = {}
        self.fleet = []

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


    def is_connected(self):
        return self.ser is not None


class BenchlabWigi:
    SCREEN_WIDTH = 1016
    SCREEN_HEIGHT = 592
    SPLASH_TIME = 3.0 
    SCREEN_KEEPALIVE_INTERVAL = 5.0
    SCREEN_IDLE_TIMEOUT = 60.0


    def __init__(self, vendor_id=0x28DA, product_id=0xEF01):
        self.usb_dev = USBDevice(vendor_id, product_id)
        self.wigidash = None
        self.keepalive_manager = None
        self.app_running = True
        self.wigi = WigiSession()
        self.graph_page = None
        self.graph_metrics = []


    def connect_wigidash(self):
        """Initialize USB and WigiDash."""
        try:
            self.usb_dev.connect()
            self.wigidash = WigidashDevice(self.usb_dev)
            self.wigidash.init_device()
            self.wigidash.clear_page(0)
            self.wigidash.change_page(0)

            widget = WidgetConfig.create_fullscreen()
            self.wigidash.add_widget(widget)

            # Start keepalive after wigidash is ready
            self.keepalive_manager = KeepAliveManager(self.wigidash, interval=self.SCREEN_KEEPALIVE_INTERVAL)
            self.keepalive_manager.start()

            logger.info("WigiDash initialized successfully.")
            return True
        except Exception as e:
            logger.error("Failed to initialize WigiDash: %s", e)
            return False


    def show_splash(self):
        """Show splash for 3 seconds"""

        base_dir = os.path.dirname(os.path.abspath(__file__))
        assets_path = os.path.join(base_dir, "assets", "benchlab.png")

        img = Image.new('RGB', (self.SCREEN_WIDTH, self.SCREEN_HEIGHT), color=(41, 39, 38))
        try:
            logo = Image.open(assets_path).convert('RGBA')
            logo.thumbnail((500, 500), Image.Resampling.LANCZOS)
            x = (self.SCREEN_WIDTH - logo.width)//2
            y = (self.SCREEN_HEIGHT - logo.height)//2
            img.paste(logo, (x, y), logo)
        except Exception as e:
            logger.warning("Failed to load splash logo: %s", e)

        display_image(self.wigidash, img)
        time.sleep(self.SPLASH_TIME)
        logger.info("Splash screen displayed.")


    def open_graph_page(self, metrics):
        self.graph_metrics = metrics
        self.next_page = "graph"


    def run(self):
        """Initialize WigiDash, show splash, and launch fleet selection."""
        if not self.connect_wigidash():
            logger.error("Cannot proceed without WigiDash connection.")
            return

        # --- Show splash screen ---
        self.show_splash()
        logger.info("Launcher finished.")

        # --- Launch fleet selection page ---
        logger.info("Launching Benchlab Fleet selection...")
        self.next_page = "fleet"
        fleet_page = None
        overview_page = None
        telemetry_thread = None
        telemetry_running = False

        def telemetry_loop(wigi):
            logger.info("Telemetry thread started.")
            while telemetry_running:
                try:
                    telemetry_step(wigi)
                    if self.keepalive_manager:
                        self.keepalive_manager.mark_active()
                except Exception as e:
                    logger.error(f"Telemetry thread error: {e}")
                time.sleep(0.25)
            logger.info("Telemetry thread stopped.")

        while self.app_running:
            touch, _, _ = self.wigidash.get_click_info()

            # --- Fleet page ---
            if self.next_page == "fleet":
                if fleet_page is None:
                    fleet_page = BenchlabFleetSelect(self.wigidash, wigi=self.wigi)
                    fleet_page.start()

                fleet_page.check_touch(touch)
                fleet_page.render_and_display()

                if not fleet_page.running and fleet_page.selected_port:
                    logger.info(f"Selected device {fleet_page.selected_port}, launching telemetry and Overview")

                    # Open serial and initialize history
                    self.wigi.ser = open_serial_connection(fleet_page.selected_port)
                    try:
                        uid_from_device = read_uid(self.wigi.ser)
                        if uid_from_device:
                            self.wigi.uid = uid_from_device
                    except Exception as e:
                        logger.warning("Failed to read UID: %s", e)
                    self.wigi.history = TelemetryHistory()

                    # Start telemetry thread once
                    if not telemetry_running:
                        telemetry_running = True
                        telemetry_thread = threading.Thread(
                            target=telemetry_loop, args=(self.wigi,), daemon=True
                        )
                        telemetry_thread.start()

                    # Launch Overview
                    overview_page = BenchlabOverview(self.wigidash, wigi=self.wigi)
                    overview_page.start()
                    self.next_page = "overview"

            # --- Overview page ---
            elif self.next_page == "overview":
                if overview_page:
                    overview_page.check_touch(touch)  # pass touch here
                    overview_page.render_and_display()

                    # If Overview stopped (footer pressed), go back to fleet
                    if not overview_page.running:
                        # Capture any requested graph metrics BEFORE stopping the page
                        requested_metrics = overview_page.requested_graph_metrics
                        
                        # Stop the page
                        overview_page.stop()
                        overview_page = None

                        # Switch to graph if Overview requested it
                        if requested_metrics:
                            self.graph_metrics = requested_metrics
                            self.next_page = "graph"
                            continue

                        # --- Stop telemetry cleanly ---
                        telemetry_running = False
                        if telemetry_thread:
                            telemetry_thread.join(timeout=1)
                            telemetry_thread = None

                        # --- Close COM port ---
                        if self.wigi.ser:
                            try:
                                self.wigi.ser.close()
                            except Exception as e:
                                logger.warning(f"Failed to close COM port: {e}")
                            self.wigi.ser = None

                        # --- Prepare to recreate FleetSelect ---
                        fleet_page = None
                        self.next_page = "fleet"

            # --- Graph page ---
            elif self.next_page == "graph":
                if not hasattr(self, "graph_page") or self.graph_page is None:
                    
                    # Create graph page with metrics requested by Overview
                    self.graph_page = BenchlabGraph(self.wigidash, self.wigi, getattr(self, "graph_metrics", []))
                    self.graph_page.start()
                
                # Get touch info
                self.graph_page.check_touch(touch)
                self.graph_page.render_and_display()
                
                # If graph page signals exit
                if not self.graph_page.running:
                    self.graph_page.stop()
                    self.graph_page = None
                    
                    # Return to Overview automatically
                    overview_page = BenchlabOverview(self.wigidash, wigi=self.wigi)
                    overview_page.start()
                    self.next_page = "overview"

            time.sleep(0.05)

        # --- Cleanup on exit ---
        if fleet_page:
            fleet_page.stop()
        if overview_page:
            overview_page.stop()
        if graph_page:
            graph_page.stop()
        telemetry_running = False
        if telemetry_thread:
            telemetry_thread.join(timeout=1)
        logger.info("Launcher exiting.")


def run_wigidash_main():
    app = BenchlabWigi()
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Ctrl+C received, shutting down")
        shutdown_wigidash(app)


if __name__ == "__main__":
    run_wigidash_main()