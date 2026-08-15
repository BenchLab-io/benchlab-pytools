"""
BENCHLAB Configuration Tool - CLI Entry Point

Command-line interface for importing/exporting device configuration.
"""

import argparse
import logging
import sys
from pathlib import Path

from .config_manager import ConfigManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger("benchlab.config.tool")


def cmd_export(args):
    """Handle export command."""
    manager = ConfigManager(source=args.source)

    # Discover devices
    devices = manager.discover_devices()
    if not devices:
        print("ERROR: No devices found")
        return 1

    # Select device
    if args.device:
        identifier = args.device
    else:
        # Use first device
        if args.source == 'direct':
            identifier = devices[0].get('port')
        else:
            identifier = devices[0].get('pipe')
        print(f"Using device: {identifier}")

    # Export configuration
    if manager.export_config(identifier, args.output):
        print(f"SUCCESS: Configuration exported to {args.output}")
        return 0
    else:
        print("ERROR: Export failed")
        return 1


def cmd_import(args):
    """Handle import command."""
    if not Path(args.config_file).exists():
        print(f"ERROR: Config file not found: {args.config_file}")
        return 1

    manager = ConfigManager(source=args.source)

    if args.dry_run:
        print("DRY RUN MODE - showing what would change, "
              "no changes will be applied")

    if manager.import_config(
            args.config_file,
            dry_run=args.dry_run,
            auto_confirm=args.yes):
        if not args.dry_run:
            print("SUCCESS: Configuration applied")
        return 0
    else:
        print("ERROR: Import failed")
        return 1


def cmd_list(args):
    """Handle list command."""
    manager = ConfigManager(source=args.source)
    devices = manager.discover_devices()

    if not devices:
        print("No devices found")
        return 1

    print(f"Found {len(devices)} device(s):")
    print()

    for i, device in enumerate(devices, 1):
        if args.source == 'direct':
            print(f"{i}. Port: {device.get('port')}")
            print(f"   UID:  {device.get('uid', 'N/A')}")
            print(f"   FW:   0x{device.get('firmware', 0):08X}")
        else:
            print(f"{i}. Pipe: {device.get('pipe')}")
            print(f"   GUID: {device.get('guid', 'N/A')}")
            print(f"   Port: {device.get('port', 'N/A')}")
            print(f"   Name: {device.get('deviceName', 'N/A')}")
        print()

    return 0


def interactive_mode(args):
    """Interactive mode for import/export operations."""
    print("=" * 60)
    print("BENCHLAB Configuration Tool - Interactive Mode")
    print("=" * 60)
    print()

    # Debug: log the source being used
    logger.info(f"Using data source: {args.source}")

    # List available devices
    manager = ConfigManager(source=args.source)
    devices = manager.discover_devices()

    if not devices:
        print("ERROR: No devices found")
        print()
        input("Press Enter to exit...")
        return 1

    print(f"Found {len(devices)} device(s):")
    for i, device in enumerate(devices, 1):
        if args.source == 'direct':
            print(f"  {i}. {device.get('port')} - {device.get('uid', 'N/A')}")
        else:
            print(
                f"  {i}. {device.get('deviceName', 'N/A')} - "
                f"{device.get('guid', 'N/A')}")
    print()

    # Ask for operation type
    print("What would you like to do?")
    print("  1. Import configuration (apply JSON to device)")
    print("  2. Export configuration (save device config to JSON)")
    print()
    operation = input("Choice [1-2]: ").strip()

    if operation == "1":
        # Import mode
        print()
        print("Enter path to JSON configuration file:")
        print("(or press Enter to cancel)")
        config_file = input("> ").strip()

        if not config_file:
            print("Cancelled.")
            return 0

        if not Path(config_file).exists():
            print(f"ERROR: File not found: {config_file}")
            print()
            input("Press Enter to exit...")
            return 1

        # Ask about saving to flash up front, so import_config's per-device
        # diff/confirm below is the only remaining approval step.
        print()
        print("Save configuration to device flash memory?")
        print("(If 'no', changes will only be applied to RAM "
              "and lost on device reset)")
        save_flash = input("Save to flash? (yes/no): ").strip().lower()

        # Read + diff + confirm happens per device inside import_config,
        # which prints the diff and prompts before applying each one.
        print()
        if manager.import_config(
            config_file,
            dry_run=False,
            save_to_flash=(
                save_flash in (
                    'yes',
                'y'))):
            print()
            if save_flash in ('yes', 'y'):
                print("SUCCESS: Configuration applied and saved to flash")
            else:
                print("SUCCESS: Configuration applied to RAM only")
            print()
            input("Press Enter to exit...")
            return 0
        else:
            print()
            print("ERROR: Configuration failed")
            print()
            input("Press Enter to exit...")
            return 1

    elif operation == "2":
        # Export mode
        print()

        # Select device if multiple available
        if len(devices) > 1:
            print("Select device to export:")
            for i, device in enumerate(devices, 1):
                if args.source == 'direct':
                    print(
                        f"  {i}. {device.get('port')} - "
                        f"{device.get('uid', 'N/A')}")
                else:
                    print(
                        f"  {i}. {device.get('deviceName', 'N/A')} - "
                        f"{device.get('guid', 'N/A')}")
            print()
            device_choice = input(f"Device [1-{len(devices)}]: ").strip()
            try:
                device_idx = int(device_choice) - 1
                if not (0 <= device_idx < len(devices)):
                    print("Invalid device selection.")
                    return 1
            except ValueError:
                print("Invalid device selection.")
                return 1
        else:
            device_idx = 0

        selected_device = devices[device_idx]
        if args.source == 'direct':
            identifier = selected_device.get('port')
        else:
            identifier = selected_device.get('pipe')

        print()
        print("Enter output filename (e.g., my_config.json):")
        output_file = input("> ").strip()

        if not output_file:
            print("Cancelled.")
            return 0

        # Add .json extension if not present
        if not output_file.endswith('.json'):
            output_file += '.json'

        # Export configuration
        print()
        print(f"Exporting configuration from {identifier}...")
        if manager.export_config(identifier, output_file):
            print()
            print(f"SUCCESS: Configuration exported to {output_file}")
            print()
            input("Press Enter to exit...")
            return 0
        else:
            print()
            print("ERROR: Export failed")
            print()
            input("Press Enter to exit...")
            return 1

    else:
        print("Invalid choice.")
        return 1


def main(args=None):
    """Main entry point for config tool.

    Args:
        args: Parsed arguments (if called from launcher)
    """
    # If called without args, parse from command line
    if args is None:
        parser = argparse.ArgumentParser(
            description='BENCHLAB Device Configuration Tool',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # List devices
  python -m benchlab -config --list

  # Export configuration
  python -m benchlab -config --export config.json
  python -m benchlab -config --export config.json --device COM4

  # Import configuration (shows a diff of what would change,
  # then asks to confirm)
  python -m benchlab -config --import config.json

  # Preview changes without applying anything
  python -m benchlab -config --import config.json --dry-run

  # Apply without the confirmation prompt (for scripts/automation)
  python -m benchlab -config --import config.json --yes

  # Use named pipe source (Windows only)
  python -m benchlab -config --list --source named_pipe
  python -m benchlab -config --import config.json --source named_pipe
            """
        )

        parser.add_argument('--source', choices=['direct', 'named_pipe'],
                            default='direct',
                            help='Data source type (default: direct)')

        # Commands
        parser.add_argument('--list', action='store_true',
                            help='List available devices')
        parser.add_argument('--export', metavar='FILE', dest='output',
                            help='Export configuration to JSON file')
        parser.add_argument('--import', metavar='FILE', dest='config_file',
                            help='Import configuration from JSON file')

        # Options
        parser.add_argument('--device', metavar='ID',
                            help='Device identifier (port or pipe name)')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would change without applying anything '
                 '(connects to the device and reads its current '
                 'config)')
        parser.add_argument(
            '-y',
            '--yes',
            action='store_true',
            help='Apply changes without the confirmation prompt '
                 '(the diff is still shown)')

        args = parser.parse_args()
    else:
        # Called from launcher - add missing command attributes
        # Ensure source exists (should be set by launcher)
        if not hasattr(args, 'source'):
            args.source = 'direct'
        if not hasattr(args, 'list'):
            args.list = False
        if not hasattr(args, 'output'):
            args.output = None
        if not hasattr(args, 'config_file'):
            args.config_file = None
        if not hasattr(args, 'device'):
            args.device = None
        if not hasattr(args, 'dry_run'):
            args.dry_run = False
        if not hasattr(args, 'yes'):
            args.yes = False

    # If no command specified, run interactive mode
    if not (args.list or args.output or args.config_file):
        return interactive_mode(args)

    # Execute command
    if args.list:
        return cmd_list(args)
    elif args.output:
        return cmd_export(args)
    elif args.config_file:
        return cmd_import(args)
    else:
        print("ERROR: No command specified. Use --help for usage.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
