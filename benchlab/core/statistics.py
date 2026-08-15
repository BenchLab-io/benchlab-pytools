"""
Statistics Module for TUI and other tools

Provides thread-safe channel statistics tracking with min/max/average
calculations that can be used by any tool consuming telemetry data.
"""

import threading
from collections import defaultdict
from typing import Dict, Optional, Tuple


def _make_stat() -> Dict[str, float]:
    """Create a new statistics dictionary."""
    return {'min': float('inf'), 'max': float('-inf'), 'sum': 0.0, 'count': 0}


class ChannelStats:
    """Thread-safe per-device, per-channel running statistics.

    Tracks minimum, maximum, and average values for numeric telemetry channels
    across multiple devices. Can be used by TUI, dashboard, or any other tool
    that needs statistical analysis of telemetry data.
    """

    def __init__(self):
        """Initialize statistics tracker."""
        self._lock = threading.Lock()
        # device -> channel -> stat dict
        self._data: Dict[str, Dict[str, Dict[str, float]]
                         ] = defaultdict(lambda: defaultdict(_make_stat))

    def update(self, device: str, channel: str, value: float) -> None:
        """Update statistics for a device channel.

        Args:
            device: Device identifier (UID or port)
            channel: Channel name (e.g., 'SYS_Power', 'Chip_Temp')
            value: Numeric value to add to statistics
        """
        if value is None or not isinstance(value, (int, float)):
            return

        with self._lock:
            s = self._data[device][channel]
            if value < s['min']:
                s['min'] = value
            if value > s['max']:
                s['max'] = value
            s['sum'] += value
            s['count'] += 1

    def get(self,
            device: str,
            channel: str) -> Tuple[Optional[float],
                                   Optional[float],
                                   Optional[float]]:
        """Get statistics for a device channel.

        Args:
            device: Device identifier
            channel: Channel name

        Returns:
            Tuple of (min, max, avg) or (None, None, None) if no data
        """
        with self._lock:
            s = self._data[device][channel]
            if s['count'] == 0:
                return None, None, None
            return s['min'], s['max'], s['sum'] / s['count']

    def get_all(self,
                device: str) -> Dict[str,
                                     Tuple[Optional[float],
                                           Optional[float],
                                           Optional[float]]]:
        """Get statistics for all channels of a device.

        Args:
            device: Device identifier

        Returns:
            Dictionary mapping channel names to (min, max, avg) tuples
        """
        with self._lock:
            result = {}
            for channel in self._data[device]:
                s = self._data[device][channel]
                if s['count'] == 0:
                    result[channel] = (None, None, None)
                else:
                    result[channel] = (
                        s['min'], s['max'], s['sum'] / s['count'])
            return result

    def reset(self, device: Optional[str] = None) -> None:
        """Reset statistics for a device or all devices.

        Args:
            device: Device identifier to reset, or None to reset all devices
        """
        with self._lock:
            if device is None:
                # Reset all devices
                self._data.clear()
            else:
                # Reset specific device
                if device in self._data:
                    self._data[device].clear()

    def get_devices(self) -> list[str]:
        """Get list of all devices with statistics.

        Returns:
            List of device identifiers with recorded statistics
        """
        with self._lock:
            return list(self._data.keys())

    def get_channels(self, device: str) -> list[str]:
        """Get list of all channels for a device.

        Args:
            device: Device identifier

        Returns:
            List of channel names with recorded statistics for the device
        """
        with self._lock:
            if device in self._data:
                return list(self._data[device].keys())
            return []

    def has_data(self, device: str, channel: Optional[str] = None) -> bool:
        """Check if statistics data exists.

        Args:
            device: Device identifier
            channel: Optional channel name to check specifically

        Returns:
            True if statistics data exists
        """
        with self._lock:
            if device not in self._data:
                return False
            if channel is None:
                return bool(self._data[device])
            return (channel in self._data[device]
                    and self._data[device][channel]['count'] > 0)


class StatsFormatter:
    """Helper class for formatting statistics for display."""

    @staticmethod
    def format_stat_string(min_val: Optional[float], max_val: Optional[float],
                           avg_val: Optional[float], decimals: int = 1,
                           unit: str = "", width: int = 10) -> str:
        """Format statistics as a display string.

        Args:
            min_val: Minimum value
            max_val: Maximum value
            avg_val: Average value
            decimals: Number of decimal places
            unit: Unit string (e.g., 'W', '°C', 'V')
            width: Fixed width for each stat column

        Returns:
            Formatted string like "↓10.2W  ↑15.8W  ~13.1W"
        """
        if min_val is None or max_val is None or avg_val is None:
            return ""

        lo = f"↓{min_val:.{decimals}f}{unit}"
        hi = f"↑{max_val:.{decimals}f}{unit}"
        av = f"~{avg_val:.{decimals}f}{unit}"

        return f"  {lo:<{width}} {hi:<{width}} {av:<{width}}"

    @staticmethod
    def format_compact_range(
            min_val: Optional[float],
            max_val: Optional[float],
            avg_val: Optional[float],
            decimals: int = 1,
            unit: str = "") -> str:
        """Format statistics as a compact range string.

        Args:
            min_val: Minimum value
            max_val: Maximum value
            avg_val: Average value
            decimals: Number of decimal places
            unit: Unit string

        Returns:
            Formatted string like "10.2-15.8 ~13.1W"
        """
        if min_val is None or max_val is None or avg_val is None:
            return ""

        return f"{
            min_val:.{decimals}f}-{
            max_val:.{decimals}f} ~{
            avg_val:.{decimals}f}{unit}"


# Convenience function for creating a stats callback
def create_stats_callback(stats: ChannelStats):
    """Create a callback function for DataSourceManager.

    Args:
        stats: ChannelStats instance to update

    Returns:
        Callback function that can be passed to DataSourceManager
    """
    def callback(device_uid: str, channel: str, value: float):
        stats.update(device_uid, channel, value)

    return callback
