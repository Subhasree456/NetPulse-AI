"""WebSocket live feed with a clearly documented polling fallback.

A persistent WebSocket needs a long-lived process. Local uvicorn deployments get
server-pushed snapshots; serverless deployments (for example Vercel Functions)
cannot hold the socket, so the frontend automatically falls back to its existing
polling hooks. The socket is read-only and authenticated with the same JWT.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import decode_access_token
from app.intelligence.common import aware, iso
from app.models import (
    Agent,
    AnomalyEvent,
    HostMetric,
    IssueTicket,
    NetworkMeasurement,
    Notification,
    OptimizationRecommendation,
    PredictionResult,
    SecurityEvent,
    TrafficSample,
    User,
)
from app.monitoring.host_agent import effective_status
from app.monitoring.scheduler import scheduler

logger = logging.getLogger("netpulse.live")
router = APIRouter(prefix="/live", tags=["Live Feed"])
PUSH_SECONDS = 5


def _snapshot() -> dict:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        latest = db.scalar(
            select(NetworkMeasurement)
            .where(NetworkMeasurement.data_origin == "live")
            .order_by(NetworkMeasurement.recorded_at.desc())
            .limit(1)
        )
        open_tickets = (
            db.scalar(
                select(func.count(IssueTicket.id)).where(
                    IssueTicket.status.in_(("Open", "In Progress"))
                )
            )
            or 0
        )
        unread = (
            db.scalar(
                select(func.count(Notification.id)).where(Notification.is_read.is_(False))
            )
            or 0
        )
        security_open = (
            db.scalar(
                select(func.count(SecurityEvent.id)).where(
                    SecurityEvent.status.in_(("Open", "Acknowledged"))
                )
            )
            or 0
        )
        optimization_open = (
            db.scalar(
                select(func.count(OptimizationRecommendation.id)).where(
                    OptimizationRecommendation.status == "Advisory"
                )
            )
            or 0
        )
        anomaly_recent = (
            db.scalar(
                select(func.count(AnomalyEvent.id)).where(
                    AnomalyEvent.detected_at >= now - timedelta(hours=1)
                )
            )
            or 0
        )
        highest = db.scalar(
            select(PredictionResult)
            .where(PredictionResult.is_active.is_(True))
            .order_by(PredictionResult.risk_score.desc())
            .limit(1)
        )
        agents = list(db.scalars(select(Agent).order_by(Agent.id.asc())).all())
        agent_online = sum(1 for agent in agents if effective_status(agent, now) == "online")
        local = next((agent for agent in agents if agent.kind == "local"), None)
        host_metric = (
            db.scalar(
                select(HostMetric)
                .where(HostMetric.agent_id == local.id)
                .order_by(HostMetric.recorded_at.desc())
                .limit(1)
            )
            if local
            else None
        )
        traffic = db.scalar(
            select(TrafficSample)
            .where(TrafficSample.data_origin == "live")
            .order_by(TrafficSample.recorded_at.desc())
            .limit(1)
        )
        active_incidents = db.scalar(
            select(func.count(IssueTicket.id)).where(
                IssueTicket.status.in_(("Open", "In Progress"))
            )
        ) or 0
        return {
            "type": "snapshot",
            "generated_at": now.isoformat(),
            "measurement": (
                {
                    "latency_ms": latest.latency_ms,
                    "packet_loss_pct": latest.packet_loss_pct,
                    "jitter_ms": latest.jitter_ms,
                    "is_online": latest.is_online,
                    "probe_target": latest.probe_target,
                    "recorded_at": iso(latest.recorded_at),
                    "age_seconds": int(
                        (now - aware(latest.recorded_at)).total_seconds()
                    )
                    if latest.recorded_at
                    else None,
                }
                if latest
                else None
            ),
            "host": (
                {
                    "agent_id": local.id if local else None,
                    "cpu_pct": host_metric.cpu_pct,
                    "memory_pct": host_metric.memory_pct,
                    "disk_pct": host_metric.disk_pct,
                    "temperature_c": host_metric.temperature_c,
                    "process_count": host_metric.process_count,
                    "upload_bps": host_metric.upload_bps,
                    "download_bps": host_metric.download_bps,
                    "net_utilization_pct": host_metric.net_utilization_pct,
                    "status": host_metric.status,
                    "recorded_at": iso(host_metric.recorded_at),
                    "age_seconds": int((now - aware(host_metric.recorded_at)).total_seconds())
                    if host_metric.recorded_at
                    else None,
                }
                if host_metric
                else None
            ),
            "traffic": (
                {
                    "bytes_per_sec": traffic.bytes_per_sec,
                    "utilization_pct": traffic.utilization_pct,
                    "recorded_at": iso(traffic.recorded_at),
                }
                if traffic
                else None
            ),
            "agents": {
                "total": len(agents),
                "online": agent_online,
                "offline": len(agents) - agent_online,
            },
            "open_tickets": open_tickets,
            "active_incidents": active_incidents,
            "unread_notifications": unread,
            "security_open": security_open,
            "optimization_open": optimization_open,
            "anomalies_last_hour": anomaly_recent,
            "highest_risk": (
                {
                    "score": round(highest.risk_score, 1),
                    "severity": highest.severity,
                    "scope": highest.scope,
                    "access_point_id": highest.access_point_id,
                }
                if highest
                else None
            ),
            "monitoring": {
                "enabled": bool(settings.monitoring_enabled),
                "host_enabled": bool(settings.host_monitoring_enabled),
                "last_cycle_at": iso(scheduler.last_cycle_at),
                "last_host_collection_at": iso(scheduler.last_host_collection_at),
                "last_network_probe_at": iso(scheduler.last_network_probe_at),
                "last_error": scheduler.last_error,
            },
        }


@router.get("/status")
def live_status(_: CurrentUser) -> dict:
    return {
        "transport": "websocket",
        "path": f"{settings.api_prefix}/live/ws",
        "push_interval_seconds": PUSH_SECONDS,
        "persistent_process_required": True,
        "polling_fallback": True,
        "message": (
            "Live snapshots are pushed over WebSocket in long-lived deployments. "
            "Serverless deployments cannot hold a socket; the dashboard then polls "
            "the REST API automatically."
        ),
    }


@router.get("/snapshot")
def live_snapshot(_: CurrentUser) -> dict:
    """REST equivalent of one WebSocket push, used when sockets are unavailable."""
    return _snapshot()


@router.websocket("/ws")
async def live_socket(websocket: WebSocket, token: str | None = Query(default=None)) -> None:
    if not token:
        await websocket.accept()
        await websocket.close(code=4401)
        return
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub", "0"))
    except Exception:
        await websocket.accept()
        await websocket.close(code=4401)
        return
    with SessionLocal() as db:
        user = db.get(User, user_id)
        active = bool(user and user.is_active)
    if not active:
        await websocket.accept()
        await websocket.close(code=4403)
        return

    await websocket.accept()
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=PUSH_SECONDS)
                if message.strip().lower() == "ping":
                    await websocket.send_json(
                        {"type": "pong", "generated_at": datetime.now(timezone.utc).isoformat()}
                    )
                    continue
            except asyncio.TimeoutError:
                pass
            snapshot = await asyncio.to_thread(_snapshot)
            await websocket.send_json(snapshot)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Live socket closed", exc_info=True)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
