"""
BenchLab Core - Infrastructure layer

Provides:
- DataSource abstraction for telemetry consumption (direct / FastAPI / MQTT)
- DeviceRegistry singleton for device lifecycle tracking
- ProcessManager singleton for infrastructure service management
- InfrastructureManager for higher-level orchestration
"""

from benchlab.core.datasource import (
    DataSource,
    DirectDataSource,
    FastAPIDataSource,
    MQTTDataSource,
    create_datasource,
)
from benchlab.core.device_registry import DeviceRegistry, DeviceInfo
from benchlab.core.process_manager import ProcessManager, ManagedProcess

__version__ = "2.0.0"

__all__ = [
    "DataSource",
    "DirectDataSource",
    "FastAPIDataSource",
    "MQTTDataSource",
    "create_datasource",
    "DeviceRegistry",
    "DeviceInfo",
    "ProcessManager",
    "ManagedProcess",
]
