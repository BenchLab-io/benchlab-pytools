"""
Configuration Module for TUI

Centralizes all configuration constants, scales, thresholds, and UI settings
for easy customization and maintenance.
"""

import curses
from typing import Tuple


# ═══════════════════════════════════════════════════════════════════════════════
#  Display Configuration
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum terminal size requirements
# The 12VHPWR tab's stacked Power/Current/Voltage sections (12 channels
# each) need rows through ~45 for the last Voltage bar, plus 2 rows for
# the status bar at the bottom (drawn at height-2/height-1).
MIN_TERMINAL_ROWS = 48
MIN_TERMINAL_COLS = 100

# Tab configuration
TAB_NAMES = [
    "Fleet",
    "Device",
    "System",
    "Motherboard",
    "12VHPWR",
    "Voltage",
    "Temperature",
    "Fans"]

# Statistics display width
STAT_COLUMN_WIDTH = 10


# ═══════════════════════════════════════════════════════════════════════════════
#  Color Pair Definitions
# ═══════════════════════════════════════════════════════════════════════════════

COLOR_PAIRS = {
    'header': 1,       # White on blue - active tab/header
    'ok': 2,           # Green - OK/connected status
    'error': 3,        # Red - error/warning/disconnected
    'caution': 4,      # Yellow - caution/power data/company color
    'info': 5,         # Cyan - temperature/fans/info
    'voltage': 6,      # Blue - voltage readings
    'default': 7,      # White - default text
    'highlight': 8,    # Black on cyan - external/special highlight
}


def init_color_pairs():
    """Initialize curses color pairs. Call this after curses.start_color()."""
    curses.init_pair(
        COLOR_PAIRS['header'],
        curses.COLOR_WHITE,
        curses.COLOR_BLUE)
    curses.init_pair(COLOR_PAIRS['ok'], curses.COLOR_GREEN, -1)
    curses.init_pair(COLOR_PAIRS['error'], curses.COLOR_RED, -1)
    curses.init_pair(COLOR_PAIRS['caution'], curses.COLOR_YELLOW, -1)
    curses.init_pair(COLOR_PAIRS['info'], curses.COLOR_CYAN, -1)
    curses.init_pair(COLOR_PAIRS['voltage'], curses.COLOR_BLUE, -1)
    curses.init_pair(COLOR_PAIRS['default'], curses.COLOR_WHITE, -1)
    curses.init_pair(
        COLOR_PAIRS['highlight'],
        curses.COLOR_BLACK,
        curses.COLOR_CYAN)


# ═══════════════════════════════════════════════════════════════════════════════
#  Progress Bar Scales and Thresholds
# ═══════════════════════════════════════════════════════════════════════════════

class BarScales:
    """Configuration for progress bar scales and thresholds."""

    # Power scaling (Watts)
    # Fixed bar ceiling (W); set to None for auto-scale
    POWER_MAX = 1000.0
    POWER_AUTO_FLOOR = 10.0         # Minimum ceiling when auto-scaling

    # Current scaling (Amps)
    # Fixed bar ceiling (A); set to None for auto-scale
    CURRENT_MAX = 100.0
    CURRENT_AUTO_FLOOR = 0.0        # Minimum ceiling when auto-scaling

    # Temperature scaling (°C)
    CHIP_TEMP_MAX = 100.0           # Chip temperature bar ceiling
    AMBIENT_TEMP_MAX = 70.0         # Ambient temperature bar ceiling
    SENSOR_TEMP_MAX = 100.0         # Sensor temperature bar ceiling

    # Temperature warning thresholds (above this → bar turns red)
    CHIP_TEMP_WARN = 70.0
    SENSOR_TEMP_WARN = 60.0

    # Humidity scaling (%)
    HUMIDITY_MAX = 100.0

    # Fan scaling
    FAN_RPM_MAX = 3000              # RPM at which the bar is full
    FAN_DUTY_MAX = 100.0            # Duty cycle percentage max

    # Voltage scaling (V)
    VIN_VOLTAGE_MAX = 15.0          # VIN channel voltage bar ceiling
    RAIL_VOLTAGE_MAX = 15.0         # Rail voltage bar ceiling (12V rails)
    BOARD_VOLTAGE_MAX = 5.0         # Board voltage bar ceiling (Vdd/Vref)


class VoltageBands:
    """Voltage OK ranges (inclusive) for status determination."""

    RAIL = (10.0, 14.0)     # EPS/PCIE/HPWR 12V rails
    VDD = (3.0, 3.6)        # Board Vdd voltage
    VREF = (1.6, 2.0)       # Board Vref voltage
    VIN = (0.5, 15.0)       # Generic VIN channels


class ThermalStatus:
    """Thermal status classification thresholds."""

    NORMAL_MAX = 60.0       # Below this → NORMAL
    WARNING_MAX = 80.0      # Above normal but below this → WARNING
    # Above WARNING_MAX → CRITICAL


# ═══════════════════════════════════════════════════════════════════════════════
#  Channel Definitions
# ═══════════════════════════════════════════════════════════════════════════════

class Channels:
    """Telemetry channel definitions and groupings.

    Supports both ORIGINAL and BL2 (BENCHLAB 2) device variants. Use get_*
    methods with sensor_data dict to get variant-appropriate channel lists.
    """

    # ──────────────────────────────────────────────────────────────────
    # ORIGINAL Variant Channels (baseline)
    # ──────────────────────────────────────────────────────────────────

    # Power summary channels (Tab 2 summary section) - same for both variants
    POWER_SUMMARY = [
        ('SYS_Power', 'SYS Power'),
        ('CPU_Power', 'CPU Power'),
        ('GPU_Power', 'GPU Power'),
        ('MB_Power', 'MB Power'),
    ]

    # Rail channels for ORIGINAL variant (11 power sensors total, 7 shown in
    # System tab)
    # Note: ORIGINAL also has ATX3V, ATX5V, ATX5VSB, ATX12V which are shown in
    # Motherboard tab
    RAIL_CHANNELS_ORIGINAL = [
        ('EPS1', 'EPS_1'),
        ('EPS2', 'EPS_2'),
        ('PCIE1', 'PCIE_1'),
        ('PCIE2', 'PCIE_2'),
        ('PCIE3', 'PCIE_3'),
        ('HPWR1', '12V_HPWR_1'),
        ('HPWR2', '12V_HPWR_2'),
    ]

    # Rail channels for BL2 (BENCHLAB 2) variant (7 main rails, same as
    # ORIGINAL)
    # The 12 additional HPWR_Wx sense lines are shown in a separate tab
    RAIL_CHANNELS_BL2 = [
        ('EPS1', 'EPS_1'),
        ('EPS2', 'EPS_2'),
        ('PCIE1', 'PCIE_1'),
        ('PCIE2', 'PCIE_2'),
        ('PCIE3', 'PCIE_3'),
        ('HPWR1', '12V_HPWR_1'),
        ('HPWR2', '12V_HPWR_2'),
    ]

    # Motherboard rail channels (shown in Motherboard tab) - same for both
    # variants
    MB_RAIL_CHANNELS = [
        ('ATX3V', 'ATX 3.3V'),
        ('ATX5V', 'ATX 5V'),
        ('ATX5VSB', 'ATX 5VSB'),
        ('ATX12V', 'ATX 12V'),
    ]

    # BL2-specific HPWR_Wx sense lines (shown in separate 12VHPWR tab)
    HPWR_SENSE_CHANNELS = [
        ('HPWR1_W1', 'HPWR1_W1'),
        ('HPWR1_W2', 'HPWR1_W2'),
        ('HPWR1_W3', 'HPWR1_W3'),
        ('HPWR1_W4', 'HPWR1_W4'),
        ('HPWR1_W5', 'HPWR1_W5'),
        ('HPWR1_W6', 'HPWR1_W6'),
        ('HPWR2_W1', 'HPWR2_W1'),
        ('HPWR2_W2', 'HPWR2_W2'),
        ('HPWR2_W3', 'HPWR2_W3'),
        ('HPWR2_W4', 'HPWR2_W4'),
        ('HPWR2_W5', 'HPWR2_W5'),
        ('HPWR2_W6', 'HPWR2_W6'),
    ]

    # Board voltage channels (Voltage tab) - same for both variants
    BOARD_VOLTAGES = [
        ('Vdd', 'Vdd', VoltageBands.VDD),
        ('Vref', 'Vref', VoltageBands.VREF),
    ]

    # VIN voltage measurement channels (Voltage tab) - same for both variants
    VIN_CHANNELS = [f'VIN_{i}' for i in range(13)]  # VIN_0 through VIN_12

    # Temperature sensors for ORIGINAL variant (4 sensors: TS_1 to TS_4)
    TEMPERATURE_SENSORS_ORIGINAL = [f'TS_{i}' for i in range(1, 5)]

    # Temperature sensors for BL2 variant (8 sensors: TS_1-4 +
    # TS_HPWR1_IN/OUT, TS_HPWR2_IN/OUT)
    TEMPERATURE_SENSORS_BL2 = [
        'TS_1', 'TS_2', 'TS_3', 'TS_4',
        'TS_HPWR1_IN', 'TS_HPWR1_OUT',
        'TS_HPWR2_IN', 'TS_HPWR2_OUT',
    ]

    # System environment channels - same for both variants
    ENVIRONMENT_CHANNELS = [
        ('Chip_Temp', 'Chip Temp'),
        ('Ambient_Temp', 'Ambient Temp'),
        ('Humidity', 'Humidity'),
    ]

    # ──────────────────────────────────────────────────────────────────
    # Dynamic Channel Resolution
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def get_rail_channels(variant: str = 'ORIGINAL') -> list:
        """Get rail channels based on device variant.

        Args:
            variant: 'ORIGINAL' or 'BL2'

        Returns:
            List of (key_prefix, label) tuples
        """
        if variant == 'BL2':
            return Channels.RAIL_CHANNELS_BL2
        return Channels.RAIL_CHANNELS_ORIGINAL

    @staticmethod
    def get_temperature_sensors(variant: str = 'ORIGINAL') -> list:
        """Get temperature sensor keys based on device variant.

        Args:
            variant: 'ORIGINAL' or 'BL2'

        Returns:
            List of sensor key strings
        """
        if variant == 'BL2':
            return Channels.TEMPERATURE_SENSORS_BL2
        return Channels.TEMPERATURE_SENSORS_ORIGINAL

    @staticmethod
    def detect_variant(sensor_data: dict) -> str:
        """Detect device variant from available sensor data.

        Args:
            sensor_data: Telemetry dictionary from datasource

        Returns:
            'BL2' if BL2-specific sensors detected, 'ORIGINAL' otherwise
        """
        # Check for BL2-specific sensors (HPWR sense lines or HPWR
        # temperature sensors)
        # Power sensors use keys like 'HPWR1_W1_Power', temperature sensors use
        # 'TS_HPWR1_IN'
        bl2_indicators = [
            'HPWR1_W1_Power', 'HPWR1_W2_Power',  # BL2 power sense lines
            'TS_HPWR1_IN', 'TS_HPWR1_OUT',  # BL2 temperature sensors
            'TS_HPWR2_IN', 'TS_HPWR2_OUT',
        ]
        for key in bl2_indicators:
            if key in sensor_data:
                return 'BL2'
        return 'ORIGINAL'


# ═══════════════════════════════════════════════════════════════════════════════
#  UI Layout Configuration
# ═══════════════════════════════════════════════════════════════════════════════

class Layout:
    """UI layout constants and positioning."""

    # Progress bar dimensions
    BAR_WIDTH = 20              # Character width of progress bars

    # Fleet tab column positions and widths
    FLEET_COLS = {
        'SEL': 2,               # Selection cursor column
        'MODEL': 5,             # Model (BL1/BL2) column
        'PORT': 11,             # Port name column
        'FW': 31,               # Firmware column
        'UID': 44,              # UID column
        'STATUS': 72,           # Status column
        'ACTIVE': 86,           # Active indicator column
    }

    FLEET_WIDTHS = {
        'MODEL': 5,             # Model width ("BL1"/"BL2")
        'PORT': 18,             # Port name width
        'FW': 11,               # Firmware width ("0x" + 8 hex digits)
        'UID': 26,              # UID width
        'STATUS': 12,           # Status width
    }

    # Fan tab column positions
    FAN_COLS = {
        'NAME': 2,              # Fan name column
        'DUTY': 10,             # Duty percentage column
        'RPM': 17,              # RPM column
        'ENABLED': 25,          # Enable status column
        'BAR': 30,              # Progress bar column
        'STATS': 52,            # Statistics column
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Help Text
# ═══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = [
    "BENCHLAB TUI — Help",
    "─" * 44,
    "",
    "Navigation",
    "  ← / →  or  h / l    Switch tabs",
    "  0 – 7               Jump to tab directly",
    "  q / Q               Quit",
    "  ?                   This help",
    "",
    "Fleet tab (0)",
    "  ↑ / ↓               Highlight device",
    "  Enter               Connect to highlighted device",
    "  f                   Re-scan fleet",
    "",
    "System tab (2)",
    "  Summary: SYS/CPU/GPU/MB power",
    "  Power / Current / Voltage per rail",
    "",
    "Motherboard tab (3)",
    "  Motherboard rails: 3.3V, 5V, 5VSB, 12V",
    "  Power / Current / Voltage per rail",
    "",
    "Voltage tab (5)",
    "  Board: Vdd, Vref",
    "  Measurements: VIN_0 to VIN_12",
    "",
    "Temperature tab (6)",
    "  Board: chip temp",
    "  System: ambient temp & humidity",
    "  Sensors: Sensor_1 to Sensor_4",
    "",
    "Global",
    "  r / R               Reset min/max/avg stats",
    "",
    "Colour key",
    "  Green               OK / Connected / Normal",
    "  Red                 Error / Disconnected / High temp",
    "  Yellow              Caution / Power data",
    "  Cyan                Temperature / Fan data",
    "  Blue                Voltage / Info",
    "",
    "Press any key to close",
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Status Determination Functions
# ═══════════════════════════════════════════════════════════════════════════════

def get_voltage_status(
        voltage: float, band: Tuple[float, float]) -> Tuple[str, int]:
    """Determine voltage status and color.

    Args:
        voltage: Voltage value
        band: (low_threshold, high_threshold) tuple

    Returns:
        Tuple of (status_string, color_pair_index)
    """
    if voltage == 0:
        return 'N/A', COLOR_PAIRS['default']
    elif voltage < band[0]:
        return 'LOW ', COLOR_PAIRS['error']
    elif voltage > band[1]:
        return 'HIGH', COLOR_PAIRS['caution']
    else:
        return 'OK  ', COLOR_PAIRS['ok']


def get_thermal_status(temperature: float) -> Tuple[str, int]:
    """Determine thermal status and color.

    Args:
        temperature: Temperature in Celsius

    Returns:
        Tuple of (status_string, color_pair_index)
    """
    if temperature <= ThermalStatus.NORMAL_MAX:
        return 'NORMAL', COLOR_PAIRS['ok']
    elif temperature <= ThermalStatus.WARNING_MAX:
        return 'WARNING', COLOR_PAIRS['caution']
    else:
        return 'CRITICAL', COLOR_PAIRS['error']


def get_temperature_color(temperature: float, warn_threshold: float) -> int:
    """Get color pair for temperature display.

    Args:
        temperature: Temperature value (may be None)
        warn_threshold: Warning threshold

    Returns:
        Color pair index
    """
    if temperature is None or temperature <= 0:
        return COLOR_PAIRS['info']
    elif temperature < warn_threshold:
        return COLOR_PAIRS['ok']
    else:
        return COLOR_PAIRS['error']


# ═══════════════════════════════════════════════════════════════════════════════
#  Configuration Access
# ═══════════════════════════════════════════════════════════════════════════════

class Config:
    """Main configuration class providing access to all settings."""

    # Class references for easy access
    BarScales = BarScales
    VoltageBands = VoltageBands
    ThermalStatus = ThermalStatus
    Channels = Channels
    Layout = Layout

    # Direct constants
    MIN_TERMINAL_ROWS = MIN_TERMINAL_ROWS
    MIN_TERMINAL_COLS = MIN_TERMINAL_COLS
    TAB_NAMES = TAB_NAMES
    STAT_COLUMN_WIDTH = STAT_COLUMN_WIDTH
    COLOR_PAIRS = COLOR_PAIRS
    HELP_TEXT = HELP_TEXT

    @staticmethod
    def init_colors():
        """Initialize curses color pairs."""
        init_color_pairs()

    @staticmethod
    def get_voltage_status(
            voltage: float, band: Tuple[float, float]) -> Tuple[str, int]:
        """Get voltage status and color."""
        return get_voltage_status(voltage, band)

    @staticmethod
    def get_thermal_status(temperature: float) -> Tuple[str, int]:
        """Get thermal status and color."""
        return get_thermal_status(temperature)

    @staticmethod
    def get_temperature_color(
            temperature: float,
            warn_threshold: float) -> int:
        """Get temperature display color."""
        return get_temperature_color(temperature, warn_threshold)
