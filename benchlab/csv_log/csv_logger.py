"""
CSV Fleet Logger for BENCHLAB
"""

import serial
import serial.tools.list_ports
import csv
import threading
import time
from datetime import datetime
from benchlab.core.serial_io import read_sensors, read_uid, read_device, get_benchlab_ports
from benchlab.core.sensor_translation import translate_sensor_struct

logging_active = False

def discover_fleet_devices():
    """Scan BENCHLAB devices and return a list with UID + FW."""
    devices = []
    for port_info in get_benchlab_ports():
        port = port_info["port"]
        try:
            ser = serial.Serial(port, baudrate=115200, timeout=1)
            uid = read_uid(ser)
            fw = read_device(ser).get("FwVersion", "?") if read_device(ser) else "?"
            ser.close()
            devices.append({"port": port, "uid": uid, "fw": fw})
            print(f"[OK] Found Benchlab on {port}, UID={uid}")
        except Exception as e:
            print(f"[WARN] Could not query {port}: {e}")
    return devices


def sensor_logger_fleet(device_ser_map, interval=1.0):
    """Log multiple devices to individual CSVs with unified fleet logic."""
    global logging_active
    logging_active = True
    writers = {}

    # open one CSV file per device and write header
    for uid, ser in device_ser_map.items():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"log_{ts}_{uid}.csv"
        try:
            f = open(filename, "w", newline="")
            data = translate_sensor_struct(read_sensors(ser))
            header = ["Timestamp"] + list(data.keys())
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writers[uid] = (f, writer)
            print(f"[FleetLogger] Started logging {uid} -> {filename}")
        except Exception as e:
            print(f"[FleetLogger] Could not init {uid}: {e}")
            f.close()

    try:
        while logging_active:
            for uid, ser in device_ser_map.items():
                if uid not in writers:
                    continue
                try:
                    data = translate_sensor_struct(read_sensors(ser))
                    row = {"Timestamp": datetime.now().isoformat(), **data}
                    f, writer = writers[uid]
                    writer.writerow(row)
                    f.flush()

                    # Console summary
                    sys_power = data.get("SYS_Power", 0)
                    cpu_power = data.get("CPU_Power", 0)
                    gpu_power = data.get("GPU_Power", 0)
                    print(f"[{uid}] SYS:{sys_power:.0f}W CPU:{cpu_power:.0f}W GPU:{gpu_power:.0f}W",
                          end="\r", flush=True)

                except Exception as e:
                    print(f"[{uid}] Error: {e}")

            time.sleep(interval)
    finally:
        for f, _ in writers.values():
            f.close()


def start_fleet_logger(device_ser_map, interval=1.0):
    global logging_active
    logging_active = True
    thread = threading.Thread(target=sensor_logger_fleet, args=(device_ser_map, interval))
    thread.daemon = True
    thread.start()
    return thread


def stop_fleet_logger():
    global logging_active
    logging_active = False


def run_csv_logger(interval: float = 1.0):
    """Run fleet logger without TUI, with optional device selection."""
    print("Running BENCHLAB CSV fleet logger...\n")
    fleet_devices = discover_fleet_devices()
    if not fleet_devices:
        print("No BENCHLAB devices found.")
        return

    # Display fleet summary
    print("\n--- Available Devices ---")
    for i, dev in enumerate(fleet_devices, 1):
        print(f"{i}: Port: {dev['port']:<12} UID: {dev['uid']} FW: {dev['fw']}")

    # Prompt for selection
    selection = input(
        "\nEnter device numbers to log (comma-separated, e.g., 1,3), or 'all' for all devices: "
    ).strip().lower()

    if selection == "all" or not selection:
        selected_devices = fleet_devices
    else:
        try:
            indices = [int(s.strip()) - 1 for s in selection.split(",")]
            selected_devices = [fleet_devices[i] for i in indices if 0 <= i < len(fleet_devices)]
        except Exception:
            print("Invalid selection. Exiting.")
            return

    if not selected_devices:
        print("No valid devices selected. Exiting.")
        return

    # Open serials for selected devices
    device_ser_map = {}
    for dev in selected_devices:
        try:
            ser = serial.Serial(dev["port"], baudrate=115200, timeout=0.5)
            if not dev.get("uid") or dev["uid"] == "?":
                print(f"[WARN] No UID detected on {dev['port']}")
            else:
                device_ser_map[dev["uid"]] = ser
                print(f"[OK] Connected {dev['uid']} on {dev['port']}")
        except Exception as e:
            print(f"[FAIL] Could not open {dev['port']}: {e}")

    if not device_ser_map:
        print("No devices with valid UID were opened. Exiting.")
        return

    # Confirm logging
    print("\n--- Selected Devices ---")
    for uid, ser in device_ser_map.items():
        print(f"Port: {ser.port:<12} UID: {uid}")

    confirm = input("\nStart logging these devices? (Y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted by user.")
        for ser in device_ser_map.values():
            ser.close()
        return

    # Start logging thread
    thread = start_fleet_logger(device_ser_map, interval=interval)

    try:
        print("\nLogging started. Press Ctrl+C to stop.")
        while thread.is_alive():
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nStopping logging...")
        stop_fleet_logger()
        thread.join(timeout=2)
        for ser in device_ser_map.values():
            ser.close()
