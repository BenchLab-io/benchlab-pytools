import os
import threading
import time
import types

from benchlab.core.datasource_manager import DataSourceManager
from benchlab.wigidash.benchlab_telemetry import (
    TelemetryHistory, telemetry_step, TelemetryContext
)
from benchlab.wigidash.benchlab_utils import get_logger
from benchlab.wigidash.wigidash_usb import scan_wigidash
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
        self.benchlab_devices = {}
        self.telemetry_histories = {}
        self.telemetry_contexts = {}
        self.shutting_down = False
        self.shutdown_event = threading.Event()
        self.shutdown_barrier = None
        self.datasource = datasource  # Already-connected DataSourceManager
        self._telemetry_lock = threading.Lock()

    # ----------------- BENCHLAB DEVICE MANAGEMENT ----------------- #
    def get_available_benchlabs(self, log_info=True):
        """Query the datasource for available Benchlab devices."""
        devices = []
        query_succeeded = False
        try:
            raw = self.datasource.list_devices()
            if isinstance(raw, dict):
                devices = [{"port": info.get("port", "?"), "uid": uid,
                            "firmware": info.get("firmware", "?")}
                           for uid, info in raw.items()]
            else:
                devices = list(raw)
            query_succeeded = True
        except Exception as e:
            logger.warning(f"list_devices failed: {e}")

        for d in devices:
            port = d["port"]
            uid = d["uid"]
            fw = d.get("firmware", "?")
            if port not in self.benchlab_devices:
                self.benchlab_devices[port] = {
                    "uid": uid, "firmware": fw, "in_use": False}
            else:
                self.benchlab_devices[port]["uid"] = uid
                self.benchlab_devices[port]["firmware"] = fw

        # Prune ports no longer reported by the data source, so a disconnected
        # device doesn't linger forever with stale UID/firmware/in_use state.
        if query_succeeded:
            current_ports = {d["port"] for d in devices}
            for stale_port in set(self.benchlab_devices) - current_ports:
                del self.benchlab_devices[stale_port]
                self.telemetry_contexts.pop(stale_port, None)

        all_devices = [
            {"port": port, "uid": info["uid"], "in_use": info["in_use"]}
            for port, info in self.benchlab_devices.items()
        ]

        if log_info:
            count = len(all_devices)
            if count:
                logger.info(
                    f"{count} available Benchlab device{
                        's' if count > 1 else ''}:")
                for dev in all_devices:
                    logger.info(f"  {dev['port']}: UID {dev['uid']}")
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

        with self._telemetry_lock:
            # ------------------------------------------------
            # CASE 1: Telemetry already running for this port
            # ------------------------------------------------
            if port in self.telemetry_contexts:
                ctx = self.telemetry_contexts[port]

                logger.info(
                    f"Selected device {port} is already in use. "
                    "Using existing telemetry.")

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
            history = self.telemetry_histories.setdefault(
                uid, TelemetryHistory())

            # Get device info from datasource
            device_info = {}
            try:
                self.datasource.select_device(uid)
                snap = self.datasource.snapshot()
                device_info = snap.get("device_info") or snap.get(
                    "all_devices", {}).get(uid, {})
            except Exception as e:
                logger.warning(f"Could not get device info for {uid}: {e}")

            ctx = TelemetryContext(
                port=port,
                ser=None,
                device_info=device_info,
                uid=uid,
                history=history,
            )
            ctx.sessions = [session]
            self.telemetry_contexts[port] = ctx

            session.ser = None
            session.device_info = device_info
            session.uid = uid
            session.telemetry_history = history
            session.history = history
            session.selected_port = port

            def telemetry_loop():
                while not self.shutdown_event.is_set():
                    try:
                        self.datasource.select_device(uid)
                        snap = self.datasource.snapshot()
                        data = (snap.get("sensor_data")
                                or snap.get("all_telemetry", {}).get(uid)
                                or {})
                        if data:
                            telemetry_step(
                                ctx, device_info=device_info,
                                sensor_struct=data)
                    except Exception as e:
                        logger.warning(f"Telemetry error on {port}: {e}")
                    time.sleep(0.1)

            threading.Thread(target=telemetry_loop, daemon=True).start()

    # ----------------- SESSION MANAGEMENT ----------------- #

    def detect_and_start_sessions(self):
        logger.info("Looking for WigiDash devices ...")
        time.sleep(1)

        usb_devices = scan_wigidash(self.vendor_id, self.product_id)
        if not usb_devices:
            logger.warning("No WigiDash devices detected.")
            return

        used_serials = {
            s.usb_device.serial for s in self.sessions if s.usb_device.serial}

        new_devices = []
        for usb in usb_devices:
            if not usb.serial:
                try:
                    usb.serial = usb.util.get_string(
                        usb.dev, usb.dev.iSerialNumber)
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
            threading.Thread(
                target=session.shutdown_session,
                daemon=True).start()

        # If barrier exists, wait for synchronized splash
        if self.shutdown_barrier:
            try:
                self.shutdown_barrier.wait()
            except threading.BrokenBarrierError:
                logger.warning("Shutdown barrier broken in manager.")
            self.shutdown_barrier = None

        # Wait for all sessions to signal cleanup_done
        for session in active_sessions:
            # blocks until each session finishes cleanup
            session.cleanup_done.wait()

        # Clean up manager
        self.shutdown_manager()
        logger.info("Graceful shutdown completed.")


def main(args=None):
    """Entry point. Accepts standard benchlab args namespace."""
    if args is None:
        args = types.SimpleNamespace(
            source=os.environ.get(
                "BENCHLAB_DATA_SOURCE",
                "direct"),
            api_url=os.environ.get(
                "BENCHLAB_API_URL",
                "http://127.0.0.1:8000"),
            mqtt_broker=os.environ.get(
                "MQTT_BROKER",
                "localhost"),
            mqtt_port=int(
                os.environ.get(
                    "MQTT_PORT",
                    "1883")),
            interval=1.0,
        )

    source = args.source
    ds_kwargs = {}
    if source in ("fastapi", "fastapi_custom"):
        ds_kwargs["base_url"] = args.api_url
        logger.info(f"Using FastAPI datasource: {args.api_url}")
    elif source == "mqtt":
        ds_kwargs["broker"] = args.mqtt_broker
        ds_kwargs["port"] = args.mqtt_port
        logger.info(
            f"Using MQTT datasource: {
                args.mqtt_broker}:{
                args.mqtt_port}")
    else:
        logger.info("Using direct datasource")

    datasource = DataSourceManager(source_type=source, **ds_kwargs)
    if not datasource.connect():
        logger.error(f"Failed to connect to {source} datasource")
        return

    manager = WigidashManager(datasource=datasource)
    manager.detect_and_start_sessions()

    try:
        while True:
            if manager.shutting_down:
                break
            if manager.sessions and all(
                    s.cleanup_done.is_set() for s in manager.sessions):
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Initiating graceful shutdown")
        manager.graceful_shutdown()
    finally:
        datasource.disconnect()


if __name__ == "__main__":
    main()
