"""
Configuration Manager

Orchestrates device discovery, selection, and configuration operations.
"""

import json
import logging
from typing import Optional, Dict, Any, List

from .config_client import (
    create_config_client, ConfigClient, query_named_pipe,
    DISCOVERY_PIPE_NAME)
from .schema import validate_config_file
from .diff import compute_diff, format_diff, DiffResult

logger = logging.getLogger("benchlab.config.manager")


def _default_confirm(diff: DiffResult, device_label: str) -> bool:
    """Default confirm_callback for import_config: interactive y/n prompt."""
    answer = input(
        f"Apply these changes to {device_label}? (yes/no): ").strip().lower()
    return answer in ('y', 'yes')


class ConfigManager:
    """Manages device configuration import/export operations."""

    def __init__(self, source: str = 'direct'):
        """Initialize configuration manager.

        Args:
            source: Data source type ('direct' or 'named_pipe')
        """
        self.source = source
        logger.info(f"Initialized ConfigManager with source: {source}")

    def discover_devices(self) -> List[Dict[str, Any]]:
        """Discover available devices.

        Returns:
            List of device info dictionaries
        """
        if self.source == 'direct':
            return self._discover_direct()
        elif self.source == 'named_pipe':
            return self._discover_named_pipe()
        else:
            raise ValueError(f"Invalid source: {self.source}")

    def _discover_direct(self) -> List[Dict[str, Any]]:
        """Discover devices via direct serial connection."""
        try:
            from benchlab_pycore.core import get_fleet_info
            devices = get_fleet_info()
            logger.info(
                f"Discovered {
                    len(devices)} device(s) via direct serial")
            return devices
        except Exception as e:
            logger.error(f"Failed to discover direct devices: {e}")
            return []

    def _discover_named_pipe(self) -> List[Dict[str, Any]]:
        """Discover devices via the BenchlabDiscovery named pipe's ListDevices
        command -- a single round-trip that returns all devices' info
        (including the server-confirmed pipeName for each), rather than
        listing local pipes and querying each device individually.
        """
        import sys
        if not sys.platform.startswith("win"):
            logger.error("Named pipe discovery only supported on Windows")
            return []

        try:
            result = query_named_pipe(DISCOVERY_PIPE_NAME, "ListDevices")
            if not isinstance(result, list):
                logger.warning(f"Unexpected ListDevices response: {result!r}")
                return []

            devices = [
                {
                    'pipe': d.get('pipeName'),
                    'guid': d.get('guid'),
                    'port': d.get('port'),
                    'productId': d.get('productId'),
                    'deviceName': d.get('deviceName'),
                }
                for d in result
                if d.get('pipeName')
            ]

            logger.info(f"Discovered {len(devices)} device(s) via named pipes")
            return devices

        except Exception as e:
            logger.error(f"Failed to discover named pipe devices: {e}")
            return []

    def select_device(
            self, selector: Dict[str, Any],
            devices: List[Dict[str, Any]]) -> Optional[str]:
        """Select device based on selector criteria.

        Args:
            selector: Device selector from config
            devices: List of available devices

        Returns:
            Device identifier (port or pipe name), or None if not found
        """
        sel_type = selector.get('type')
        sel_value = selector.get('value')

        if sel_type == 'productId' and self.source == 'direct':
            logger.warning(
                "productId selector is not supported for the direct source "
                "(product ID isn't available from fleet discovery without "
                "connecting to each device first) — use guid or port instead."
            )
            return None

        if sel_type == 'any' and devices:
            # Return first available device
            if self.source == 'direct':
                return devices[0].get('port')
            else:
                return devices[0].get('pipe')

        for device in devices:
            if self.source == 'direct':
                # Direct serial matching
                if sel_type == 'port' and device.get('port') == sel_value:
                    return device.get('port')
                elif sel_type == 'guid' and device.get('uid') == sel_value:
                    return device.get('port')
            else:
                # Named pipe matching
                if sel_type == 'guid' and device.get('guid') == sel_value:
                    return device.get('pipe')
                elif (sel_type == 'productId'
                        and device.get('productId') == sel_value):
                    return device.get('pipe')
                elif (sel_type == 'pipeName'
                        and device.get('pipe') == sel_value):
                    return device.get('pipe')

        return None

    def _read_current_state(self, client: ConfigClient) -> Dict[str, Any]:
        """Read a device's current name/fan/RGB/calibration state.

        Returns a dict shaped like a DeviceConfig (deviceName, fanProfiles,
        rgbProfiles, calibration), plus a 'readErrors' list describing any
        section that failed to read. Never raises -- callers (export and the
        diff-before-apply path) both need read failures to be non-fatal so a
        flaky calibration read (for example) doesn't block everything else,
        matching the tolerance export_config already had for calibration.
        """
        read_errors: List[str] = []

        try:
            device_name = client.read_device_name()
        except Exception as e:
            logger.warning(f"Could not read device name: {e}")
            device_name = None
            read_errors.append(f"device name: {e}")

        # Read fan profiles (all 3 profiles, all 9 fans)
        fan_profiles = []
        for profile_id in range(3):
            fans = []
            for fan_id in range(9):
                try:
                    fan_config = client.read_fan_config(profile_id, fan_id)
                except Exception as e:
                    logger.warning(
                        f"Could not read fan config "
                        f"{profile_id}/{fan_id}: {e}")
                    read_errors.append(
                        f"fan profile {profile_id} fan {fan_id}: {e}")
                    continue
                if fan_config:
                    fan_config['fanId'] = fan_id
                    fans.append(fan_config)

            if fans:
                fan_profiles.append({
                    'profileId': profile_id,
                    'fans': fans
                })

        # Read RGB profiles (both profiles)
        rgb_profiles = []
        for profile_id in range(2):
            try:
                rgb_config = client.read_rgb_config(profile_id)
            except Exception as e:
                logger.warning(f"Could not read RGB config {profile_id}: {e}")
                read_errors.append(f"RGB profile {profile_id}: {e}")
                continue
            if rgb_config:
                rgb_config['profileId'] = profile_id
                rgb_profiles.append(rgb_config)

        # Read calibration
        calibration = None
        try:
            calibration = client.read_calibration()
        except Exception as e:
            logger.warning(f"Could not read calibration: {e}")
            read_errors.append(f"calibration: {e}")

        return {
            'deviceName': device_name,
            'fanProfiles': fan_profiles if fan_profiles else None,
            'rgbProfiles': rgb_profiles if rgb_profiles else None,
            'calibration': calibration,
            'readErrors': read_errors,
        }

    def export_config(self, identifier: str, output_file: str) -> bool:
        """Export device configuration to JSON file.

        Args:
            identifier: Device identifier (port or pipe name)
            output_file: Output JSON file path

        Returns:
            True if successful
        """
        try:
            client = create_config_client(self.source, identifier)
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

        try:
            # Read device info
            device_info = client.get_device_info()
            if not device_info:
                logger.error("Failed to read device info")
                return False

            # Build selector based on available info - prefer UID/GUID over
            # port
            uid = device_info.get('uid')
            guid = device_info.get('guid')

            if self.source == 'direct' and uid:
                selector = {
                    'type': 'guid',
                    'value': uid
                }
            elif self.source == 'named_pipe' and guid:
                selector = {
                    'type': 'guid',
                    'value': guid
                }
            else:
                # Fallback to port if UID/GUID not available
                selector = {
                    'type': 'port',
                    'value': identifier
                }

            state = self._read_current_state(client)
            if state['calibration']:
                logger.info("Calibration data exported")

            # Build config structure
            config = {
                'version': '1.0',
                'description':
                    f'Exported from {state["deviceName"] or identifier}',
                'devices': [{
                    'selector': selector,
                    'deviceName': state['deviceName'],
                    'fanProfiles': state['fanProfiles'],
                    'rgbProfiles': state['rgbProfiles'],
                    'calibration': state['calibration'],
                    'saveToFlash': False
                }]
            }

            # Write to file
            with open(output_file, 'w') as f:
                json.dump(config, f, indent=2)

            logger.info(f"Exported configuration to {output_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to export config: {e}")
            return False
        finally:
            client.close()

    def import_config(
        self,
        config_file: str,
        dry_run: bool = False,
        save_to_flash: bool = None,
        auto_confirm: bool = False,
        confirm_callback=None,
    ) -> bool:
        """Import configuration from JSON file.

        Reads each selected device's current state, computes a diff against
        the desired config, and prints it before applying -- so users see
        exactly what would change rather than writing blind.

        Args:
            config_file: Input JSON file path
            dry_run: If True, show the diff for each device but don't apply
                     or prompt for confirmation.
            save_to_flash: If True, override saveToFlash in config and save
                          to flash. If False, override and don't save.
                          If None, use config file setting.
            auto_confirm: If True, skip the confirmation prompt and apply
                          immediately after showing the diff (for automation).
            confirm_callback: Called with the rendered diff text; return True
                          to apply, False to skip this device. Defaults to an
                          interactive y/n prompt on stdin. Ignored when
                          auto_confirm=True or dry_run=True.

        Returns:
            True if successful
        """
        try:
            # Load and validate config file
            with open(config_file, 'r') as f:
                config_dict = json.load(f)

            config = validate_config_file(config_dict)
            logger.info(f"Loaded config: {config.description}")
            logger.info(f"Version: {config.version}")
            logger.info(f"Devices: {len(config.devices)}")

            # Discover available devices
            devices = self.discover_devices()
            if not devices:
                logger.error("No devices found")
                return False

            if confirm_callback is None:
                confirm_callback = _default_confirm

            # Apply configuration to each device
            success_count = 0
            for i, device_config in enumerate(config.devices):
                logger.info(f"--- Device {i + 1}/{len(config.devices)} ---")

                # Select device
                identifier = self.select_device(
                    device_config.selector.model_dump(), devices)
                if not identifier:
                    logger.error(
                        f"Device not found matching selector: {
                            device_config.selector}")
                    continue

                logger.info(f"Selected: {identifier}")

                # Read current state and show a diff before applying anything
                try:
                    client = create_config_client(self.source, identifier)
                except Exception as e:
                    logger.error(f"Failed to connect to {identifier}: {e}")
                    continue
                try:
                    current_state = self._read_current_state(client)
                finally:
                    client.close()

                diff = compute_diff(current_state, device_config)
                device_label = device_config.deviceName or identifier
                print(format_diff(diff, device_label))

                if diff.is_empty():
                    logger.info("No changes needed for this device.")
                    success_count += 1
                    continue

                if dry_run:
                    continue

                if not auto_confirm:
                    if not confirm_callback(diff, device_label):
                        logger.info("Skipped by user.")
                        continue

                # Apply configuration
                if self._apply_device_config(
                        identifier, device_config, save_to_flash):
                    logger.info("Configuration applied successfully")
                    success_count += 1
                else:
                    logger.error("Configuration failed")

            if dry_run:
                logger.info("DRY RUN - No changes were applied")
                return True

            logger.info(
                f"{success_count}/{len(config.devices)} devices "
                f"configured successfully")
            return success_count == len(config.devices)

        except Exception as e:
            logger.error(f"Failed to import config: {e}")
            return False

    def _apply_device_config(
            self,
            identifier: str,
            device_config,
            save_to_flash: Optional[bool] = None) -> bool:
        """Apply configuration to a single device.

        Args:
            identifier: Device identifier
            device_config: DeviceConfig object
            save_to_flash: Override saveToFlash setting
                (None = use config file setting)

        Returns:
            True if successful
        """
        try:
            client = create_config_client(self.source, identifier)
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

        try:
            success = True

            # Set device name
            if device_config.deviceName:
                if not client.write_device_name(device_config.deviceName):
                    logger.error("Failed to set device name")
                    success = False

            # Apply fan profiles
            if device_config.fanProfiles:
                for profile in device_config.fanProfiles:
                    for fan in profile.fans:
                        fan_dict = fan.model_dump()
                        fan_id = fan_dict.pop('fanId')

                        if not client.write_fan_config(
                                profile.profileId, fan_id, fan_dict):
                            logger.error(
                                f"Failed to write fan config {
                                    profile.profileId}/{fan_id}")
                            success = False

            # Apply RGB profiles
            if device_config.rgbProfiles:
                for rgb in device_config.rgbProfiles:
                    rgb_dict = rgb.model_dump()
                    profile_id = rgb_dict.pop('profileId')

                    if not client.write_rgb_config(profile_id, rgb_dict):
                        logger.error(
                            f"Failed to write RGB config {profile_id}")
                        success = False

            # Apply calibration
            if device_config.calibration:
                if not client.write_calibration(device_config.calibration):
                    logger.error("Failed to write calibration")
                    success = False

            # Determine whether to save to flash (override takes precedence)
            should_save = (
                save_to_flash if save_to_flash is not None
                else device_config.saveToFlash)

            if should_save:
                if not client.save_config():
                    logger.error("Failed to save config to flash")
                    success = False
                else:
                    logger.info("Configuration saved to flash")

            return success

        except Exception as e:
            logger.error(f"Failed to apply device config: {e}")
            return False
        finally:
            client.close()
