"""
Configuration Diff

Computes and renders a field-level diff between a device's current state
(as read by ConfigManager._read_current_state) and the desired state from
a config file, so users see exactly what would change before applying.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FieldChange:
    field: str
    current: Any
    desired: Any


@dataclass
class DiffResult:
    device_name_change: Optional[FieldChange] = None
    # (profileId, fanId) -> list of FieldChange
    fan_changes: Dict[Tuple[int, int], List[FieldChange]
                      ] = field(default_factory=dict)
    # profileId -> list of FieldChange
    rgb_changes: Dict[int, List[FieldChange]] = field(default_factory=dict)
    calibration_changed: bool = False
    read_errors: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.device_name_change
            or self.fan_changes
            or self.rgb_changes
            or self.calibration_changed
        )


def _dict_diff(current: Dict[str, Any], desired: Dict[str,
               Any], skip_keys=()) -> List[FieldChange]:
    """Return FieldChanges for keys present in *desired* whose value differs
    from *current* (missing-in-current counts as a difference, e.g. first
    import onto a fresh device)."""
    changes = []
    for k, desired_v in desired.items():
        if k in skip_keys:
            continue
        current_v = current.get(k) if current else None
        if current_v != desired_v:
            changes.append(
                FieldChange(
                    field=k,
                    current=current_v,
                    desired=desired_v))
    return changes


def compute_diff(current_state: Dict[str, Any],
                 desired_device_config) -> DiffResult:
    """Compare a device's current state against a DeviceConfig (pydantic
    model).

    Args:
        current_state: dict as returned by ConfigManager._read_current_state
                        (deviceName, fanProfiles, rgbProfiles, calibration,
                        readErrors)
        desired_device_config: schema.DeviceConfig instance from the
                        loaded config file

    Returns:
        DiffResult describing only the fields that differ.
    """
    result = DiffResult(read_errors=list(current_state.get('readErrors', [])))

    # Device name
    if desired_device_config.deviceName is not None:
        current_name = current_state.get('deviceName')
        if current_name != desired_device_config.deviceName:
            result.device_name_change = FieldChange(
                field='deviceName',
                current=current_name,
                desired=desired_device_config.deviceName)

    # Fan profiles: index current by (profileId, fanId)
    current_fans: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for profile in (current_state.get('fanProfiles') or []):
        pid = profile.get('profileId')
        for fan in profile.get('fans', []):
            current_fans[(pid, fan.get('fanId'))] = fan

    for profile in (desired_device_config.fanProfiles or []):
        for fan in profile.fans:
            key = (profile.profileId, fan.fanId)
            fan_dict = fan.model_dump()
            changes = _dict_diff(
                current_fans.get(
                    key, {}), fan_dict, skip_keys=(
                    'fanId',))
            if changes:
                result.fan_changes[key] = changes

    # RGB profiles: index current by profileId
    current_rgb: Dict[int, Dict[str, Any]] = {
        rgb.get('profileId'): rgb
        for rgb in (current_state.get('rgbProfiles') or [])
    }
    for rgb in (desired_device_config.rgbProfiles or []):
        rgb_dict = rgb.model_dump()
        changes = _dict_diff(
            current_rgb.get(
                rgb.profileId, {}), rgb_dict, skip_keys=(
                'profileId',))
        if changes:
            result.rgb_changes[rgb.profileId] = changes

    # Calibration: whole-blob comparison, not meant to be hand-edited field by
    # field
    if desired_device_config.calibration is not None:
        if current_state.get(
                'calibration') != desired_device_config.calibration:
            result.calibration_changed = True

    return result


def format_diff(diff: DiffResult, device_label: str) -> str:
    """Render a DiffResult as human-readable text."""
    lines = [f"Changes to be applied ({device_label}):"]
    has_content = False

    if diff.device_name_change:
        has_content = True
        c = diff.device_name_change
        lines.append("  Device Name:")
        lines.append(f"    {c.current!r} -> {c.desired!r}")

    for (profile_id, fan_id), changes in sorted(diff.fan_changes.items()):
        has_content = True
        lines.append(f"  Fan {fan_id} Profile {profile_id}:")
        for c in changes:
            lines.append(f"    {c.field}: {c.current} -> {c.desired}")

    for profile_id, changes in sorted(diff.rgb_changes.items()):
        has_content = True
        lines.append(f"  RGB Profile {profile_id}:")
        for c in changes:
            lines.append(f"    {c.field}: {c.current} -> {c.desired}")

    if diff.calibration_changed:
        has_content = True
        lines.append("  Calibration: (changed)")

    if not has_content:
        lines.append("  No changes.")

    if diff.read_errors:
        lines.append("")
        lines.append("  Warning: could not read current state for:")
        for err in diff.read_errors:
            lines.append(f"    - {err}")

    return "\n".join(lines)
