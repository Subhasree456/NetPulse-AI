"""Validation schemas for host telemetry and remote agents."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    hostname: str | None = Field(default=None, max_length=255)
    ip_address: str | None = Field(default=None, max_length=64)
    os_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Agent name must contain at least two characters")
        return cleaned

    @field_validator("ip_address")
    @classmethod
    def clean_ip(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        import ipaddress

        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError as exc:
            raise ValueError("Enter a valid IPv4 or IPv6 address") from exc


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class AgentRegistrationResponse(BaseModel):
    agent: dict
    token: str
    warning: str


class AgentTelemetry(BaseModel):
    """Payload accepted from an authenticated agent.

    Missing metrics stay ``None``. Nothing is imputed. The backend records the
    server receive time as ``recorded_at`` and preserves the agent clock in
    ``agent_recorded_at`` so clock drift is visible.
    """

    model_config = ConfigDict(extra="ignore")

    hostname: str | None = Field(default=None, max_length=255)
    os_name: str | None = Field(default=None, max_length=120)
    agent_version: str | None = Field(default=None, max_length=40)
    recorded_at: datetime | None = None
    cpu_pct: float | None = Field(default=None, ge=0, le=100)
    memory_pct: float | None = Field(default=None, ge=0, le=100)
    disk_pct: float | None = Field(default=None, ge=0, le=100)
    swap_pct: float | None = Field(default=None, ge=0, le=100)
    load_1: float | None = Field(default=None, ge=0, le=100000)
    load_5: float | None = Field(default=None, ge=0, le=100000)
    load_15: float | None = Field(default=None, ge=0, le=100000)
    uptime_seconds: float | None = Field(default=None, ge=0)
    process_count: int | None = Field(default=None, ge=0, le=1000000)
    temperature_c: float | None = Field(default=None, ge=-50, le=200)
    bytes_sent_total: int | None = Field(default=None, ge=0)
    bytes_recv_total: int | None = Field(default=None, ge=0)
    upload_bps: float | None = Field(default=None, ge=0)
    download_bps: float | None = Field(default=None, ge=0)
    net_utilization_pct: float | None = Field(default=None, ge=0, le=100)
    latency_ms: float | None = Field(default=None, ge=0)
    packet_loss_pct: float | None = Field(default=None, ge=0, le=100)
    status: Literal["ok", "partial", "error"] = "ok"
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def limit_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 40:
            raise ValueError("details may contain at most 40 entries")
        return value

    @model_validator(mode="after")
    def require_some_measurement(self) -> "AgentTelemetry":
        numeric = (
            self.cpu_pct,
            self.memory_pct,
            self.disk_pct,
            self.upload_bps,
            self.download_bps,
            self.latency_ms,
            self.packet_loss_pct,
        )
        if all(item is None for item in numeric):
            raise ValueError("At least one measured metric is required")
        return self


class AgentHeartbeat(BaseModel):
    hostname: str | None = Field(default=None, max_length=255)
    agent_version: str | None = Field(default=None, max_length=40)
    status: str = Field(default="online", max_length=20)
    details: dict[str, Any] = Field(default_factory=dict)
