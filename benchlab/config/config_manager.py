"""
Configuration Manager

Orchestrates device discovery, selection, and configuration operations.
"""

import json
import logging
import os
from typing import Optional, Dict, Any, List

from .config_client import create_config_client, ConfigClient
from .schema import ConfigFile, validate_config_file

logger = logging.getLogger("benchlab.config.manager")


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
            logger.info(f"Discovered {len(devices)} device(s) via direct serial")
            return devices
        except Exception as e:
            logger.error(f"Failed to discover direct devices: {e}")
            return []
    
    def _discover_named_pipe(self) -> List[Dict[str, Any]]:
        """Discover devices via named pipes."""
        import sys
        if not sys.platform.startswith("win"):
            logger.error("Named pipe discovery only supported on Windows")
            return []
        
        try:
            pipes = [name for name in os.listdir(r"\\.\pipe\\") 
                    if name.startswith("BenchlabSensorPipe_")]
            
            devices = []
            for pipe_name in pipes:
                try:
                    client = create_config_client('named_pipe', pipe_name)
                    info = client.get_device_info()
                    client.close()
                    
                    if info:
                        devices.append({
                            'pipe': pipe_name,
                            'guid': info.get('guid'),
                            'port': info.get('port'),
                            'productId': info.get('productId'),
                            'deviceName': info.get('deviceName'),
                        })
                except Exception as e:
                    logger.warning(f"Failed to query pipe {pipe_name}: {e}")
            
            logger.info(f"Discovered {len(devices)} device(s) via named pipes")
            return devices
            
        except Exception as e:
            logger.error(f"Failed to discover named pipe devices: {e}")
            return []
    
    def select_device(self, selector: Dict[str, Any], devices: List[Dict[str, Any]]) -> Optional[str]:
        """Select device based on selector criteria.
        
        Args:
            selector: Device selector from config
            devices: List of available devices
            
        Returns:
            Device identifier (port or pipe name), or None if not found
        """
        sel_type = selector.get('type')
        sel_value = selector.get('value')
        
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
                elif sel_type == 'productId':
                    # Need to connect to check product ID
                    pass
            else:
                # Named pipe matching
                if sel_type == 'guid' and device.get('guid') == sel_value:
                    return device.get('pipe')
                elif sel_type == 'productId' and device.get('productId') == sel_value:
                    return device.get('pipe')
                elif sel_type == 'pipeName' and device.get('pipe') == sel_value:
                    return device.get('pipe')
        
        return None
    
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
            
            # Read device info
            device_info = client.get_device_info()
            if not device_info:
                logger.error("Failed to read device info")
                return False
            
            # Build selector based on available info - prefer UID/GUID over port
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
            
            # Read device name
            device_name = client.read_device_name()
            
            # Read fan profiles (all 3 profiles, all 9 fans)
            fan_profiles = []
            for profile_id in range(3):
                fans = []
                for fan_id in range(9):
                    fan_config = client.read_fan_config(profile_id, fan_id)
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
                rgb_config = client.read_rgb_config(profile_id)
                if rgb_config:
                    rgb_config['profileId'] = profile_id
                    rgb_profiles.append(rgb_config)
            
            # Read calibration
            calibration = None
            try:
                calibration = client.read_calibration()
                if calibration:
                    logger.info("Calibration data exported")
            except Exception as e:
                logger.warning(f"Could not read calibration: {e}")
            
            # Build config structure
            config = {
                'version': '1.0',
                'description': f'Exported from {device_name or identifier}',
                'devices': [{
                    'selector': selector,
                    'deviceName': device_name,
                    'fanProfiles': fan_profiles if fan_profiles else None,
                    'rgbProfiles': rgb_profiles if rgb_profiles else None,
                    'calibration': calibration,
                    'saveToFlash': False
                }]
            }
            
            # Write to file
            with open(output_file, 'w') as f:
                json.dump(config, f, indent=2)
            
            client.close()
            logger.info(f"Exported configuration to {output_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to export config: {e}")
            return False
    
    def import_config(self, config_file: str, dry_run: bool = False, save_to_flash: bool = None) -> bool:
        """Import configuration from JSON file.
        
        Args:
            config_file: Input JSON file path
            dry_run: If True, validate only without applying
            save_to_flash: If True, override saveToFlash in config and save to flash.
                          If False, override and don't save. If None, use config file setting.
            
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
            
            if dry_run:
                logger.info("DRY RUN - No changes will be applied")
                return True
            
            # Discover available devices
            devices = self.discover_devices()
            if not devices:
                logger.error("No devices found")
                return False
            
            # Apply configuration to each device
            success_count = 0
            for i, device_config in enumerate(config.devices):
                logger.info(f"--- Device {i+1}/{len(config.devices)} ---")
                
                # Select device
                identifier = self.select_device(device_config.selector.model_dump(), devices)
                if not identifier:
                    logger.error(f"Device not found matching selector: {device_config.selector}")
                    continue
                
                logger.info(f"Selected: {identifier}")
                
                # Apply configuration
                if self._apply_device_config(identifier, device_config, save_to_flash):
                    logger.info("Configuration applied successfully")
                    success_count += 1
                else:
                    logger.error("Configuration failed")
            
            logger.info(f"{success_count}/{len(config.devices)} devices configured successfully")
            return success_count == len(config.devices)
            
        except Exception as e:
            logger.error(f"Failed to import config: {e}")
            return False
    
    def _apply_device_config(self, identifier: str, device_config, save_to_flash: Optional[bool] = None) -> bool:
        """Apply configuration to a single device.
        
        Args:
            identifier: Device identifier
            device_config: DeviceConfig object
            save_to_flash: Override saveToFlash setting (None = use config file setting)
            
        Returns:
            True if successful
        """
        try:
            client = create_config_client(self.source, identifier)
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
                        
                        if not client.write_fan_config(profile.profileId, fan_id, fan_dict):
                            logger.error(f"Failed to write fan config {profile.profileId}/{fan_id}")
                            success = False
            
            # Apply RGB profiles
            if device_config.rgbProfiles:
                for rgb in device_config.rgbProfiles:
                    rgb_dict = rgb.model_dump()
                    profile_id = rgb_dict.pop('profileId')
                    
                    if not client.write_rgb_config(profile_id, rgb_dict):
                        logger.error(f"Failed to write RGB config {profile_id}")
                        success = False
            
            # Apply calibration
            if device_config.calibration:
                if not client.write_calibration(device_config.calibration):
                    logger.error("Failed to write calibration")
                    success = False
            
            # Determine whether to save to flash (override takes precedence)
            should_save = save_to_flash if save_to_flash is not None else device_config.saveToFlash
            
            if should_save:
                if not client.save_config():
                    logger.error("Failed to save config to flash")
                    success = False
                else:
                    logger.info("Configuration saved to flash")
            
            client.close()
            return success
            
        except Exception as e:
            logger.error(f"Failed to apply device config: {e}")
            return False
