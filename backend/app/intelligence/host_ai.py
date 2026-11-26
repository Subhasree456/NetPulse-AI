"""Host-level predictive intelligence from persisted telemetry.

The engine uses transparent statistics rather than an opaque model:

* robust z-scores detect unusual latest values against each host's own baseline;
* least-squares trends describe sustained resource growth;
* a weighted score estimates operational risk from measured utilisation,
  availability, latency, packet loss and observed anomalies.

Every result carries its sample count, confidence and limitations. With fewer
than eight real observations the engine returns ``insufficient_data`` instead of
inventing a risk score.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import fmean, pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.intelligence.common import aware, clamp, iso, risk_severity
from app.models import Agent, HostMetric, NetworkMeasurement
from app.monitoring.host_agent import effective_status
from app.services.settings import get_setting

METHOD = "Host baseline z-score + weighted risk model v1"
MODEL_VERSION = "host-risk-v1"
WINDOW_HOURS = 24
MIN_SAMPLES = 8
STALE_AFTER = timedelta(minutes=10)
Z_THRESHOLD = 3.0

DISCLAIMER = (
    "Host risk scores are transparent statistical estimates from stored psutil "
    "telemetry. They are not guarantees of failure. A small sample produces a "
    "lower confidence, and insufficient data is reported explicitly."
)

LIMITATIONS = [
    "Risk reflects observed resource pressure and availability, not hardware diagnostics.",
    "Connection heuristics describe this host's socket table only; they are not network-wide packet inspection.",
    "Temperature is unavailable on many Windows systems and is shown as not measured.",
]


def _metrics(db: Session, agent_id: int, cutoff: datetime, limit: int = 2000) -> list[HostMetric]:
    return list(
        db.scalars(
            select(HostMetric)
            .where(
                HostMetric.agent_id == agent_id,
                HostMetric.recorded_at >= cutoff,
                HostMetric.data_origin == "live",
            )
            .order_by(HostMetric.recorded_at.asc())
            .limit(limit)
        ).all()
    )


def _values(points: list[HostMetric], field: str) -> list[float]:
    values: list[float] = []
    for point in points:
        value = getattr(point, field, None)
        if value is not None:
            values.append(float(value))
    return values


def _trend_pct(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    count = len(values)
    mean_x = (count - 1) / 2
    mean_y = sum(values) / count
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    if denominator == 0 or mean_y == 0:
        return None
    slope = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    slope /= denominator
    return round(slope * (count - 1) / mean_y * 100, 2)


def _z_anomalies(
    points: list[HostMetric],
    agent: Agent,
    *,
    cpu_threshold: float,
    memory_threshold: float,
    disk_threshold: float,
) -> list[dict]:
    if len(points) < MIN_SAMPLES:
        return []
    latest = points[-1]
    baseline = points[:-1]
    anomalies: list[dict] = []
    fields = {
        "cpu_pct": "CPU utilisation",
        "memory_pct": "memory utilisation",
        "disk_pct": "disk utilisation",
        "download_bps": "download throughput",
        "upload_bps": "upload throughput",
        "latency_ms": "network latency",
        "packet_loss_pct": "packet loss",
    }
    for field, label in fields.items():
        series = _values(baseline, field)
        current = getattr(latest, field, None)
        if current is None or len(series) < MIN_SAMPLES - 1:
            continue
        avg = fmean(series)
        deviation = pstdev(series)
        if deviation <= 0:
            continue
        z = (float(current) - avg) / deviation
        if abs(z) < Z_THRESHOLD:
            continue
        severity = "High" if abs(z) >= 4.5 else "Medium"
        anomalies.append(
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "metric": field,
                "label": label,
                "value": round(float(current), 2),
                "baseline_mean": round(avg, 2),
                "baseline_std": round(deviation, 2),
                "z_score": round(z, 2),
                "direction": "above" if z > 0 else "below",
                "severity": severity,
                "recorded_at": iso(latest.recorded_at),
                "explanation": (
                    f"{label.capitalize()} is {abs(z):.1f} standard deviations "
                    f"{'above' if z > 0 else 'below'} this host's recent baseline "
                    f"({avg:.1f} baseline vs {float(current):.1f} latest)."
                ),
                "method": "Baseline z-score",
            }
        )
    # Absolute threshold observations are useful even when variance is low.
    absolute = [
        ("cpu_pct", cpu_threshold, "CPU utilisation", "High"),
        ("memory_pct", memory_threshold, "Memory utilisation", "High"),
        ("disk_pct", disk_threshold, "Disk utilisation", "Medium"),
    ]
    for field, threshold, label, severity in absolute:
        current = getattr(latest, field, None)
        if current is None or float(current) < threshold:
            continue
        if any(item["metric"] == field for item in anomalies):
            continue
        anomalies.append(
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "metric": field,
                "label": label,
                "value": round(float(current), 2),
                "baseline_mean": round(fmean(_values(baseline, field)), 2)
                if _values(baseline, field)
                else None,
                "baseline_std": None,
                "z_score": None,
                "direction": "above",
                "severity": severity,
                "recorded_at": iso(latest.recorded_at),
                "explanation": f"{label} is above the configured {threshold:.0f}% threshold.",
                "method": "Absolute threshold",
            }
        )
    return anomalies


def _recommendations_for(
    agent: Agent,
    latest: HostMetric,
    anomalies: list[dict],
    trend: dict[str, float | None],
) -> list[dict]:
    actions: list[dict] = []
    if latest.cpu_pct is not None and latest.cpu_pct >= settings.cpu_threshold_pct:
        actions.append(
            {
                "priority": "High",
                "agent_id": agent.id,
                "agent_name": agent.name,
                "title": "Investigate sustained CPU pressure",
                "detail": f"CPU utilisation is {latest.cpu_pct:.1f}%. Review the top processes on this host and confirm whether the workload is expected.",
                "evidence": {"cpu_pct": latest.cpu_pct, "threshold": settings.cpu_threshold_pct},
            }
        )
    if latest.memory_pct is not None and latest.memory_pct >= settings.memory_threshold_pct:
        actions.append(
            {
                "priority": "High",
                "agent_id": agent.id,
                "agent_name": agent.name,
                "title": "Investigate memory pressure",
                "detail": f"Memory utilisation is {latest.memory_pct:.1f}%. Check for leaks or oversized processes before performance degrades.",
                "evidence": {"memory_pct": latest.memory_pct, "threshold": settings.memory_threshold_pct},
            }
        )
    if latest.disk_pct is not None and latest.disk_pct >= settings.disk_threshold_pct:
        actions.append(
            {
                "priority": "Medium",
                "agent_id": agent.id,
                "agent_name": agent.name,
                "title": "Free disk capacity",
                "detail": f"Disk utilisation is {latest.disk_pct:.1f}%. Archive or remove non-essential data and review log retention.",
                "evidence": {"disk_pct": latest.disk_pct, "threshold": settings.disk_threshold_pct},
            }
        )
    cpu_trend = trend.get("cpu_pct")
    if cpu_trend is not None and cpu_trend > 12:
        actions.append(
            {
                "priority": "Medium",
                "agent_id": agent.id,
                "agent_name": agent.name,
                "title": "Review a rising CPU trend",
                "detail": f"CPU utilisation rose {cpu_trend:.1f}% across the observation window. Correlate the change with deployments or scheduled jobs.",
                "evidence": {"cpu_trend_pct": cpu_trend},
            }
        )
    connection_ports = (
        (latest.details or {}).get("connections", {}).get("distinct_ports_by_host", {})
        if latest.details
        else {}
    )
    if isinstance(connection_ports, dict) and connection_ports:
        top_host, top_ports = max(connection_ports.items(), key=lambda item: item[1])
        if int(top_ports or 0) >= 12:
            actions.append(
                {
                    "priority": "High",
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "title": "Review a high distinct-port fan-out",
                    "detail": f"This host reported {top_ports} distinct destination ports to {top_host}. Confirm whether the application is expected to open that many connections.",
                    "evidence": {"remote_host": top_host, "distinct_ports": top_ports},
                }
            )
    if anomalies:
        top = max(anomalies, key=lambda item: abs(item.get("z_score") or 0))
        actions.append(
            {
                "priority": top["severity"],
                "agent_id": agent.id,
                "agent_name": agent.name,
                "title": f"Review unusual {top['label']}",
                "detail": top["explanation"],
                "evidence": {"metric": top["metric"], "z_score": top["z_score"], "value": top["value"]},
            }
        )
    return actions


def _assess_agent(
    db: Session,
    agent: Agent,
    points: list[HostMetric],
    *,
    cpu_threshold: float,
    memory_threshold: float,
    disk_threshold: float,
    latency_threshold: float,
    loss_threshold: float,
) -> dict:
    status = effective_status(agent)
    if not points:
        return {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "kind": agent.kind,
            "status": status,
            "prediction_status": "insufficient_data",
            "risk_score": None,
            "severity": None,
            "confidence": 0.0,
            "samples": 0,
            "message": "No live telemetry has been received from this agent yet.",
            "warning_signs": [],
            "predicted_issue": None,
            "recommended_actions": [
                "Confirm the agent is running and can reach the NetPulse API.",
                "Check the agent token and network path if it has never reported.",
            ],
            "metrics": None,
            "trend": {},
            "anomalies": [],
        }

    latest = points[-1]
    anomalies = _z_anomalies(
        points,
        agent,
        cpu_threshold=cpu_threshold,
        memory_threshold=memory_threshold,
        disk_threshold=disk_threshold,
    )
    trends = {
        "cpu_pct": _trend_pct(_values(points, "cpu_pct")),
        "memory_pct": _trend_pct(_values(points, "memory_pct")),
        "disk_pct": _trend_pct(_values(points, "disk_pct")),
        "download_bps": _trend_pct(_values(points, "download_bps")),
    }
    cpu = latest.cpu_pct or 0
    memory = latest.memory_pct or 0
    disk = latest.disk_pct or 0
    latency = latest.latency_ms or 0
    loss = latest.packet_loss_pct or 0
    connection_ports = (
        (latest.details or {}).get("connections", {}).get("distinct_ports_by_host", {})
        if latest.details
        else {}
    )
    max_ports = max((int(value or 0) for value in connection_ports.values()), default=0) if isinstance(connection_ports, dict) else 0

    components = {
        "cpu": round(min(1.0, cpu / 100) * 22, 2),
        "memory": round(min(1.0, memory / 100) * 22, 2),
        "disk": round(min(1.0, disk / 100) * 16, 2),
        "latency": round(min(1.0, latency / max(latency_threshold * 2, 1)) * 8, 2),
        "packet_loss": round(min(1.0, loss / max(loss_threshold, 0.1)) * 10, 2),
        "trend": round(min(10.0, max(0.0, (trends.get("cpu_pct") or 0) / 3)), 2),
        "anomalies": round(min(12.0, len(anomalies) * 3.0), 2),
        "connection_fanout": round(min(10.0, max(0.0, (max_ports - 10) * 1.0)), 2),
        "availability": 0.0 if status == "online" else 25.0,
    }
    score = round(clamp(sum(components.values())), 2)
    severity = risk_severity(score)
    samples = len(points)
    confidence = round(min(0.9, 0.3 + samples / 80), 2)
    if status != "online":
        confidence = round(max(0.2, confidence - 0.15), 2)
    if samples < MIN_SAMPLES:
        prediction_status = "insufficient_data"
        score_value = None
        severity_value = None
        message = (
            f"Only {samples} of {MIN_SAMPLES} required live observations are available. "
            "No host risk score is produced yet."
        )
    else:
        prediction_status = "estimated"
        score_value = score
        severity_value = severity
        message = (
            f"Estimated {score:.0f}/100 operational risk from {samples} live observations. "
            "This is a statistical estimate, not a failure guarantee."
        )

    warning_signs: list[str] = []
    if cpu >= cpu_threshold:
        warning_signs.append(f"CPU at {cpu:.1f}% is above the {cpu_threshold:.0f}% threshold.")
    if memory >= memory_threshold:
        warning_signs.append(f"Memory at {memory:.1f}% is above the {memory_threshold:.0f}% threshold.")
    if disk >= disk_threshold:
        warning_signs.append(f"Disk at {disk:.1f}% is above the {disk_threshold:.0f}% threshold.")
    if latency >= latency_threshold:
        warning_signs.append(f"Network latency {latency:.1f} ms is above the {latency_threshold:.0f} ms threshold.")
    if loss >= loss_threshold:
        warning_signs.append(f"Packet loss {loss:.2f}% is above the {loss_threshold:.1f}% threshold.")
    if status != "online":
        warning_signs.append(f"Agent status is {status}; telemetry may be stale.")
    for anomaly in anomalies[:4]:
        warning_signs.append(anomaly["explanation"])

    if status != "online":
        predicted = "Telemetry interruption may hide an availability problem."
    elif disk >= disk_threshold or (trends.get("disk_pct") or 0) > 20:
        predicted = "Disk capacity exhaustion could disrupt services if growth continues."
    elif memory >= memory_threshold or (trends.get("memory_pct") or 0) > 20:
        predicted = "Memory pressure could cause slowdowns or process termination."
    elif cpu >= cpu_threshold or (trends.get("cpu_pct") or 0) > 15:
        predicted = "Sustained CPU pressure may increase response times."
    elif loss >= loss_threshold or latency >= latency_threshold:
        predicted = "Network instability could affect this host's service availability."
    elif anomalies:
        predicted = "An unusual metric pattern may indicate an emerging workload or configuration change."
    else:
        predicted = "No material resource or availability risk is currently indicated."

    recommendations = _recommendations_for(agent, latest, anomalies, trends)
    return {
        "agent_id": agent.id,
        "agent_name": agent.name,
        "kind": agent.kind,
        "hostname": agent.hostname,
        "status": status,
        "prediction_status": prediction_status,
        "risk_score": score_value,
        "severity": severity_value,
        "confidence": confidence,
        "samples": samples,
        "message": message,
        "warning_signs": warning_signs[:8],
        "predicted_issue": predicted,
        "recommended_actions": recommendations,
        "trend": {key: value for key, value in trends.items() if value is not None},
        "components": components,
        "anomalies": anomalies,
        "metrics": {
            "cpu_pct": latest.cpu_pct,
            "memory_pct": latest.memory_pct,
            "disk_pct": latest.disk_pct,
            "latency_ms": latest.latency_ms,
            "packet_loss_pct": latest.packet_loss_pct,
            "download_bps": latest.download_bps,
            "upload_bps": latest.upload_bps,
            "recorded_at": iso(latest.recorded_at),
        },
    }


def _network_assessment(
    db: Session, *, latency_threshold: float, loss_threshold: float
) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    points = list(
        db.scalars(
            select(NetworkMeasurement)
            .where(
                NetworkMeasurement.data_origin == "live",
                NetworkMeasurement.recorded_at >= cutoff,
                NetworkMeasurement.access_point_id.is_(None),
            )
            .order_by(NetworkMeasurement.recorded_at.asc())
            .limit(2000)
        ).all()
    )
    if len(points) < MIN_SAMPLES:
        return {
            "prediction_status": "insufficient_data",
            "risk_score": None,
            "severity": None,
            "confidence": 0.0,
            "samples": len(points),
            "message": f"{len(points)} of {MIN_SAMPLES} required live network measurements are available.",
            "predicted_issue": None,
            "warning_signs": [],
        }
    recent = points[-12:]
    online_ratio = sum(1 for point in recent if point.is_online) / len(recent)
    latencies = [point.latency_ms for point in recent if point.latency_ms is not None]
    losses = [point.packet_loss_pct for point in recent]
    latency = fmean(latencies) if latencies else 0.0
    loss = fmean(losses) if losses else 0.0
    latency_trend = _trend_pct([point.latency_ms for point in points if point.latency_ms is not None])
    components = {
        "availability": round((1 - online_ratio) * 40, 2),
        "packet_loss": round(min(1.0, loss / max(loss_threshold, 0.1)) * 25, 2),
        "latency": round(min(1.0, latency / max(latency_threshold * 2, 1)) * 20, 2),
        "trend": round(min(10.0, max(0.0, (latency_trend or 0) / 4)), 2),
    }
    score = round(clamp(sum(components.values())), 2)
    warnings: list[str] = []
    if online_ratio < 1:
        warnings.append(f"{round((1 - online_ratio) * 100)}% of recent probes were offline.")
    if loss >= loss_threshold:
        warnings.append(f"Average packet loss is {loss:.2f}%.")
    if latency >= latency_threshold:
        warnings.append(f"Average latency is {latency:.1f} ms.")
    if latency_trend is not None and latency_trend > 15:
        warnings.append(f"Latency rose {latency_trend:.1f}% across the window.")
    return {
        "prediction_status": "estimated",
        "risk_score": score,
        "severity": risk_severity(score),
        "confidence": round(min(0.9, 0.4 + len(points) / 60), 2),
        "samples": len(points),
        "message": f"Estimated {score:.0f}/100 network instability risk from {len(points)} live probes.",
        "predicted_issue": (
            "Repeated probe failures or elevated loss/latency could affect network availability."
            if score >= 30
            else "Network performance is currently within the measured thresholds."
        ),
        "warning_signs": warnings,
        "components": components,
        "latency_trend_pct": latency_trend,
        "online_ratio_pct": round(online_ratio * 100, 2),
    }


def host_analysis(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    cpu_threshold = float(get_setting(db, "cpu_threshold_pct", settings.cpu_threshold_pct))
    memory_threshold = float(get_setting(db, "memory_threshold_pct", settings.memory_threshold_pct))
    disk_threshold = float(get_setting(db, "disk_threshold_pct", settings.disk_threshold_pct))
    latency_threshold = float(get_setting(db, "latency_threshold_ms", settings.latency_threshold_ms))
    loss_threshold = float(get_setting(db, "packet_loss_threshold_pct", settings.packet_loss_threshold_pct))

    agents = list(
        db.scalars(select(Agent).where(Agent.is_active.is_(True)).order_by(Agent.id.asc())).all()
    )
    assessments: list[dict] = []
    anomalies: list[dict] = []
    recommendations: list[dict] = []
    for agent in agents:
        points = _metrics(db, agent.id, cutoff)
        assessment = _assess_agent(
            db,
            agent,
            points,
            cpu_threshold=cpu_threshold,
            memory_threshold=memory_threshold,
            disk_threshold=disk_threshold,
            latency_threshold=latency_threshold,
            loss_threshold=loss_threshold,
        )
        assessments.append(assessment)
        anomalies.extend(assessment.get("anomalies") or [])
        recommendations.extend(assessment.get("recommended_actions") or [])

    network = _network_assessment(
        db, latency_threshold=latency_threshold, loss_threshold=loss_threshold
    )
    assessments.sort(
        key=lambda item: item.get("risk_score") if item.get("risk_score") is not None else -1,
        reverse=True,
    )
    anomalies.sort(key=lambda item: abs(item.get("z_score") or 0), reverse=True)
    priority_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    recommendations.sort(key=lambda item: priority_order.get(item.get("priority"), 4))

    scored = [item for item in assessments if item.get("risk_score") is not None]
    online = sum(1 for item in assessments if item.get("status") == "online")
    has_any = any(item.get("samples") for item in assessments) or network.get("samples")
    status = "ready" if has_any else "insufficient_data"
    if not has_any:
        message = (
            "No host telemetry has been collected yet. Start the local collector or "
            "register a remote agent; no synthetic values are substituted."
        )
    elif not scored:
        message = "Telemetry exists but there are not yet enough samples for a stable host risk estimate."
    else:
        message = (
            f"Assessed {len(scored)} host(s) and the network path from stored live telemetry. "
            f"{sum(1 for item in scored if item['risk_score'] >= 50)} host(s) are at elevated risk."
        )

    return {
        "status": status,
        "message": message,
        "window_hours": WINDOW_HOURS,
        "minimum_samples": MIN_SAMPLES,
        "agents": assessments,
        "network": network,
        "anomalies": anomalies[:50],
        "recommendations": recommendations[:20],
        "summary": {
            "agents": len(assessments),
            "online": online,
            "offline": len(assessments) - online,
            "scored": len(scored),
            "at_risk": sum(1 for item in scored if item["risk_score"] >= 50),
            "critical": sum(1 for item in scored if item["severity"] == "Critical"),
            "high": sum(1 for item in scored if item["severity"] == "High"),
            "anomalies": len(anomalies),
            "recommendations": len(recommendations),
            "highest_risk": scored[0]["risk_score"] if scored else None,
            "highest_agent": scored[0]["agent_name"] if scored else None,
            "average_cpu_pct": round(
                fmean([item["metrics"]["cpu_pct"] for item in assessments if item.get("metrics") and item["metrics"].get("cpu_pct") is not None]),
                2,
            )
            if any(item.get("metrics") and item["metrics"].get("cpu_pct") is not None for item in assessments)
            else None,
            "average_memory_pct": round(
                fmean([item["metrics"]["memory_pct"] for item in assessments if item.get("metrics") and item["metrics"].get("memory_pct") is not None]),
                2,
            )
            if any(item.get("metrics") and item["metrics"].get("memory_pct") is not None for item in assessments)
            else None,
        },
        "generated_at": now.isoformat(),
        "method": METHOD,
        "model_version": MODEL_VERSION,
        "disclaimer": DISCLAIMER,
        "limitations": LIMITATIONS,
    }


def stale_agents(db: Session) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(Agent).where(Agent.is_active.is_(True))).all()
    return [
        {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "status": effective_status(agent, now),
            "last_seen_at": iso(agent.last_seen_at),
            "stale": bool(
                agent.last_seen_at
                and (now - (aware(agent.last_seen_at) or now)).total_seconds()
                > settings.host_offline_after_seconds
            ),
        }
        for agent in rows
    ]
