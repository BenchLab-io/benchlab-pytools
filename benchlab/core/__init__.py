"""
BenchLab Core - Infrastructure layer

Provides:
- DataSource abstraction for telemetry consumption (direct / FastAPI / MQTT)
- DataSourceManager for unified datasource management with statistics
- DeviceRegistry singleton for device lifecycle tracking
- ProcessManager singleton for infrastructure service management
- ChannelStats for thread-safe telemetry statistics tracking
- InfrastructureManager for higher-level orchestration

PyCore v0.3.0 Multi-Variant Support:
- ORIGINAL (0x10): 11 power sensors, 4 temperature sensors
- BL2 / BENCHLAB 2 (0x11): 23 power sensors, 8 temperature sensors
"""

import logging

from benchlab.core.datasource import (
    DataSource,
    DirectDataSource,
    FastAPIDataSource,
    MQTTDataSource,
    create_datasource,
)
from benchlab.core.datasource_manager import DataSourceManager
from benchlab.core.device_registry import DeviceRegistry, DeviceInfo
from benchlab.core.process_manager import ProcessManager, ManagedProcess
from benchlab.core.statistics import (
    ChannelStats,
    StatsFormatter,
    create_stats_callback,
)

logger = logging.getLogger("benchlab.core")

# PyCore variant constants for tools that need to detect device capabilities
# Import from pycore to make them easily accessible to tools.
# PyCore >=0.4.1 renamed BENCHLAB_CFE_PRODUCT_ID to BENCHLAB_BL2_PRODUCT_ID
# (the "CFE" SKU is now marketed as BENCHLAB 2); fall back to the old name
# for older pycore installs.
try:
    from benchlab_pycore.core import BENCHLAB_ORIGINAL_PRODUCT_ID
    try:
        from benchlab_pycore.core import BENCHLAB_BL2_PRODUCT_ID
    except ImportError:
        from benchlab_pycore.core import (
            BENCHLAB_CFE_PRODUCT_ID as BENCHLAB_BL2_PRODUCT_ID)
except ImportError:
    logger.warning(
        "benchlab_pycore not installed; using fallback product ID constants")
    BENCHLAB_ORIGINAL_PRODUCT_ID = 0x10
    BENCHLAB_BL2_PRODUCT_ID = 0x11

__version__ = "2.0.0"

__all__ = [
    # DataSource layer
    "DataSource",
    "DirectDataSource",
    "FastAPIDataSource",
    "MQTTDataSource",
    "create_datasource",
    # Unified datasource management
    "DataSourceManager",
    # Statistics tracking
    "ChannelStats",
    "StatsFormatter",
    "create_stats_callback",
    # Device and process management
    "DeviceRegistry",
    "DeviceInfo",
    "ProcessManager",
    "ManagedProcess",
    # PyCore v0.3.0 variant constants
    "BENCHLAB_ORIGINAL_PRODUCT_ID",
    "BENCHLAB_BL2_PRODUCT_ID",
]
