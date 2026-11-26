"""Dashboard aggregation. Simulated measurements are always excluded."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import fmean

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.ai.anomaly import anomaly_window_summary
from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.intelligence.common import aware, clamp
from app.intelligence.rollup import intelligence_overview
from app.models import (
    AccessPoint,
    Agent,
    AnomalyEvent,
    Device,
    HostMetric,
    IssueTicket,
    NetworkMeasurement,
    SecurityEvent,
    TrafficSample,
)
from app.monitoring import internet
from app.monitoring.host_agent import effective_status, metric_as_dict
from app.monitoring.scheduler import scheduler

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
ACTIVE_TICKET_STATUSES = ("Open", "In Progress")
OPEN_SECURITY_STATUSES = ("Open", "Acknowledged")
DEVICE_ONLINE_WINDOW = timedelta(minutes=15)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _age_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - value).total_seconds()))


def _health_buckets(
    measurements: list[NetworkMeasurement], hours: int
) -> list[dict]:
    """Aggregate real probe results into at most 24 health/availability buckets."""
    if not measurements:
        return []
    step_hours = max(1, hours // 24)
    buckets: dict[str, dict] = {}
    for row in measurements:
        moment = aware(row.recorded_at)
        if moment is None:
            continue
        bucket_hour = moment.replace(minute=0, second=0, microsecond=0)
        bucket_hour = bucket_hour - timedelta(hours=bucket_hour.hour % step_hours)
        key = bucket_hour.isoformat()
        bucket = buckets.setdefault(
            key,
            {
                "timestamp": key,
                "total": 0,
                "online": 0,
                "latencies": [],
                "losses": [],
            },
        )
        bucket["total"] += 1
        if row.is_online:
            bucket["online"] += 1
        if row.latency_ms is not None:
            bucket["latencies"].append(float(row.latency_ms))
        bucket["losses"].append(float(row.packet_loss_pct or 0))

    output: list[dict] = []
    for key in sorted(buckets):
        bucket = buckets[key]
        total = bucket["total"]
        ratio = bucket["online"] / total if total else 0
        loss = fmean(bucket["losses"]) if bucket["losses"] else 0
        latency = fmean(bucket["latencies"]) if bucket["latencies"] else None
        score = clamp(ratio * 100 - min(25.0, loss * 2.5))
        output.append(
            {
                "timestamp": key,
                "uptime_pct": round(ratio * 100, 2),
                "packet_loss_pct": round(loss, 2),
                "latency_ms": round(latency, 2) if latency is not None else None,
                "health_pct": round(score, 2),
                "samples": total,
            }
        )
    return output


def _host_block(
    db: DbSession, cutoff: datetime
) -> tuple[dict, dict, list[dict], HostMetric | None]:
    agents = list(db.scalars(select(Agent).order_by(Agent.kind.asc(), Agent.id.asc())).all())
    agent_items = []
    local_metric: HostMetric | None = None
    local_series: list[dict] = []
    for agent in agents:
        latest = db.scalar(
            select(HostMetric)
            .where(HostMetric.agent_id == agent.id)
            .order_by(HostMetric.recorded_at.desc())
            .limit(1)
        )
        item = {
            "id": agent.id,
            "name": agent.name,
            "kind": agent.kind,
            "hostname": agent.hostname,
            "status": effective_status(agent),
            "last_seen_at": _iso(agent.last_seen_at),
            "latest": metric_as_dict(latest),
        }
        if agent.kind == "local":
            local_metric = latest
            rows = list(
                db.scalars(
                    select(HostMetric)
                    .where(
                        HostMetric.agent_id == agent.id,
                        HostMetric.recorded_at >= cutoff,
                    )
                    .order_by(HostMetric.recorded_at.desc())
                    .limit(180)
                ).all()
            )
            rows.reverse()
            local_series = [
                {
                    "timestamp": _iso(row.recorded_at),
                    "cpu_pct": row.cpu_pct,
                    "memory_pct": row.memory_pct,
                    "disk_pct": row.disk_pct,
                    "upload_bps": row.upload_bps,
                    "download_bps": row.download_bps,
                }
                for row in rows
            ]
        agent_items.append(item)
    online = sum(1 for item in agent_items if item["status"] == "online")
    summary = {
        "total": len(agent_items),
        "online": online,
        "offline": sum(1 for item in agent_items if item["status"] == "offline"),
        "never_reported": sum(1 for item in agent_items if item["status"] == "never"),
        "local": sum(1 for item in agent_items if item["kind"] == "local"),
        "remote": sum(1 for item in agent_items if item["kind"] == "remote"),
    }
    return summary, {"agents": agent_items}, local_series, local_metric



@router.get("/overview")
def overview(
    db: DbSession,
    _: CurrentUser,
    hours: int = Query(default=24, ge=1, le=720),
) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    access_points = db.scalars(select(AccessPoint)).all()
    real_aps = [ap for ap in access_points if not ap.is_simulated]
    simulated_aps = [ap for ap in access_points if ap.is_simulated]
    online_aps = [ap for ap in real_aps if ap.status == "online"]
    offline_aps = [ap for ap in real_aps if ap.status == "offline"]

    measurements = db.scalars(
        select(NetworkMeasurement)
        .where(
            NetworkMeasurement.data_origin == "live",
            NetworkMeasurement.recorded_at >= cutoff,
        )
        .order_by(NetworkMeasurement.recorded_at.asc())
        .limit(2000)
    ).all()
    latest = db.scalar(
        select(NetworkMeasurement)
        .where(NetworkMeasurement.data_origin == "live")
        .order_by(NetworkMeasurement.recorded_at.desc())
        .limit(1)
    )
    total = len(measurements)
    online_count = sum(1 for m in measurements if m.is_online)
    uptime_pct = round(online_count / total * 100, 2) if total else None
    latency_values = [m.latency_ms for m in measurements if m.latency_ms is not None]
    loss_values = [m.packet_loss_pct for m in measurements]
    avg_latency = round(fmean(latency_values), 2) if latency_values else None
    avg_loss = round(fmean(loss_values), 2) if loss_values else None

    ap_health = (
        round(len(online_aps) / len(real_aps) * 100, 2)
        if real_aps and any(ap.last_checked_at for ap in real_aps)
        else None
    )
    health_parts = [value for value in (uptime_pct, ap_health) if value is not None]
    health_pct = round(fmean(health_parts), 2) if health_parts else None

    open_tickets = db.scalars(
        select(IssueTicket).where(IssueTicket.status.in_(ACTIVE_TICKET_STATUSES))
    ).all()
    anomaly_summary = anomaly_window_summary(db, hours=hours)
    known_devices = db.scalar(
        select(func.count(Device.id)).where(Device.is_approved.is_(True))
    ) or 0
    unknown_devices = db.scalar(
        select(func.count(Device.id)).where(Device.is_approved.is_(False))
    ) or 0

    alerts: list[dict] = []
    for event in db.scalars(
        select(AnomalyEvent)
        .where(AnomalyEvent.detected_at >= cutoff)
        .order_by(AnomalyEvent.detected_at.desc())
        .limit(6)
    ).all():
        alerts.append(
            {
                "kind": "ai_anomaly",
                "id": event.id,
                "title": f"AI anomaly · {event.severity}",
                "message": event.explanation[:220],
                "severity": event.severity,
                "created_at": _iso(event.detected_at),
                "path": "/anomalies",
            }
        )
    for ticket in sorted(
        [t for t in open_tickets if t.priority in {"Critical", "High"}],
        key=lambda t: t.created_at,
        reverse=True,
    )[:5]:
        alerts.append(
            {
                "kind": "ticket",
                "id": ticket.id,
                "title": f"{ticket.priority} issue · {ticket.ticket_number}",
                "message": ticket.title,
                "severity": ticket.priority,
                "created_at": _iso(ticket.created_at),
                "path": "/issues",
            }
        )
    for device in db.scalars(
        select(Device)
        .where(Device.is_approved.is_(False))
        .order_by(Device.last_seen_at.desc())
        .limit(4)
    ).all():
        alerts.append(
            {
                "kind": "unknown_device",
                "id": device.id,
                "title": "Unrecognized device observed",
                "message": f"{device.display_name or 'Unapproved device'} at {device.ip_address}",
                "severity": "Medium",
                "created_at": _iso(device.last_seen_at),
                "path": "/devices",
            }
        )
    alerts.sort(key=lambda item: item["created_at"] or "", reverse=True)

    series = [
        {
            "timestamp": _iso(m.recorded_at),
            "latency_ms": m.latency_ms,
            "packet_loss_pct": m.packet_loss_pct,
            "jitter_ms": m.jitter_ms,
            "is_online": m.is_online,
        }
        for m in measurements
    ]

    now = datetime.now(timezone.utc)
    agents_summary, agents_block, host_series, host_metric = _host_block(db, cutoff)
    traffic_latest = db.scalar(
        select(TrafficSample)
        .where(TrafficSample.data_origin == "live")
        .order_by(TrafficSample.recorded_at.desc())
        .limit(1)
    )
    all_devices = list(db.scalars(select(Device)).all())
    online_devices = 0
    for device in all_devices:
        seen = aware(device.last_seen_at)
        if device.is_active and seen is not None and now - seen <= DEVICE_ONLINE_WINDOW:
            online_devices += 1

    security_open_events = list(
        db.scalars(
            select(SecurityEvent).where(SecurityEvent.status.in_(OPEN_SECURITY_STATUSES))
        ).all()
    )
    recent_security = list(
        db.scalars(
            select(SecurityEvent).order_by(SecurityEvent.detected_at.desc()).limit(8)
        ).all()
    )
    vendor_counts: dict[str, int] = {}
    for device in all_devices:
        label = "Approved" if device.is_approved else "Unrecognized"
        vendor_counts[label] = vendor_counts.get(label, 0) + 1
    device_distribution = [
        {"name": name, "value": count}
        for name, count in sorted(vendor_counts.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    health_trend = _health_buckets(measurements, hours)
    recent_incidents = [
        {
            "id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "title": ticket.title,
            "priority": ticket.priority,
            "status": ticket.status,
            "issue_type": ticket.issue_type,
            "created_at": _iso(ticket.created_at),
            "updated_at": _iso(ticket.updated_at),
        }
        for ticket in sorted(
            open_tickets, key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True
        )[:8]
    ]
    security_timeline = [
        {
            "id": row.id,
            "title": row.title,
            "event_type": row.event_type,
            "category": row.category,
            "severity": row.severity,
            "status": row.status,
            "detected_at": _iso(row.detected_at),
            "description": row.description,
        }
        for row in recent_security
    ]
    latest_host_age = _age_seconds(host_metric.recorded_at) if host_metric else None
    latest_measurement_age = _age_seconds(latest.recorded_at) if latest else None

    return {
        "summary": {
            "total_access_points": len(real_aps),
            "online_access_points": len(online_aps),
            "offline_access_points": len(offline_aps),
            "simulated_access_points": len(simulated_aps),
            "network_health_pct": health_pct,
            "average_latency_ms": avg_latency,
            "packet_loss_pct": avg_loss,
            "uptime_pct": uptime_pct,
            "open_issues": len(open_tickets),
            "anomalies": anomaly_summary["total"],
            "known_devices": known_devices,
            "unknown_devices": unknown_devices,
            "total_devices": len(all_devices),
            "online_devices": online_devices,
            "offline_devices": max(0, len(all_devices) - online_devices),
            "active_incidents": len(open_tickets),
            "security_threats": len(security_open_events),
            "critical_threats": sum(
                1 for item in security_open_events if item.severity == "Critical"
            ),
            "cpu_pct": host_metric.cpu_pct if host_metric else None,
            "memory_pct": host_metric.memory_pct if host_metric else None,
            "disk_pct": host_metric.disk_pct if host_metric else None,
            "upload_bps": host_metric.upload_bps if host_metric else None,
            "download_bps": host_metric.download_bps if host_metric else None,
            "incoming_bps": (
                host_metric.download_bps
                if host_metric and host_metric.download_bps is not None
                else (
                        traffic_latest.bytes_per_sec * 8
                        if traffic_latest and traffic_latest.bytes_per_sec is not None
                        else None
                    )
            ),
            "outgoing_bps": host_metric.upload_bps if host_metric else None,
            "bandwidth_utilization_pct": (
                host_metric.net_utilization_pct
                if host_metric and host_metric.net_utilization_pct is not None
                else (traffic_latest.utilization_pct if traffic_latest else None)
            ),
            "agents_total": agents_summary["total"],
            "agents_online": agents_summary["online"],
            "agents_offline": agents_summary["offline"],
        },
        "latest_measurement": (
            {
                "id": latest.id,
                "latency_ms": latest.latency_ms,
                "packet_loss_pct": latest.packet_loss_pct,
                "jitter_ms": latest.jitter_ms,
                "is_online": latest.is_online,
                "probe_target": latest.probe_target,
                "recorded_at": _iso(latest.recorded_at),
                "data_origin": latest.data_origin,
                "error_message": latest.error_message,
            }
            if latest
            else None
        ),
        "series": series,
        "health_trend": health_trend,
        "availability": health_trend,
        "host": {
            "summary": (
                {
                    "cpu_pct": host_metric.cpu_pct,
                    "memory_pct": host_metric.memory_pct,
                    "disk_pct": host_metric.disk_pct,
                    "swap_pct": host_metric.swap_pct,
                    "temperature_c": host_metric.temperature_c,
                    "uptime_seconds": host_metric.uptime_seconds,
                    "process_count": host_metric.process_count,
                    "upload_bps": host_metric.upload_bps,
                    "download_bps": host_metric.download_bps,
                    "net_utilization_pct": host_metric.net_utilization_pct,
                    "recorded_at": _iso(host_metric.recorded_at),
                    "details": host_metric.details or {},
                }
                if host_metric
                else None
            ),
            "series": host_series,
            "status": "live" if host_metric else "no_data",
        },
        "agents": agents_block,
        "agents_summary": agents_summary,
        "devices": {
            "total": len(all_devices),
            "online": online_devices,
            "offline": max(0, len(all_devices) - online_devices),
            "approved": known_devices,
            "unrecognized": unknown_devices,
            "distribution": device_distribution,
            "online_window_minutes": int(DEVICE_ONLINE_WINDOW.total_seconds() // 60),
        },
        "security": {
            "open": len(security_open_events),
            "critical": sum(1 for item in security_open_events if item.severity == "Critical"),
            "high": sum(1 for item in security_open_events if item.severity == "High"),
            "timeline": security_timeline,
        },
        "recent_incidents": recent_incidents,
        "traffic": {
            "latest": (
                {
                    "recorded_at": _iso(traffic_latest.recorded_at),
                    "bytes_per_sec": traffic_latest.bytes_per_sec,
                    "packets_per_sec": traffic_latest.packets_per_sec,
                    "utilization_pct": traffic_latest.utilization_pct,
                    "link_speed_mbps": traffic_latest.link_speed_mbps,
                }
                if traffic_latest
                else None
            ),
            "incoming_bps": (
                host_metric.download_bps
                if host_metric and host_metric.download_bps is not None
                else (
                        traffic_latest.bytes_per_sec * 8
                        if traffic_latest and traffic_latest.bytes_per_sec is not None
                        else None
                    )
            ),
            "outgoing_bps": host_metric.upload_bps if host_metric else None,
            "utilization_pct": (
                host_metric.net_utilization_pct
                if host_metric and host_metric.net_utilization_pct is not None
                else (traffic_latest.utilization_pct if traffic_latest else None)
            ),
            "series": host_series,
        },
        "network_probes": internet.latest_probes(db, limit_per_protocol=3),
        "data_freshness": {
            "latest_measurement_age_seconds": latest_measurement_age,
            "host_metric_age_seconds": latest_host_age,
            "measurement_stale": (
                latest_measurement_age is None
                or latest_measurement_age > settings.monitoring_interval_seconds * 3
            ),
            "host_stale": (
                latest_host_age is None
                or latest_host_age > settings.host_metric_interval_seconds * 3
            ),
            "stale_after_seconds": settings.monitoring_interval_seconds * 3,
        },
        "connection": {
            "websocket_path": f"{settings.api_prefix}/live/ws",
            "polling_fallback": True,
            "last_cycle_at": _iso(scheduler.last_cycle_at),
            "last_host_collection_at": _iso(scheduler.last_host_collection_at),
            "last_network_probe_at": _iso(scheduler.last_network_probe_at),
            "last_error": scheduler.last_error,
        },
        "recent_alerts": alerts[:10],
        "anomaly_breakdown": anomaly_summary,
        "intelligence": intelligence_overview(db),
        "ticket_breakdown": {
            "open": sum(1 for t in open_tickets if t.status == "Open"),
            "in_progress": sum(1 for t in open_tickets if t.status == "In Progress"),
        },
        "monitoring": {
            "enabled": bool(settings.monitoring_enabled),
            "host_monitoring_enabled": bool(settings.host_monitoring_enabled),
            "interval_seconds": settings.monitoring_interval_seconds,
            "host_interval_seconds": settings.host_metric_interval_seconds,
            "network_probe_interval_seconds": settings.network_check_interval_seconds,
            "target": settings.monitor_target,
            "last_cycle_at": _iso(scheduler.last_cycle_at),
            "last_device_scan_at": _iso(scheduler.last_device_scan_at),
            "last_error": scheduler.last_error,
            "method": "Live ICMP probes / psutil host telemetry / DNS+TCP checks / read-only ARP cache",
        },
        "window_hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "All metrics are computed from live local probes and psutil telemetry. Simulated "
            "AP records and simulated measurements are excluded. AI outputs are statistical "
            "observations, not guaranteed predictions."
        ),
    }
