from PIL import Image, ImageDraw, ImageFont

import os
import sys
import threading
import time

from benchlab_pycore.core import read_sensors, read_device, read_uid
from benchlab_pycore.core.serial_io import open_serial_connection, get_fleet_info
from benchlab.core.datasource import create_datasource, DataSource

from benchlab.wigidash.benchlab_telemetry import TelemetryHistory, telemetry_step, TelemetryContext
from benchlab.wigidash.benchlab_utils import get_logger, display_image

from benchlab.wigidash.wigidash_usb import scan_wigidash, USBDevice
from benchlab.wigidash.wigidash_session import BenchlabWigiSession

logger = get_logger("WigidashManager")


class WigidashManager:
    """
    Manages multiple Wigidash sessions and their assigned Benchlab devices.
    """

    def __init__(self, vendor_id=0x28DA, product_id=0xEF01, datasource=None):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.sessions = []
        self.benchlab_devices = {}  # port -> {'uid': str, 'in_use': bool, 'firmware': str}
        self.telemetry_histories = {}  # uid -> TelemetryHistory
        self.telemetry_contexts = {}
        self.shutting_down = False
        self.shutdown_event = threading.Event()
        self.shutdown_barrier = None
        self.datasource = datasource  # Optional DataSource instance
        # Connect the datasource if provided
        if self.datasource is not None:
            try:
                if not self.datasource.connect():
                    logger.warning(f"Failed to connect to {self.datasource.source_type} datasource")
                else:
                    logger.info(f"Connected to {self.datasource.source_type} datasource")
            except Exception as e:
                logger.warning(f"Error connecting to datasource: {e}")

    # ----------------- BENCHLAB DEVICE MANAGEMENT ----------------- #
    def get_available_benchlabs(self, log_info=True):
        """
        Initial scan: get fleet info from DataSource if available, otherwise use pycore.
        After this, fleet pages only read from the cache; no serial access.
        """
        devices = []
        if self.datasource is not None:
            try:
                devices = self.datasource.list_devices()
            except Exception as e:
                logger.warning(f"DataSource list_devices failed: {e}")
                devices = []
        else:
            # Only use direct serial when no datasource is configured
            try:
                devices = get_fleet_info()
            except Exception as e:
                logger.warning(f"Failed to get fleet info: {e}")
                devices = []

        for d in devices:
            port = d['port']
            uid = d['uid']
            fw = d.get('firmware', '?')

            if port not in self.benchlab_devices:
                self.benchlab_devices[port] = {
                    'uid': uid,
                    'firmware': fw,
                    'in_use': False
                }
            else:
                # update UID/firmware if changed
                self.benchlab_devices[port]['uid'] = uid
                self.benchlab_devices[port]['firmware'] = fw

        # Return only devices not currently in use
        all_devices = [
            {"port": port, "uid": info['uid'], "in_use": info['in_use']}
            for port, info in self.benchlab_devices.items()
        ]

        if log_info:
            count = len(all_devices)
            if count:
                logger.info(f"{count} available Benchlab device{'s' if count > 1 else ''}:")
                for dev in all_devices:
                    logger.info(f"{dev['port']}: UID {dev['uid']}")
            else:
                logger.info("No Benchlab devices detected.")

        return all_devices

    # ----------------- TELEMETRY MANAGEMENT ----------------- #
    def release_port(self, port):
        if port in self.benchlab_devices:
            self.benchlab_devices[port]["in_use"] = False

    def start_telemetry(self, port, session: BenchlabWigiSession):
        if port not in self.benchlab_devices:
            logger.warning(f"Cannot start telemetry: unknown port {port}")
            return

        # ------------------------------------------------
        # CASE 1: Telemetry already running for this port
        # ------------------------------------------------
        if port in self.telemetry_contexts:
            ctx = self.telemetry_contexts[port]

            logger.info(
                f"Selected device {port} is already in use. Using existing telemetry."
            )

            session.ser = ctx.ser
            session.device_info = ctx.device_info
            session.uid = ctx.uid
            session.telemetry_history = ctx.history
            session.history = ctx.history
            session.selected_port = port
            session.telemetry_context = ctx

            ctx.sessions.append(session)
            return

        # ------------------------------------------------
        # CASE 2: First session → start telemetry
        # ------------------------------------------------
        self.benchlab_devices[port]["in_use"] = True
        uid = self.benchlab_devices[port]["uid"]

        history = self.telemetry_histories.setdefault(uid, TelemetryHistory())

        # Determine device_info: use DataSource if available, else direct
        device_info = {}
        ser = None

        if self.datasource is not None:
            info = self.datasource.get_device_info(uid)
            if info:
                device_info = info
            uid_from_info = info.get("uid", uid) if info else uid
            uid = uid_from_info
            logger.info(f"DataSource provides info for UID={uid}")
        else:
            # Fallback: direct serial
            try:
                ser = open_serial_connection(port)
                logger.info(f"Opened serial port {port} for telemetry")
            except Exception as e:
                logger.warning(f"Error opening serial for {port}: {e}")
                return

            try:
                device_info = read_device(ser)
                uid = device_info.get("UID", uid)
                logger.info(f"Device info read for {port}: UID={uid}")
            except Exception as e:
                logger.warning(f"Failed to read device info from {port}: {e}")
                device_info = {}

        # Create telemetry context
        ctx = TelemetryContext(
            port=port,
            ser=ser,  # None when using DataSource
            device_info=device_info,
            uid=uid,
            history=history,
        )
        ctx.sessions = [session]

        self.telemetry_contexts[port] = ctx

        # Inject into first session
        session.ser = ser
        session.device_info = device_info
        session.uid = uid
        session.telemetry_history = history
        session.history = history
        session.selected_port = port

        # Telemetry thread (single owner)
        def telemetry_loop():
            while not self.shutdown_event.is_set():
                try:
                    if self.datasource is not None:
                        # DataSource path: get telemetry dict directly
                        data = self.datasource.get_telemetry(uid)
                        if data:
                            # Convert telemetry dict to sensor_struct format
                            # telemetry_step expects a sensor_struct, but our
                            # DataSource returns a dict - it's already translated
                            telemetry_step(ctx, device_info=device_info, sensor_struct=data)
                    else:
                        # Direct serial fallback
                        sensor_struct = read_sensors(ser)
                        if sensor_struct:
                            telemetry_step(ctx, device_info=device_info, sensor_struct=sensor_struct)
                except Exception as e:
                    logger.warning(f"Telemetry error on {port}: {e}")
                time.sleep(0.1)

            # Cleanup serial if we opened it
            if ser is not None:
                try:
                    ser.close()
                    logger.info(f"Closed serial port {port}")
                except Exception:
                    pass

        threading.Thread(target=telemetry_loop, daemon=True).start()


    # ----------------- SESSION MANAGEMENT ----------------- #
    def detect_and_start_sessions(self):
        logger.info("Looking for WigiDash devices ...")
        time.sleep(1)

        usb_devices = scan_wigidash(self.vendor_id, self.product_id)
        if not usb_devices:
            logger.warning("No WigiDash devices detected.")
            return

        used_serials = {s.usb_device.serial for s in self.sessions if s.usb_device.serial}

        new_devices = []
        for usb in usb_devices:
            if not usb.serial:
                try:
                    usb.serial = usb.util.get_string(usb.dev, usb.dev.iSerialNumber)
                except Exception as e:
                    logger.warning(f"Failed to read USB serial: {e}")
                    continue
            if usb.serial in used_serials:
                continue
            new_devices.append(usb)

        if not new_devices:
            logger.info("No new WigiDash devices.")
            return

        logger.info("Scanning Benchlab devices ...")
        time.sleep(1)
        self.get_available_benchlabs()  # only builds cache, closes ports

        logger.info("Starting Wigi sessions ...")
        for usb in new_devices:
            session = BenchlabWigiSession(
                usb_device=usb,
                telemetry_history=None,
                manager=self,
            )
            try:
                threading.Thread(target=session.run, daemon=True).start()
                self.sessions.append(session)
                used_serials.add(usb.serial)
                logger.info(f"Started Wigidash session for {usb.serial}")
            except Exception as e:
                logger.error(f"Failed to start session for {usb.serial}: {e}")

    def shutdown_manager(self):
        """Close USB and COM ports safely after all sessions done."""
        logger.info("Manager Shutdown Initiated.")

        # Close COM ports
        for port in self.benchlab_devices:
            self.release_port(port)

        self.sessions.clear()
        logger.info("Manager shutdown completed")


    def graceful_shutdown(self):
        """Graceful shutdown: synchronize splash, then cleanup."""
        if getattr(self, "shutting_down", False):
            logger.info("Graceful shutdown already in progress.")
            return

        logger.info("Initiating graceful shutdown...")
        self.shutting_down = True
        self.shutdown_event.set()

        # Disconnect DataSource if we created one
        if self.datasource is not None:
            try:
                self.datasource.disconnect()
                logger.info("DataSource disconnected")
            except Exception as e:
                logger.warning(f"Error disconnecting DataSource: {e}")

        active_sessions = [s for s in self.sessions if s.app_running]

        # Only create a barrier if we want splash synchronization
        if active_sessions:
            self.shutdown_barrier = threading.Barrier(len(active_sessions) + 1)

        # Trigger shutdown in all sessions
        for session in active_sessions:
            threading.Thread(target=session.shutdown_session, daemon=True).start()

        # If barrier exists, wait for synchronized splash
        if self.shutdown_barrier:
            try:
                self.shutdown_barrier.wait()
            except threading.BrokenBarrierError:
                logger.warning("Shutdown barrier broken in manager.")
            self.shutdown_barrier = None

        # Wait for all sessions to signal cleanup_done
        for session in active_sessions:
            session.cleanup_done.wait()  # blocks until each session finishes cleanup

        # Clean up manager
        self.shutdown_manager()
        logger.info("Graceful shutdown completed.")


def main():
    """Entry point — auto-selects DataSource from BENCHLAB_DATA_SOURCE env var."""
    datasource = None
    source_type = os.environ.get("BENCHLAB_DATA_SOURCE", "direct")

    if source_type == "fastapi":
        from benchlab.core.datasource import FastAPIDataSource
        base_url = os.environ.get("BENCHLAB_API_URL", "http://127.0.0.1:8000")
        datasource = FastAPIDataSource(base_url=base_url)
        logger.info(f"Using FastAPI data source: {base_url}")
    elif source_type == "mqtt":
        from benchlab.core.datasource import MQTTDataSource
        broker = os.environ.get("MQTT_BROKER", "localhost")
        port = int(os.environ.get("MQTT_PORT", "1883"))
        datasource = MQTTDataSource(broker=broker, port=port)
        logger.info(f"Using MQTT data source: {broker}:{port}")
    else:
        logger.info("Using direct serial data source")

    manager = WigidashManager(datasource=datasource)
    manager.detect_and_start_sessions()

    try:
        while True:
            if manager.shutting_down:
                break
            if manager.sessions and all(s.cleanup_done.is_set() for s in manager.sessions):
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Initiating graceful shutdown")
        manager.graceful_shutdown()
        sys.exit(0)

if __name__ == "__main__":
    main()
