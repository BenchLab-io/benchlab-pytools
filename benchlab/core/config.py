"""Configuration models for Benchlab data sources.

This module defines Pydantic ``BaseModel`` classes that validate the
configuration parameters required by each concrete ``DataSource``
implementation.  Using a strict schema helps catch mis-configurations
early (e.g. missing serial port, invalid MQTT broker address) and makes
the library easier to use from external tools.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class SerialConfig(BaseModel):
    """Configuration for :class:`DirectDataSource`.

    Attributes
    ----------
    port: Optional[str]
        The serial port to connect to. ``None`` triggers auto-detection.
    poll_interval: float
        Seconds between successive sensor reads. Must be positive.
    """

    port: Optional[str] = None
    poll_interval: float = Field(
        default=1.0,
        gt=0,
        description="Polling interval in seconds")


class FastAPIConfig(BaseModel):
    """Configuration for :class:`FastAPIDataSource`.

    Attributes
    ----------
    base_url: str
        Base URL of the FastAPI server (e.g. ``http://127.0.0.1:8000``).
    timeout: float
        HTTP request timeout in seconds. Must be positive.
    """

    base_url: str = Field(..., description="Base URL of the FastAPI server")
    timeout: float = Field(
        default=5.0,
        gt=0,
        description="Request timeout in seconds")

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        """Ensure base_url has a protocol prefix and no trailing slash."""
        v = v.strip()
        if not v.startswith(('http://', 'https://')):
            v = f"http://{v}"
        return v.rstrip('/')


class MQTTConfig(BaseModel):
    """Configuration for :class:`MQTTDataSource`.

    Attributes
    ----------
    broker: str
        Hostname of the MQTT broker.
    port: int
        Network port of the broker (default 1883).
    topic_prefix: str
        Prefix used for all Benchlab topics.
    timeout: float
        Connection timeout in seconds.
    """

    broker: str = Field(
        default="localhost",
        description="MQTT broker hostname")
    port: int = Field(default=1883, ge=1, le=65535, description="Broker port")
    topic_prefix: str = Field(
        default="benchlab",
        description="Base topic prefix")
    timeout: float = Field(
        default=5.0,
        gt=0,
        description="Connection timeout in seconds")


class NamedPipeConfig(BaseModel):
    """Configuration for :class:`NamedPipeDataSource`.

    Windows-only. Connects to the C# BenchLab Windows service via
    named pipes (BenchlabDiscovery + per-device BenchlabSensorPipe_XX_YYY).

    Attributes
    ----------
    timeout: float
        Seconds to wait for a pipe connection before giving up.
    poll_interval: float
        Seconds between successive sensor reads.
    """

    timeout: float = Field(
        default=5.0,
        gt=0,
        description="Pipe connection timeout in seconds")
    poll_interval: float = Field(
        default=1.0,
        gt=0,
        description="Polling interval in seconds")


class ServiceHttpConfig(BaseModel):
    """Configuration for :class:`ServiceHttpDataSource`.

    Connects to the C# BenchLab service REST HTTP API.
    The service is auto-detected at port 8585 by default.

    Attributes
    ----------
    base_url: str
        Base URL of the C# BenchLab service
        (e.g. ``http://localhost:8585``).
    timeout: float
        HTTP request timeout in seconds. Must be positive.
    poll_interval: float
        Seconds between successive telemetry polls.
    """

    base_url: str = Field(
        default="http://localhost:8585",
        description="Base URL of the C# BenchLab service HTTP API",
    )
    timeout: float = Field(
        default=5.0,
        gt=0,
        description="Request timeout in seconds")
    poll_interval: float = Field(
        default=1.0,
        gt=0,
        description="Polling interval in seconds")

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip('/')


class ServiceWsConfig(BaseModel):
    """Configuration for :class:`ServiceWsDataSource`.

    Connects to the C# BenchLab service WebSocket event stream
    (``ws://localhost:8585/events``). The service pushes a ``hello`` frame
    on connect followed by ``telemetry`` and ``device`` frames.

    Attributes
    ----------
    url: str
        WebSocket URL of the C# BenchLab service event stream.
    token: Optional[str]
        Bearer token, sent as the ``X-Benchlab-Token`` header. Only needed
        when the service has ``ApiSettings:Token`` configured. ``None``
        (the loopback default) means no auth.
    timeout: float
        Seconds to wait for the connection to open and the first ``hello``
        frame. Must be positive.
    """

    url: str = Field(
        default="ws://localhost:8585/events",
        description="WebSocket URL of the C# BenchLab service event stream",
    )
    token: Optional[str] = Field(
        default=None,
        description="Optional X-Benchlab-Token value")
    timeout: float = Field(
        default=5.0,
        gt=0,
        description="Connection / first-hello timeout in seconds")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        """Normalise to a ws:// URL ending in /events."""
        v = v.strip()
        if v.startswith("https://"):
            v = "wss://" + v[len("https://"):]
        elif v.startswith("http://"):
            v = "ws://" + v[len("http://"):]
        elif not v.startswith(("ws://", "wss://")):
            v = f"ws://{v}"
        v = v.rstrip('/')
        if not v.endswith("/events"):
            v = f"{v}/events"
        return v
