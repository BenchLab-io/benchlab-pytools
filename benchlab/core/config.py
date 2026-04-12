"""Configuration models for Benchlab data sources.

This module defines Pydantic ``BaseModel`` classes that validate the
configuration parameters required by each concrete ``DataSource``
implementation.  Using a strict schema helps catch mis‑configurations
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
        The serial port to connect to. ``None`` triggers auto‑detection.
    poll_interval: float
        Seconds between successive sensor reads. Must be positive.
    """

    port: Optional[str] = None
    poll_interval: float = Field(default=1.0, gt=0, description="Polling interval in seconds")


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
    timeout: float = Field(default=5.0, gt=0, description="Request timeout in seconds")

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
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

    broker: str = Field(default="localhost", description="MQTT broker hostname")
    port: int = Field(default=1883, ge=1, le=65535, description="Broker port")
    topic_prefix: str = Field(default="benchlab", description="Base topic prefix")
    timeout: float = Field(default=5.0, gt=0, description="Connection timeout in seconds")
