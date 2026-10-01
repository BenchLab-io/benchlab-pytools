"""
BENCHLAB Firmware Flash Tool - CLI Entry Point

Flashes BENCHLAB1/2 devices via ST's ROM DFU bootloader. The tool owns the
full round trip per device: confirms it's connected in normal CDC mode,
commands it into DFU mode, erases/writes/verifies the new firmware, and
leaves DFU mode so it re-enumerates as CDC again -- the user never manually
plugs/unplugs or pre-enters DFU mode.
"""

import argparse
import logging
import sys
from pathlib import Path

from .flash_manager import FlashManager
from . import image

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger("benchlab.flash.tool")


def cmd_list(args):
    """Handle --list command."""
    manager = FlashManager()
    devices = manager.discover_devices()

    if not devices:
        print("No devices found")
        return 1

    print(f"Found {len(devices)} device(s):")
    print()
    for i, device in enumerate(devices, 1):
        print(f"{i}. Port: {device.get('port')}")
        print(f"   UID:  {device.get('uid', 'N/A')}")
        print(f"   FW:   0x{device.get('fw', 0):02X}")
        print()

    return 0


def _resolve_ports(args, manager: FlashManager):
    """Resolve the list of target ports from --all/--port/auto-pick."""
    if args.all:
        devices = manager.discover_devices()
        return [d.get("port") for d in devices if d.get("port")]

    if args.port:
        return [p.strip() for p in args.port.split(",") if p.strip()]

    devices = manager.discover_devices()
    if len(devices) == 1:
        port = devices[0].get("port")
        print(f"Using device: {port}")
        return [port]
    if not devices:
        print("ERROR: No devices found")
        return []
    print(
        "ERROR: Multiple devices found, specify --port or --all:")
    for d in devices:
        print(f"  {d.get('port')} (UID: {d.get('uid', 'N/A')})")
    return []


def cmd_flash(args):
    """Handle the flash/verify-only command."""
    manager = FlashManager()

    ports = _resolve_ports(args, manager)
    if not ports:
        return 1

    try:
        data = image.load_image(Path(args.file))
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    targets = []
    for port in ports:
        info = manager.identify_device(port)
        if info is None:
            print(f"WARNING: Skipping {port} (not a recognized "
                  "BENCHLAB device in CDC mode)")
            continue
        ok, message = image.confirm_image_matches_device(
            args.file, info.get("ProductId"))
        if not ok:
            print(f"ERROR: {message}")
            return 1
        if message:
            print(f"WARNING: {message}")
        targets.append((port, info))

    if not targets:
        print("ERROR: No valid target devices to flash")
        return 1

    print()
    print(f"{'Verifying' if args.verify_only else 'Flashing'} "
          f"{Path(args.file).name} ({len(data)} bytes) on "
          f"{len(targets)} device(s):")
    for port, info in targets:
        print(f"  {port}  FW: 0x{info.get('FwVersion', 0):02X}  "
              f"UID: {info.get('uid', 'N/A')}")
    print()

    if not args.verify_only and not args.yes:
        print("This will ERASE and REWRITE the device's firmware. "
              "Interrupting this process may require a physical "
              "bootloader recovery.")
        confirm = input("Type FLASH to continue: ").strip()
        if confirm != "FLASH":
            print("Cancelled.")
            return 1

    def progress_cb(port, written, total):
        pct = (written / total * 100) if total else 100
        print(f"\r  {port}: {written}/{total} bytes ({pct:.0f}%)",
              end="", flush=True)

    results = manager.flash_many(
        [port for port, _ in targets], args.file,
        verify_only=args.verify_only, progress_cb=progress_cb)
    print()

    print()
    print("Results:")
    any_failed = False
    for result in results:
        status = "OK" if result.ok else "FAILED"
        if not result.ok:
            any_failed = True
        print(f"  {result.port}  [{status}]  {result.message}")

    return 1 if any_failed else 0


def main(args=None):
    """Main entry point for the flash tool.

    Args:
        args: Parsed arguments (if called from launcher)
    """
    if args is None:
        parser = argparse.ArgumentParser(
            description='BENCHLAB Firmware Flash Tool',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # List devices
  python -m benchlab -flash --list

  # Flash a single device (auto-selected if only one is connected)
  python -m benchlab -flash --file benchlab1-fw06-v0.6.0.bin

  # Flash a specific device
  python -m benchlab -flash --port COM4 --file firmware.bin

  # Flash every connected BENCHLAB device with the same image
  python -m benchlab -flash --all --file firmware.bin

  # Verify currently-flashed firmware against an image, no write
  python -m benchlab -flash --port COM4 --file firmware.bin --verify-only

  # Skip the confirmation prompt (for scripts/automation)
  python -m benchlab -flash --all --file firmware.bin --yes
            """
        )

        parser.add_argument('--list', action='store_true',
                            help='List available devices')
        parser.add_argument('--file', metavar='PATH',
                            help='Firmware image to flash (.bin or .hex)')

        target_group = parser.add_mutually_exclusive_group()
        target_group.add_argument(
            '--port', metavar='PORT',
            help='Target port(s), comma-separated (e.g. COM3,COM5)')
        target_group.add_argument(
            '--all', action='store_true',
            help='Flash every connected BENCHLAB device')

        parser.add_argument(
            '--verify-only', action='store_true',
            help='Only verify currently-flashed firmware against the '
                 'image, do not erase/write anything')
        parser.add_argument(
            '-y', '--yes', action='store_true',
            help='Skip the confirmation prompt before flashing')

        args = parser.parse_args()
    else:
        if not hasattr(args, 'list'):
            args.list = False
        if not hasattr(args, 'file'):
            args.file = None
        if not hasattr(args, 'port'):
            args.port = None
        if not hasattr(args, 'all'):
            args.all = False
        if not hasattr(args, 'verify_only'):
            args.verify_only = False
        if not hasattr(args, 'yes'):
            args.yes = False

    if args.list:
        return cmd_list(args)

    if not args.file:
        print("ERROR: --file is required. Use --help for usage.")
        return 1

    if not Path(args.file).exists():
        print(f"ERROR: Firmware file not found: {args.file}")
        return 1

    return cmd_flash(args)


if __name__ == '__main__':
    sys.exit(main())
