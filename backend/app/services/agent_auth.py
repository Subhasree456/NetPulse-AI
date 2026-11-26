"""Authentication and serialisation helpers for remote monitoring agents.

Agents authenticate with a per-agent bearer token. Only the SHA-256 hash is
stored, so a database leak does not expose usable credentials. Every ingest call
is rate limited per agent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.core.config import settings
from app.intelligence.common import iso
from app.models import Agent, HostMetric
from app.monitoring.host_agent import (
    agent_is_online,
    effective_status,
    hash_token,
    metric_as_dict,
)
from app.services.rate_limit import SlidingWindowLimiter

telemetry_limiter = SlidingWindowLimiter(settings.agent_telemetry_rate_limit_per_minute, 60)


def _extract_token(
    x_agent_token: str | None,
    authorization: str | None,
) -> str | None:
    if x_agent_token:
        return x_agent_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_agent(
    db: DbSession,
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Agent:
    token = _extract_token(x_agent_token, authorization)
    if not token or len(token) < 12:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid agent token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    agent = db.scalar(select(Agent).where(Agent.token_hash == hash_token(token)))
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent token was not recognised",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not agent.is_active:
        raise HTTPException(status_code=403, detail="This agent has been disabled")
    allowed, remaining = telemetry_limiter.allow(f"agent:{agent.id}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Agent telemetry rate limit exceeded; retry in a minute",
            headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"},
        )
    agent.token_last_used_at = datetime.now(timezone.utc)
    db.flush()
    return agent


AgentAuth = Annotated[Agent, Depends(require_agent)]


def latest_metric(db: Session, agent_id: int) -> HostMetric | None:
    return db.scalar(
        select(HostMetric)
        .where(HostMetric.agent_id == agent_id)
        .order_by(HostMetric.recorded_at.desc())
        .limit(1)
    )


def agent_as_dict(
    db: Session,
    agent: Agent,
    *,
    include_latest: bool = True,
    include_token_prefix: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)
    latest = latest_metric(db, agent.id) if include_latest else None
    status_value = effective_status(agent, now)
    data: dict = {
        "id": agent.id,
        "name": agent.name,
        "kind": agent.kind,
        "hostname": agent.hostname,
        "ip_address": agent.ip_address,
        "os_name": agent.os_name,
        "agent_version": agent.agent_version,
        "status": status_value,
        "online": agent_is_online(agent, now),
        "last_seen_at": iso(agent.last_seen_at),
        "token_last_used_at": iso(agent.token_last_used_at),
        "is_active": agent.is_active,
        "notes": agent.notes,
        "metadata": agent.metadata_json or {},
        "created_at": iso(agent.created_at),
        "latest": metric_as_dict(latest),
        "telemetry_interval_seconds": settings.host_metric_interval_seconds,
        "offline_after_seconds": settings.host_offline_after_seconds,
    }
    if include_token_prefix:
        data["token_prefix"] = agent.token_prefix
    if latest is not None:
        recorded = latest.recorded_at
        if recorded is not None and recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        data["seconds_since_metric"] = (
            int((now - recorded).total_seconds()) if recorded is not None else None
        )
    else:
        data["seconds_since_metric"] = None
    return data
