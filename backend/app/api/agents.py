"""Remote monitoring agent management and authenticated telemetry ingest.

Security model
--------------
* Only an administrator can register, disable or rotate an agent token.
* The plaintext token is returned exactly once and only its SHA-256 hash is
  stored.
* Every ingest request is rate limited per agent and requires an active token.
* Telemetry is stored with its real receive timestamp; missing values remain
  NULL rather than being imputed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.config import settings
from app.intelligence.common import iso
from app.models import Agent, HostMetric
from app.monitoring.host_agent import (
    REMOTE_KIND,
    metric_as_dict,
    new_agent_token,
    record_host_metric,
)
from app.schemas.agent import (
    AgentCreate,
    AgentHeartbeat,
    AgentRegistrationResponse,
    AgentTelemetry,
    AgentUpdate,
)
from app.services.agent_auth import AgentAuth, agent_as_dict, latest_metric
from app.services.notifications import notify
from app.services.ticketing import evaluate_host_metric_ticket

router = APIRouter(prefix="/agents", tags=["Monitoring Agents"])


def _require_remote(agent: Agent) -> None:
    if agent.kind != REMOTE_KIND:
        raise HTTPException(
            status_code=400,
            detail="The local system agent is managed automatically and cannot be edited here",
        )


@router.get("")
def list_agents(db: DbSession, _: CurrentUser) -> dict:
    agents = db.scalars(select(Agent).order_by(Agent.kind.asc(), Agent.id.asc())).all()
    items = [agent_as_dict(db, agent, include_token_prefix=True) for agent in agents]
    online = sum(1 for item in items if item["status"] == "online")
    return {
        "agents": items,
        "summary": {
            "total": len(items),
            "online": online,
            "offline": sum(1 for item in items if item["status"] == "offline"),
            "never_reported": sum(1 for item in items if item["status"] == "never"),
            "disabled": sum(1 for item in items if item["status"] == "disabled"),
            "local": sum(1 for item in items if item["kind"] == "local"),
            "remote": sum(1 for item in items if item["kind"] == REMOTE_KIND),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Agents push real telemetry to /api/agents/telemetry. Offline means no "
            "heartbeat or telemetry within the configured stale window; it never "
            "implies fabricated data."
        ),
    }


@router.post("", response_model=AgentRegistrationResponse, status_code=201)
def register_agent(payload: AgentCreate, db: DbSession, admin: AdminUser) -> AgentRegistrationResponse:
    existing = db.scalar(select(Agent).where(Agent.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="An agent with this name already exists")
    token, token_hash, prefix = new_agent_token()
    agent = Agent(
        name=payload.name,
        kind=REMOTE_KIND,
        hostname=payload.hostname,
        ip_address=payload.ip_address,
        os_name=payload.os_name,
        agent_version=None,
        token_hash=token_hash,
        token_prefix=prefix,
        status="never",
        is_active=True,
        notes=payload.notes,
        metadata_json={"authorized": True, "registration": "admin"},
        created_by=admin.id,
    )
    db.add(agent)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An agent with this name or token already exists") from None
    db.refresh(agent)
    return AgentRegistrationResponse(
        agent=agent_as_dict(db, agent, include_token_prefix=True),
        token=token,
        warning=(
            "Copy this token now — it is shown once and never stored in plaintext. "
            "Send it as the X-Agent-Token header from the remote agent."
        ),
    )


@router.post("/telemetry")
def ingest_telemetry(payload: AgentTelemetry, db: DbSession, agent: AgentAuth) -> dict:
    if agent.kind != REMOTE_KIND:
        raise HTTPException(status_code=400, detail="Use /api/host/collect for the local agent")
    if payload.hostname:
        agent.hostname = payload.hostname
    if payload.os_name:
        agent.os_name = payload.os_name
    if payload.agent_version:
        agent.agent_version = payload.agent_version

    snapshot = {
        "cpu_pct": payload.cpu_pct,
        "memory_pct": payload.memory_pct,
        "disk_pct": payload.disk_pct,
        "swap_pct": payload.swap_pct,
        "load_1": payload.load_1,
        "load_5": payload.load_5,
        "load_15": payload.load_15,
        "uptime_seconds": payload.uptime_seconds,
        "process_count": payload.process_count,
        "temperature_c": payload.temperature_c,
        "bytes_sent_total": payload.bytes_sent_total,
        "bytes_recv_total": payload.bytes_recv_total,
        "upload_bps": payload.upload_bps,
        "download_bps": payload.download_bps,
        "net_utilization_pct": payload.net_utilization_pct,
        "latency_ms": payload.latency_ms,
        "packet_loss_pct": payload.packet_loss_pct,
        "status": payload.status,
        "details": {**(payload.details or {}), "ingest": "remote-agent"},
    }
    metric = record_host_metric(
        db,
        agent,
        snapshot,
        source="agent",
        data_origin="live",
        agent_recorded_at=payload.recorded_at,
    )
    ticket = evaluate_host_metric_ticket(db, agent, metric)
    if ticket is not None:
        notify(
            db,
            kind="ticket",
            severity=ticket.priority,
            title=f"New host incident · {ticket.ticket_number}",
            message=ticket.title,
            path="/incidents",
            dedupe_key=f"host-ticket:{ticket.id}",
        )
    db.commit()
    db.refresh(metric)
    return {
        "accepted": True,
        "agent_id": agent.id,
        "metric": metric_as_dict(metric),
        "incident_created": ticket is not None,
        "incident_id": ticket.id if ticket else None,
        "received_at": iso(metric.recorded_at),
        "next_expected_seconds": settings.host_metric_interval_seconds,
    }


@router.post("/heartbeat")
def heartbeat(payload: AgentHeartbeat, db: DbSession, agent: AgentAuth) -> dict:
    if agent.kind != REMOTE_KIND:
        raise HTTPException(status_code=400, detail="The local agent reports through host collection")
    now = datetime.now(timezone.utc)
    agent.last_seen_at = now
    agent.status = "online"
    if payload.hostname:
        agent.hostname = payload.hostname
    if payload.agent_version:
        agent.agent_version = payload.agent_version
    if payload.details:
        agent.metadata_json = {**(agent.metadata_json or {}), "last_heartbeat": payload.details}
    db.commit()
    return {
        "ok": True,
        "agent_id": agent.id,
        "status": "online",
        "server_time": now.isoformat(),
        "collection_interval_seconds": settings.host_metric_interval_seconds,
        "telemetry_path": f"{settings.api_prefix}/agents/telemetry",
        "heartbeat_path": f"{settings.api_prefix}/agents/heartbeat",
    }


@router.get("/me")
def agent_identity(db: DbSession, agent: AgentAuth) -> dict:
    return {
        "agent": agent_as_dict(db, agent, include_token_prefix=True),
        "collection_interval_seconds": settings.host_metric_interval_seconds,
        "endpoints": {
            "telemetry": f"{settings.api_prefix}/agents/telemetry",
            "heartbeat": f"{settings.api_prefix}/agents/heartbeat",
        },
    }


@router.get("/{agent_id}")
def get_agent(agent_id: int, db: DbSession, _: CurrentUser) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent_as_dict(db, agent, include_token_prefix=True)


@router.patch("/{agent_id}")
def update_agent(agent_id: int, payload: AgentUpdate, db: DbSession, _: AdminUser) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    _require_remote(agent)
    if payload.name is not None:
        clash = db.scalar(select(Agent).where(Agent.name == payload.name, Agent.id != agent.id))
        if clash is not None:
            raise HTTPException(status_code=409, detail="An agent with this name already exists")
        agent.name = payload.name
    if payload.notes is not None:
        agent.notes = payload.notes
    if payload.is_active is not None:
        agent.is_active = payload.is_active
        agent.status = "online" if payload.is_active else "disabled"
    db.commit()
    db.refresh(agent)
    return agent_as_dict(db, agent, include_token_prefix=True)


@router.post("/{agent_id}/rotate-token", response_model=AgentRegistrationResponse)
def rotate_token(agent_id: int, db: DbSession, _: AdminUser) -> AgentRegistrationResponse:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    _require_remote(agent)
    token, token_hash, prefix = new_agent_token()
    agent.token_hash = token_hash
    agent.token_prefix = prefix
    db.commit()
    db.refresh(agent)
    return AgentRegistrationResponse(
        agent=agent_as_dict(db, agent, include_token_prefix=True),
        token=token,
        warning="The previous token is now invalid. Copy this replacement token once.",
    )


@router.delete("/{agent_id}")
def delete_agent(agent_id: int, db: DbSession, _: AdminUser) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    _require_remote(agent)
    name = agent.name
    db.delete(agent)
    db.commit()
    return {"deleted": True, "agent_id": agent_id, "name": name}


@router.get("/{agent_id}/metrics")
def agent_metrics(
    agent_id: int,
    db: DbSession,
    _: CurrentUser,
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = list(
        db.scalars(
            select(HostMetric)
            .where(HostMetric.agent_id == agent_id, HostMetric.recorded_at >= cutoff)
            .order_by(HostMetric.recorded_at.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    samples = [metric_as_dict(row) for row in rows]
    return {
        "agent": agent_as_dict(db, agent),
        "samples": samples,
        "hours": hours,
        "count": len(samples),
        "latest": samples[-1] if samples else None,
        "empty": not samples,
        "message": (
            None
            if samples
            else "This agent has not reported any telemetry in the selected window."
        ),
    }
