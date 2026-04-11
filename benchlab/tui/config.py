"""
Configuration Module for TUI

Centralizes all configuration constants, scales, thresholds, and UI settings
for easy customization and maintenance.
"""

import curses
from typing import Dict, Tuple, Any


# ═══════════════════════════════════════════════════════════════════════════════
#  Display Configuration
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum terminal size requirements
MIN_TERMINAL_ROWS = 35
MIN_TERMINAL_COLS = 100

# Tab configuration
TAB_NAMES = ["Fleet", "Device", "System", "Voltage", "Temperature", "Fans"]

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
    curses.init_pair(COLOR_PAIRS['header'], curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(COLOR_PAIRS['ok'], curses.COLOR_GREEN, -1)
    curses.init_pair(COLOR_PAIRS['error'], curses.COLOR_RED, -1)
    curses.init_pair(COLOR_PAIRS['caution'], curses.COLOR_YELLOW, -1)
    curses.init_pair(COLOR_PAIRS['info'], curses.COLOR_CYAN, -1)
    curses.init_pair(COLOR_PAIRS['voltage'], curses.COLOR_BLUE, -1)
    curses.init_pair(COLOR_PAIRS['default'], curses.COLOR_WHITE, -1)
    curses.init_pair(COLOR_PAIRS['highlight'], curses.COLOR_BLACK, curses.COLOR_CYAN)


# ═══════════════════════════════════════════════════════════════════════════════
#  Progress Bar Scales and Thresholds
# ═══════════════════════════════════════════════════════════════════════════════

class BarScales:
    """Configuration for progress bar scales and thresholds."""
    
    # Power scaling (Watts)
    POWER_MAX = 1000.0              # Fixed bar ceiling (W); set to None for auto-scale
    POWER_AUTO_FLOOR = 10.0         # Minimum ceiling when auto-scaling
    
    # Current scaling (Amps) 
    CURRENT_MAX = 100.0             # Fixed bar ceiling (A); set to None for auto-scale
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
    """Telemetry channel definitions and groupings."""
    
    # Power summary channels (Tab 2 summary section)
    POWER_SUMMARY = [
        ('SYS_Power', 'SYS Power'),
        ('CPU_Power', 'CPU Power'), 
        ('GPU_Power', 'GPU Power'),
        ('MB_Power', 'MB Power'),
    ]
    
    # Rail channels for power/current/voltage (shared across System tab)
    RAIL_CHANNELS = [
        ('EPS1', 'EPS_1'),
        ('EPS2', 'EPS_2'),
        ('PCIE8_1', 'PCIE8_1'),
        ('PCIE8_2', 'PCIE8_2'),
        ('PCIE8_3', 'PCIE8_3'),
        ('HPWR1', '12V_HPWR_1'),
        ('HPWR2', '12V_HPWR_2'),
    ]
    
    # Board voltage channels (Voltage tab)
    BOARD_VOLTAGES = [
        ('Vdd', 'Vdd', VoltageBands.VDD),
        ('Vref', 'Vref', VoltageBands.VREF),
    ]
    
    # VIN voltage measurement channels (Voltage tab)
    VIN_CHANNELS = [f'VIN_{i}' for i in range(13)]  # VIN_0 through VIN_12
    
    # Temperature channels
    TEMPERATURE_SENSORS = [f'Temp_Sensor_{i+1}' for i in range(4)]  # Temp_Sensor_1 to 4
    
    # System environment channels
    ENVIRONMENT_CHANNELS = [
        ('Chip_Temp', 'Chip Temp'),
        ('Ambient_Temp', 'Ambient Temp'),
        ('Humidity', 'Humidity'),
    ]


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
        'PORT': 5,              # Port name column
        'FW': 25,               # Firmware column
        'UID': 38,              # UID column
        'STATUS': 66,           # Status column
        'ACTIVE': 80,           # Active indicator column
    }
    
    FLEET_WIDTHS = {
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
    "  0 – 5               Jump to tab directly",
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
    "Voltage tab (3)",
    "  Board: Vdd, Vref",
    "  Measurements: VIN_0 to VIN_12",
    "",
    "Temperature tab (4)",
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

def get_voltage_status(voltage: float, band: Tuple[float, float]) -> Tuple[str, int]:
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
    def get_voltage_status(voltage: float, band: Tuple[float, float]) -> Tuple[str, int]:
        """Get voltage status and color."""
        return get_voltage_status(voltage, band)
    
    @staticmethod
    def get_thermal_status(temperature: float) -> Tuple[str, int]:
        """Get thermal status and color."""
        return get_thermal_status(temperature)
    
    @staticmethod
    def get_temperature_color(temperature: float, warn_threshold: float) -> int:
        """Get temperature display color."""
        return get_temperature_color(temperature, warn_threshold)