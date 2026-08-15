"""
JSON Schema and Validation for BENCHLAB Device Configuration

Defines Pydantic models for type-safe configuration handling.
"""

from typing import List, Optional, Any, Dict, Literal
from pydantic import BaseModel, Field, field_validator


class DeviceSelector(BaseModel):
    """Device selection criteria."""
    type: Literal["guid", "productId", "pipeName", "port", "any"] = Field(
        description="How to identify the device"
    )
    value: Optional[Any] = Field(
        default=None,
        description="Selector value (null for 'any')"
    )


class FanConfig(BaseModel):
    """Fan configuration for a single fan channel."""
    fanId: int = Field(ge=0, le=8, description="Fan ID (0-8)")
    FanMode: int = Field(ge=0, le=2, description="0=Auto, 1=Fixed, 2=Manual")
    TempSource: int = Field(ge=0, le=5, description="Temperature sensor index")
    Temp: List[int] = Field(
        default=[300, 600],
        min_length=2,
        max_length=2,
        description="Temperature curve points (0.1°C units)"
    )
    Duty: List[int] = Field(
        default=[30, 80],
        min_length=2,
        max_length=2,
        description="Duty cycle at each temp point (0-100%)"
    )
    RampStep: int = Field(
        ge=0,
        le=255,
        default=5,
        description="Rate of change")
    FixedDuty: int = Field(
        ge=0,
        le=100,
        default=50,
        description="Fixed duty cycle")
    MinDuty: int = Field(ge=0, le=100, default=20, description="Minimum duty")
    MaxDuty: int = Field(ge=0, le=100, default=100, description="Maximum duty")
    FanStop: int = Field(
        ge=0,
        le=1,
        default=0,
        description="0=disabled, 1=enabled")

    @field_validator('Duty')
    @classmethod
    def validate_duty(cls, v):
        for duty in v:
            if not 0 <= duty <= 100:
                raise ValueError(f"Duty values must be 0-100, got {duty}")
        return v


class FanProfile(BaseModel):
    """Fan profile containing multiple fan configurations."""
    profileId: int = Field(ge=0, le=2, description="Profile ID (0-2)")
    fans: List[FanConfig] = Field(description="Fan configurations")


class RGBConfig(BaseModel):
    """RGB profile configuration."""
    profileId: int = Field(ge=0, le=1, description="Profile ID (0-1)")
    Mode: int = Field(ge=0, le=9, description="RGB mode")
    Red: int = Field(ge=0, le=255, description="Red component")
    Green: int = Field(ge=0, le=255, description="Green component")
    Blue: int = Field(ge=0, le=255, description="Blue component")
    Direction: int = Field(ge=0, le=1, description="Animation direction")
    Speed: int = Field(ge=0, le=100, description="Animation speed")


class DeviceConfig(BaseModel):
    """Configuration for a single device."""
    selector: DeviceSelector = Field(description="Device selection criteria")
    deviceName: Optional[str] = Field(
        default=None,
        max_length=31,
        description="Device friendly name"
    )
    fanProfiles: Optional[List[FanProfile]] = Field(
        default=None,
        description="Fan profile configurations"
    )
    rgbProfiles: Optional[List[RGBConfig]] = Field(
        default=None,
        description="RGB profile configurations"
    )
    calibration: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Calibration data"
    )
    saveToFlash: bool = Field(
        default=False,
        description="Save config to flash after applying"
    )


class ConfigFile(BaseModel):
    """Root configuration file schema."""
    version: str = Field(default="1.0", description="Schema version")
    description: str = Field(
        default="BENCHLAB Configuration",
        description="Human-readable description"
    )
    devices: List[DeviceConfig] = Field(description="Device configurations")

    @field_validator('devices')
    @classmethod
    def validate_devices(cls, v):
        if not v:
            raise ValueError("Configuration must contain at least one device")
        return v


def validate_config_file(config_dict: dict) -> ConfigFile:
    """Validate a configuration dictionary against the schema.

    Args:
        config_dict: Dictionary loaded from JSON

    Returns:
        Validated ConfigFile object

    Raises:
        ValidationError: If validation fails
    """
    return ConfigFile(**config_dict)
