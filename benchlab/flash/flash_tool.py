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
from typing import List, Optional

from benchlab_pycore.core import (
    BENCHLAB_BL2_PRODUCT_ID,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
)

from .flash_manager import FlashManager
from . import image

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger("benchlab.flash.tool")

_VARIANT_CHOICES = {
    "benchlab1": BENCHLAB_ORIGINAL_PRODUCT_ID,
    "benchlab2": BENCHLAB_BL2_PRODUCT_ID,
}


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


def _resolve_variant(args, image_path: Path) -> int:
    """Resolve a product_id for a bare-DFU flash: from --variant if given,
    else from the image filename, else ask interactively. There's no
    device to read it from -- a manually-jumpered device exposes no
    BENCHLAB identity over bare USB DFU."""
    if args.variant:
        return _VARIANT_CHOICES[args.variant]

    guessed = image.guess_product_from_filename(image_path)
    if guessed is not None:
        name = next(k for k, v in _VARIANT_CHOICES.items() if v == guessed)
        print(f"Detected variant '{name}' from filename "
              f"'{image_path.name}'.")
        confirm = input("Is this correct? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            return guessed

    print()
    print("Could not determine the device variant automatically.")
    print("Which BENCHLAB variant is the jumpered device?")
    print("  1. BENCHLAB1 (ORIGINAL)")
    print("  2. BENCHLAB2")
    choice = input("Choice [1-2]: ").strip()
    if choice == "1":
        return BENCHLAB_ORIGINAL_PRODUCT_ID
    if choice == "2":
        return BENCHLAB_BL2_PRODUCT_ID
    raise ValueError(f"Invalid variant choice: {choice!r}")


def cmd_flash_bare_dfu(args):
    """Handle --dfu: flash a device that's already sitting in USB DFU mode
    with no CDC port at all (e.g. an old BL1 unit manually jumpered into
    its bootloader via BOOT0, since CMD_BOOTLOADER doesn't exist on
    firmware below the minimum version)."""
    manager = FlashManager()
    image_path = Path(args.file)

    try:
        data = image.load_image(image_path)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    try:
        product_id = _resolve_variant(args, image_path)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    variant_name = next(
        k for k, v in _VARIANT_CHOICES.items() if v == product_id)
    print()
    print(f"{'Verifying' if args.verify_only else 'Flashing'} "
          f"{image_path.name} ({len(data)} bytes) on the bare DFU device "
          f"as {variant_name}.")
    print()

    if not args.verify_only and not args.yes:
        print("This will ERASE and REWRITE the device's firmware. "
              "Interrupting this process may require a physical "
              "bootloader recovery.")
        confirm = input("Type FLASH to continue: ").strip()
        if confirm != "FLASH":
            print("Cancelled.")
            return 1

    def progress_cb(written, total):
        pct = (written / total * 100) if total else 100
        print(f"\r  {written}/{total} bytes ({pct:.0f}%)",
              end="", flush=True)

    result = manager.flash_bare_dfu(
        product_id, data, verify_only=args.verify_only,
        progress_cb=progress_cb)
    print()
    print()
    status = "OK" if result.ok else "FAILED"
    print(f"[{status}]  {result.message}")

    return 0 if result.ok else 1


def _parse_device_selection(choice: str, count: int) -> Optional[List[int]]:
    """Parse a numbered device selection: a single number, a comma list,
    or 'all'. Returns 0-based indices, or None if the input is invalid."""
    choice = choice.strip().lower()
    if choice == "all":
        return list(range(count))
    try:
        indices = [int(part.strip()) - 1
                   for part in choice.split(",") if part.strip()]
    except ValueError:
        return None
    if not indices or any(not (0 <= i < count) for i in indices):
        return None
    return indices


def interactive_mode(args):
    """Guided interactive mode: list CDC-connected devices, let the user
    pick one/many/all by number, ask for the firmware file, confirm twice
    (destructive operation), flash, and report per-device results."""
    print("=" * 60)
    print("BENCHLAB Firmware Flash Tool - Interactive Mode")
    print("=" * 60)
    print()

    manager = FlashManager()
    devices = manager.discover_devices()

    if not devices:
        print("ERROR: No devices found. Devices must be connected in "
              "normal CDC mode to appear here -- use --dfu for a device "
              "already manually jumpered into DFU mode.")
        print()
        input("Press Enter to exit...")
        return 1

    print(f"Found {len(devices)} device(s):")
    for i, device in enumerate(devices, 1):
        print(f"  {i}. {device.get('port')}  "
              f"Variant: {device.get('variant', 'N/A')}  "
              f"FW: {device.get('fw', 'N/A')}  "
              f"UID: {device.get('uid', 'N/A')}")
    print()

    print("Which device(s) do you want to flash?")
    print("  Enter a number, a comma-separated list (e.g. 1,3), "
          "or 'all'.")
    choice = input("Selection: ").strip()
    indices = _parse_device_selection(choice, len(devices))
    if indices is None:
        print("ERROR: Invalid selection.")
        return 1
    selected = [devices[i] for i in indices]

    print()
    print("Selected device(s):")
    for device in selected:
        print(f"  {device.get('port')}  "
              f"Variant: {device.get('variant', 'N/A')}")
    print()

    file_path_str = input(
        "Path to firmware file (.bin or .hex): ").strip().strip('"')
    if not file_path_str:
        print("Cancelled.")
        return 0
    file_path = Path(file_path_str)
    if not file_path.exists():
        print(f"ERROR: Firmware file not found: {file_path}")
        return 1

    try:
        data = image.load_image(file_path)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    targets = []
    for device in selected:
        product_id = _VARIANT_CHOICES[
            "benchlab2" if device.get("variant") == "BL2" else "benchlab1"]
        ok, message = image.confirm_image_matches_device(
            file_path, product_id)
        if not ok:
            print(f"ERROR: {message}")
            return 1
        if message:
            print(f"WARNING: {message}")
        targets.append(device)

    print()
    print(f"About to flash {file_path.name} ({len(data)} bytes) on "
          f"{len(targets)} device(s):")
    for device in targets:
        print(f"  {device.get('port')}  "
              f"Variant: {device.get('variant', 'N/A')}  "
              f"FW: {device.get('fw', 'N/A')}")
    print()

    print("This will ERASE and REWRITE the selected device(s)' firmware.")
    confirm1 = input("Continue? [y/N]: ").strip().lower()
    if confirm1 not in ("y", "yes"):
        print("Cancelled.")
        return 0

    print()
    print("Last chance -- interrupting this process may require a "
          "physical bootloader recovery.")
    confirm2 = input("Type FLASH to proceed: ").strip()
    if confirm2 != "FLASH":
        print("Cancelled.")
        return 0

    def progress_cb(port, written, total):
        pct = (written / total * 100) if total else 100
        print(f"\r  {port}: {written}/{total} bytes ({pct:.0f}%)",
              end="", flush=True)

    print()
    results = manager.flash_many(
        [device.get("port") for device in targets], file_path,
        progress_cb=progress_cb)
    print()

    print()
    print("Results:")
    any_failed = False
    for result in results:
        status = "OK" if result.ok else "FAILED"
        if not result.ok:
            any_failed = True
        print(f"  {result.port}  [{status}]  {result.message}")
    print()
    input("Press Enter to exit...")

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

  # Flash an old BL1 unit manually jumpered into DFU mode (BOOT0), since
  # its firmware predates CMD_BOOTLOADER support. Prompts for the variant
  # if it can't be guessed from the filename.
  python -m benchlab -flash --dfu --file firmware.bin
  python -m benchlab -flash --dfu --variant benchlab1 --file firmware.bin
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
        target_group.add_argument(
            '--dfu', action='store_true',
            help='Flash a device already in USB DFU mode with no CDC '
                 'port (e.g. an old BL1 unit manually jumpered into its '
                 'bootloader via BOOT0)')

        parser.add_argument(
            '--variant', choices=sorted(_VARIANT_CHOICES),
            help='BENCHLAB variant of the --dfu target (only needed with '
                 '--dfu; prompted for interactively if omitted)')
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
        if not hasattr(args, 'dfu'):
            args.dfu = False
        if not hasattr(args, 'variant'):
            args.variant = None
        if not hasattr(args, 'verify_only'):
            args.verify_only = False
        if not hasattr(args, 'yes'):
            args.yes = False

    if args.list:
        return cmd_list(args)

    if not args.file and not args.port and not args.all and not args.dfu:
        return interactive_mode(args)

    if not args.file:
        print("ERROR: --file is required. Use --help for usage.")
        return 1

    if not Path(args.file).exists():
        print(f"ERROR: Firmware file not found: {args.file}")
        return 1

    if args.dfu:
        return cmd_flash_bare_dfu(args)

    return cmd_flash(args)


if __name__ == '__main__':
    sys.exit(main())
