"""Local system monitoring endpoints (real psutil telemetry)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import fmean

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AnalystUser, CurrentUser, DbSession
from app.core.config import settings
from app.intelligence.common import iso
from app.models import Agent, HostMetric, NetworkMeasurement, TrafficSample
from app.monitoring import internet
from app.monitoring.diagnostics import network_info
from app.monitoring.host_agent import (
    collect_host_snapshot,
    collect_local_metrics,
    ensure_local_agent,
    metric_as_dict,
)
from app.monitoring.scheduler import scheduler
from app.services.agent_auth import agent_as_dict, latest_metric
from app.services.settings import get_setting

router = APIRouter(prefix="/host", tags=["Local System"])


def _local_agent(db: DbSession) -> Agent:
    return ensure_local_agent(db)


def _series(db: DbSession, agent_id: int, hours: int, limit: int) -> list[dict]:
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
    return [metric_as_dict(row) for row in rows]


@router.get("/overview")
def host_overview(
    db: DbSession,
    _: CurrentUser,
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=720, ge=1, le=5000),
) -> dict:
    agent = _local_agent(db)
    db.commit()
    latest = latest_metric(db, agent.id)
    samples = _series(db, agent.id, hours, limit)
    values = lambda field: [  # noqa: E731
        float(row[field]) for row in samples if row.get(field) is not None
    ]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    network_rows = list(
        db.scalars(
            select(NetworkMeasurement)
            .where(
                NetworkMeasurement.data_origin == "live",
                NetworkMeasurement.recorded_at >= cutoff,
            )
            .order_by(NetworkMeasurement.recorded_at.asc())
            .limit(2000)
        ).all()
    )
    network_latest = db.scalar(
        select(NetworkMeasurement)
        .where(NetworkMeasurement.data_origin == "live")
        .order_by(NetworkMeasurement.recorded_at.desc())
        .limit(1)
    )
    traffic_rows = list(
        db.scalars(
            select(TrafficSample)
            .where(
                TrafficSample.data_origin == "live",
                TrafficSample.recorded_at >= cutoff,
            )
            .order_by(TrafficSample.recorded_at.asc())
            .limit(2000)
        ).all()
    )
    probes = internet.latest_probes(db)

    cpu = values("cpu_pct")
    memory = values("memory_pct")
    disk = values("disk_pct")
    upload = values("upload_bps")
    download = values("download_bps")
    latest_dict = metric_as_dict(latest)
    return {
        "agent": agent_as_dict(db, agent),
        "latest": latest_dict,
        "series": samples,
        "summary": {
            "cpu_pct": latest.cpu_pct if latest else None,
            "memory_pct": latest.memory_pct if latest else None,
            "disk_pct": latest.disk_pct if latest else None,
            "swap_pct": latest.swap_pct if latest else None,
            "temperature_c": latest.temperature_c if latest else None,
            "uptime_seconds": latest.uptime_seconds if latest else None,
            "process_count": latest.process_count if latest else None,
            "upload_bps": latest.upload_bps if latest else None,
            "download_bps": latest.download_bps if latest else None,
            "net_utilization_pct": latest.net_utilization_pct if latest else None,
            "average_cpu_pct": round(fmean(cpu), 2) if cpu else None,
            "average_memory_pct": round(fmean(memory), 2) if memory else None,
            "average_disk_pct": round(fmean(disk), 2) if disk else None,
            "average_upload_bps": round(fmean(upload), 2) if upload else None,
            "average_download_bps": round(fmean(download), 2) if download else None,
            "peak_cpu_pct": round(max(cpu), 2) if cpu else None,
            "peak_memory_pct": round(max(memory), 2) if memory else None,
            "samples": len(samples),
        },
        "thresholds": {
            "cpu_pct": float(get_setting(db, "cpu_threshold_pct", settings.cpu_threshold_pct)),
            "memory_pct": float(
                get_setting(db, "memory_threshold_pct", settings.memory_threshold_pct)
            ),
            "disk_pct": float(get_setting(db, "disk_threshold_pct", settings.disk_threshold_pct)),
            "latency_ms": float(get_setting(db, "latency_threshold_ms", settings.latency_threshold_ms)),
            "packet_loss_pct": float(
                get_setting(db, "packet_loss_threshold_pct", settings.packet_loss_threshold_pct)
            ),
        },
        "network": {
            "latest": (
                {
                    "id": network_latest.id,
                    "latency_ms": network_latest.latency_ms,
                    "packet_loss_pct": network_latest.packet_loss_pct,
                    "jitter_ms": network_latest.jitter_ms,
                    "is_online": network_latest.is_online,
                    "probe_target": network_latest.probe_target,
                    "recorded_at": iso(network_latest.recorded_at),
                }
                if network_latest
                else None
            ),
            "series": [
                {
                    "timestamp": iso(row.recorded_at),
                    "latency_ms": row.latency_ms,
                    "packet_loss_pct": row.packet_loss_pct,
                    "is_online": row.is_online,
                }
                for row in network_rows
            ],
            "probes": probes,
        },
        "traffic_series": [
            {
                "timestamp": iso(row.recorded_at),
                "bytes_per_sec": row.bytes_per_sec,
                "packets_per_sec": row.packets_per_sec,
                "utilization_pct": row.utilization_pct,
                "link_speed_mbps": row.link_speed_mbps,
            }
            for row in traffic_rows
        ],
        "system": network_info(),
        "monitoring": {
            "enabled": bool(settings.host_monitoring_enabled),
            "interval_seconds": settings.host_metric_interval_seconds,
            "last_cycle_at": iso(scheduler.last_cycle_at),
            "last_host_collection_at": iso(scheduler.last_host_collection_at),
            "last_network_probe_at": iso(scheduler.last_network_probe_at),
            "last_error": scheduler.last_error,
            "collection": "psutil",
        },
        "hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "empty": latest is None,
        "message": (
            None
            if latest is not None
            else (
                "No host telemetry has been stored yet. Start the backend scheduler or "
                "click Collect now. Values are never fabricated."
            )
        ),
        "disclaimer": (
            "CPU, memory, disk, process, temperature and interface counters are real "
            "measurements from this machine. Fields that the operating system does not "
            "expose are shown as not measured."
        ),
    }


@router.post("/collect")
def collect_now(db: DbSession, _: AnalystUser) -> dict:
    if settings.environment == "testing":
        raise HTTPException(
            status_code=409,
            detail="Live host collection is disabled in the test environment",
        )
    metric = collect_local_metrics(db)
    return {
        "collected": True,
        "metric": metric_as_dict(metric),
        "message": "A real psutil host observation was stored.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metrics")
def host_metrics(
    db: DbSession,
    _: CurrentUser,
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=1000, ge=1, le=5000),
    agent_id: int | None = Query(default=None, ge=1),
) -> dict:
    if agent_id is not None:
        agent = db.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
    else:
        agent = _local_agent(db)
        db.commit()
    samples = _series(db, agent.id, hours, limit)
    return {
        "agent": agent_as_dict(db, agent),
        "samples": samples,
        "hours": hours,
        "count": len(samples),
        "empty": not samples,
    }


@router.get("/processes")
def host_processes(_: CurrentUser) -> dict:
    snapshot = collect_host_snapshot()
    details = snapshot.get("details") or {}
    return {
        "processes": details.get("top_processes") or [],
        "interfaces": details.get("interfaces") or [],
        "disks": details.get("disks") or [],
        "connections": details.get("connections") or {},
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Read-only snapshot of this host's process, disk and socket tables. "
            "No process is modified and no packet capture is performed."
        ),
    }


@router.get("/network")
def host_network(
    db: DbSession,
    _: CurrentUser,
    hours: int = Query(default=24, ge=1, le=720),
) -> dict:
    return {
        "info": network_info(),
        "gateway": internet.default_gateway(),
        "probes": internet.latest_probes(db, limit_per_protocol=5),
        "history": internet.probe_history(db, hours=hours, limit=1000),
        "configured_targets": {
            "dns": settings.network_dns_target,
            "tcp": settings.network_tcp_targets,
            "icmp": settings.monitor_target,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/probes/run")
def run_probes(db: DbSession, _: AnalystUser) -> dict:
    if settings.environment == "testing":
        raise HTTPException(
            status_code=409,
            detail="Live connectivity probes are disabled in the test environment",
        )
    return internet.run_network_probes(db)
