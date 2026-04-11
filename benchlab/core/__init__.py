"""
BenchLab Core - Infrastructure layer

Provides:
- DataSource abstraction for telemetry consumption (direct / FastAPI / MQTT)
- DataSourceManager for unified datasource management with statistics
- DeviceRegistry singleton for device lifecycle tracking
- ProcessManager singleton for infrastructure service management
- ChannelStats for thread-safe telemetry statistics tracking
- InfrastructureManager for higher-level orchestration
"""

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
]
