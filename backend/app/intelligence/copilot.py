"""Data-grounded network copilot.

The copilot answers operational questions from the database. It does not invent
measurements and it does not call an external language model unless one is
configured by an operator (this build ships a deterministic, explainable
responder). Every answer lists the data sources it used and the limits of the
conclusion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.intelligence.common import aware, format_ms, format_pct, iso, risk_severity
from app.intelligence.host_ai import host_analysis
from app.intelligence.optimization import recommendation_feed
from app.intelligence.prediction import prediction_feed
from app.intelligence.rca import investigation_feed
from app.intelligence.security import security_feed
from app.models import (
    Agent,
    Device,
    HostMetric,
    IssueTicket,
    NetworkMeasurement,
    SecurityEvent,
    TrafficSample,
)

DISCLAIMER = (
    "The copilot summarises stored NetPulse telemetry with deterministic rules. "
    "It cannot see traffic it was not given, does not change device configuration, "
    "and does not replace operator judgement."
)
LIMITATIONS = [
    "Answers cover only data stored by this NetPulse deployment.",
    "Host process and socket details come from the monitoring host's own psutil view.",
    "Predictions are statistical estimates with the confidence shown in the relevant page.",
]


def _bytes_rate(value: float | None) -> str:
    if value is None:
        return "not measured"
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    index = 0
    number = float(value)
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.2f} {units[index]}"


def _latest_measurement(db: Session) -> NetworkMeasurement | None:
    return db.scalar(
        select(NetworkMeasurement)
        .where(NetworkMeasurement.data_origin == "live")
        .order_by(NetworkMeasurement.recorded_at.desc())
        .limit(1)
    )


def _local_metric(db: Session) -> HostMetric | None:
    local = db.scalar(select(Agent).where(Agent.kind == "local").order_by(Agent.id.asc()))
    if local is None:
        return None
    return db.scalar(
        select(HostMetric)
        .where(HostMetric.agent_id == local.id)
        .order_by(HostMetric.recorded_at.desc())
        .limit(1)
    )


def _device_counts(db: Session) -> dict:
    total = db.scalar(select(func.count(Device.id))) or 0
    approved = db.scalar(select(func.count(Device.id)).where(Device.is_approved.is_(True))) or 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    online = (
        db.scalar(
            select(func.count(Device.id)).where(
                Device.is_active.is_(True), Device.last_seen_at >= cutoff
            )
        )
        or 0
    )
    return {
        "total": total,
        "approved": approved,
        "unrecognized": total - approved,
        "online": online,
    }


def _overview(db: Session) -> dict:
    measurement = _latest_measurement(db)
    host = _local_metric(db)
    devices = _device_counts(db)
    tickets = (
        db.scalar(
            select(func.count(IssueTicket.id)).where(
                IssueTicket.status.in_(("Open", "In Progress"))
            )
        )
        or 0
    )
    threats = (
        db.scalar(
            select(func.count(SecurityEvent.id)).where(
                SecurityEvent.status.in_(("Open", "Acknowledged"))
            )
        )
        or 0
    )
    agents = list(db.scalars(select(Agent).where(Agent.is_active.is_(True))).all())
    online_agents = sum(
        1
        for agent in agents
        if agent.last_seen_at
        and (datetime.now(timezone.utc) - (aware(agent.last_seen_at) or datetime.now(timezone.utc))).total_seconds()
        <= settings.host_offline_after_seconds
    )
    traffic = db.scalar(
        select(TrafficSample)
        .where(TrafficSample.data_origin == "live")
        .order_by(TrafficSample.recorded_at.desc())
        .limit(1)
    )
    latency = measurement.latency_ms if measurement else None
    lines = [
        f"Devices: {devices['total']} observed ({devices['online']} seen in the last 15 minutes, "
        f"{devices['unrecognized']} unrecognised).",
        f"Active incidents: {tickets}. Open security findings: {threats}.",
        f"Monitoring agents: {len(agents)} configured, {online_agents} reporting within the stale window.",
        f"Latest ICMP probe: {format_ms(latency)} to {measurement.probe_target if measurement else settings.monitor_target}"
        f"{' (offline)' if measurement and not measurement.is_online else ''}.",
        (
            f"Local host: CPU {format_pct(host.cpu_pct)}, memory {format_pct(host.memory_pct)}, "
            f"disk {format_pct(host.disk_pct)}, download {_bytes_rate(host.download_bps)}, "
            f"upload {_bytes_rate(host.upload_bps)}."
            if host
            else "Local host telemetry has not been collected yet."
        ),
        (
            f"Host-interface throughput: {_bytes_rate(traffic.bytes_per_sec)} "
            f"({format_pct(traffic.utilization_pct)} of link speed)."
            if traffic
            else "No traffic sample is available yet."
        ),
    ]
    return {
        "answer": "Here is the current operational picture from stored telemetry.\n\n" + "\n".join(lines),
        "data": {
            "devices": devices,
            "active_incidents": tickets,
            "security_open": threats,
            "agents": {"total": len(agents), "online": online_agents},
            "measurement": {
                "latency_ms": measurement.latency_ms if measurement else None,
                "packet_loss_pct": measurement.packet_loss_pct if measurement else None,
                "is_online": measurement.is_online if measurement else None,
                "recorded_at": iso(measurement.recorded_at) if measurement else None,
            },
            "host": {
                "cpu_pct": host.cpu_pct if host else None,
                "memory_pct": host.memory_pct if host else None,
                "disk_pct": host.disk_pct if host else None,
                "download_bps": host.download_bps if host else None,
                "upload_bps": host.upload_bps if host else None,
                "recorded_at": iso(host.recorded_at) if host else None,
            },
        },
        "sources": [
            "devices table",
            "issue tickets",
            "security events",
            "network measurements",
            "host metrics",
            "traffic samples",
        ],
        "confidence": 0.9,
        "suggestions": [
            "Which host is at highest risk?",
            "What are the current security concerns?",
            "How is latency trending?",
            "What should I fix first?",
        ],
    }


def _host_question(db: Session) -> dict:
    analysis = host_analysis(db)
    scored = [item for item in analysis["agents"] if item.get("risk_score") is not None]
    at_risk = [item for item in scored if item["risk_score"] >= 50]
    if not analysis["agents"]:
        answer = (
            "No host telemetry exists yet. Start the backend collector or register a remote "
            "agent. I will not invent a host status."
        )
    elif not scored:
        answer = (
            analysis["message"]
            + " Current samples: "
            + "; ".join(
                f"{item['agent_name']}: {item.get('samples', 0)} observation(s)"
                for item in analysis["agents"]
            )
            + "."
        )
    else:
        lines = [analysis["message"]]
        for item in scored[:5]:
            metrics = item.get("metrics") or {}
            lines.append(
                f"• {item['agent_name']} — risk {item['risk_score']:.0f}/100 "
                f"({item['severity']}, {int(item['confidence'] * 100)}% confidence): "
                f"CPU {format_pct(metrics.get('cpu_pct'))}, memory {format_pct(metrics.get('memory_pct'))}, "
                f"disk {format_pct(metrics.get('disk_pct'))}."
            )
        if at_risk:
            top = at_risk[0]
            actions = [action["title"] for action in (top.get("recommended_actions") or [])[:3]]
            lines.append(
                f"Highest risk: {top['agent_name']}. Suggested checks: "
                + ("; ".join(actions) if actions else "review the host's recent metrics.")
            )
        answer = "\n".join(lines)
    return {
        "answer": answer,
        "data": {
            "summary": analysis.get("summary"),
            "agents": [
                {
                    "agent_id": item["agent_id"],
                    "agent_name": item["agent_name"],
                    "status": item["status"],
                    "risk_score": item.get("risk_score"),
                    "severity": item.get("severity"),
                    "confidence": item.get("confidence"),
                    "prediction_status": item.get("prediction_status"),
                    "metrics": item.get("metrics"),
                }
                for item in analysis["agents"][:10]
            ],
            "network": analysis.get("network"),
        },
        "sources": ["host metrics", "host risk model", "network measurements"],
        "confidence": 0.78,
        "suggestions": [
            "Show the recent host anomalies.",
            "What should I do about the highest-risk host?",
            "Is disk capacity a risk?",
        ],
    }


def _network_question(db: Session) -> dict:
    measurement = _latest_measurement(db)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = list(
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
    if not rows:
        return {
            "answer": (
                "No live network measurements are stored yet. Run a probe or wait for the "
                "monitoring cycle. No latency value is invented."
            ),
            "data": {"samples": 0},
            "sources": ["network measurements"],
            "confidence": 0.95,
            "suggestions": ["Why is monitoring empty?"],
        }
    online_ratio = sum(1 for row in rows if row.is_online) / len(rows) * 100
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    losses = [row.packet_loss_pct for row in rows if row.packet_loss_pct is not None]
    average_latency = round(sum(latencies) / len(latencies), 2) if latencies else None
    average_loss = round(sum(losses) / len(losses), 2) if losses else None
    first = next((row.latency_ms for row in rows if row.latency_ms is not None), None)
    last = next((row.latency_ms for row in reversed(rows) if row.latency_ms is not None), None)
    direction = "stable"
    if first is not None and last is not None:
        if last > first * 1.2:
            direction = "rising"
        elif last < first * 0.8:
            direction = "falling"
    answer = (
        f"Across {len(rows)} live probes in the last 24 hours, availability is "
        f"{online_ratio:.1f}% with mean latency {format_ms(average_latency)} and mean packet "
        f"loss {format_pct(average_loss)}. The recent latency direction is {direction}. "
        f"Latest probe: {format_ms(measurement.latency_ms) if measurement else 'not measured'} "
        f"to {measurement.probe_target if measurement else settings.monitor_target}."
    )
    warnings = []
    if average_latency is not None and average_latency >= settings.latency_threshold_ms:
        warnings.append(
            f"Mean latency exceeds the configured {settings.latency_threshold_ms:.0f} ms threshold."
        )
    if average_loss is not None and average_loss >= settings.packet_loss_threshold_pct:
        warnings.append(
            f"Mean packet loss exceeds the configured {settings.packet_loss_threshold_pct:.1f}% threshold."
        )
    if online_ratio < 100:
        warnings.append(f"{100 - online_ratio:.1f}% of probes were offline.")
    if warnings:
        answer += " Concerns: " + " ".join(warnings)
    return {
        "answer": answer,
        "data": {
            "samples": len(rows),
            "availability_pct": round(online_ratio, 2),
            "average_latency_ms": average_latency,
            "average_packet_loss_pct": average_loss,
            "direction": direction,
            "warnings": warnings,
        },
        "sources": ["network measurements", "ICMP probe engine"],
        "confidence": 0.85 if len(rows) >= 8 else 0.55,
        "suggestions": [
            "Is there an outage or just high latency?",
            "Which protocol checks are failing?",
        ],
    }


def _security_question(db: Session) -> dict:
    feed = security_feed(db, limit=20)
    open_events = [item for item in feed["events"] if item["status"] in {"Open", "Acknowledged"}]
    if not open_events:
        answer = (
            "There are no open defensive findings. "
            + (
                "A scan has run; absence of findings is not a security guarantee."
                if feed["last_scan_at"]
                else "No defensive scan has run yet, so this is not an all-clear."
            )
        )
    else:
        lines = [f"{len(open_events)} open defensive finding(s)."]
        for item in open_events[:6]:
            lines.append(
                f"• [{item['severity']}] {item['title']} — {item['description'][:180]}"
            )
        answer = "\n".join(lines) + (
            "\n\nFindings are based on authorised local observations only; "
            "no offensive scanning is performed."
        )
    return {
        "answer": answer,
        "data": {
            "summary": feed["summary"],
            "events": open_events[:10],
            "threat_intel": feed.get("threat_intel"),
            "last_scan_at": feed["last_scan_at"],
        },
        "sources": ["security events", "sign-in audit", "host socket table"],
        "confidence": 0.8,
        "suggestions": [
            "Explain the port fan-out finding.",
            "Is threat intelligence enabled?",
            "What authentication failures were seen?",
        ],
    }


def _incident_question(db: Session) -> dict:
    tickets = list(
        db.scalars(
            select(IssueTicket)
            .where(IssueTicket.status.in_(("Open", "In Progress")))
            .order_by(IssueTicket.created_at.desc())
            .limit(10)
        ).all()
    )
    if not tickets:
        answer = "There are no open or in-progress incidents."
    else:
        lines = [f"{len(tickets)} active incident(s)."]
        for ticket in tickets[:8]:
            lines.append(
                f"• {ticket.ticket_number} [{ticket.priority}] {ticket.title} — {ticket.status}"
            )
        answer = "\n".join(lines)
    return {
        "answer": answer,
        "data": {
            "incidents": [
                {
                    "id": ticket.id,
                    "ticket_number": ticket.ticket_number,
                    "title": ticket.title,
                    "priority": ticket.priority,
                    "status": ticket.status,
                    "issue_type": ticket.issue_type,
                    "created_at": iso(ticket.created_at),
                }
                for ticket in tickets
            ]
        },
        "sources": ["issue tickets"],
        "confidence": 0.92,
        "suggestions": ["What caused the newest incident?", "Which incident is most urgent?"],
    }


def _priority_question(db: Session) -> dict:
    analysis = host_analysis(db)
    security = security_feed(db, limit=20)
    predictions = prediction_feed(db)
    incidents = db.scalar(
        select(func.count(IssueTicket.id)).where(IssueTicket.status.in_(("Open", "In Progress")))
    ) or 0
    actions: list[dict] = []
    scored = [item for item in analysis["agents"] if item.get("risk_score") is not None]
    if scored:
        top = max(scored, key=lambda item: item["risk_score"])
        if top["risk_score"] >= 40:
            actions.append(
                {
                    "priority": 1,
                    "title": f"Investigate host {top['agent_name']}",
                    "reason": f"Estimated host risk {top['risk_score']:.0f}/100 ({top['severity']}).",
                }
            )
    open_security = [
        item for item in security["events"] if item["status"] in {"Open", "Acknowledged"}
    ]
    high_security = [item for item in open_security if item["severity"] in {"Critical", "High"}]
    if high_security:
        actions.append(
            {
                "priority": 2,
                "title": f"Review {len(high_security)} high-severity security finding(s)",
                "reason": high_security[0]["title"],
            }
        )
    predictions_list = [
        item
        for item in (predictions.get("predictions") or [])
        if (item.get("risk_score") or 0) >= 40
    ]
    if predictions_list:
        top = max(predictions_list, key=lambda item: item["risk_score"])
        actions.append(
            {
                "priority": 3,
                "title": f"Review predicted failure risk for {top.get('access_point_name') or top.get('scope')}",
                "reason": f"Predicted {top['risk_score']:.0f}/100 risk ({top['severity']}).",
            }
        )
    if incidents:
        actions.append(
            {
                "priority": 4,
                "title": f"Triage {incidents} active incident(s)",
                "reason": "Open tickets remain in the incident queue.",
            }
        )
    if not actions:
        answer = (
            "No priority action is indicated by the stored telemetry. Keep monitoring; "
            "this is not a guarantee that nothing is wrong."
        )
    else:
        actions.sort(key=lambda item: item["priority"])
        answer = "Suggested priority order based on current stored evidence:\n" + "\n".join(
            f"{index + 1}. {item['title']} — {item['reason']}"
            for index, item in enumerate(actions)
        )
    return {
        "answer": answer,
        "data": {"actions": actions},
        "sources": ["host metrics", "security events", "failure-risk predictions", "issue tickets"],
        "confidence": 0.7,
        "suggestions": ["Show the highest-risk host.", "Show open security findings."],
    }


def _agent_question(db: Session) -> dict:
    agents = list(db.scalars(select(Agent).order_by(Agent.id.asc())).all())
    if not agents:
        answer = "No monitoring agents are registered yet."
    else:
        now = datetime.now(timezone.utc)
        lines = []
        for agent in agents:
            seen = aware(agent.last_seen_at)
            age = int((now - seen).total_seconds()) if seen else None
            status = (
                "disabled"
                if not agent.is_active
                else ("online" if age is not None and age <= settings.host_offline_after_seconds else "offline")
            )
            lines.append(
                f"• {agent.name} ({agent.kind}) — {status}; last seen "
                + (f"{age}s ago" if age is not None else "never")
            )
        answer = "Registered telemetry sources:\n" + "\n".join(lines)
    return {
        "answer": answer,
        "data": {
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "kind": agent.kind,
                    "status": agent.status,
                    "is_active": agent.is_active,
                    "last_seen_at": iso(agent.last_seen_at),
                }
                for agent in agents
            ]
        },
        "sources": ["agents table"],
        "confidence": 0.9,
        "suggestions": ["Which agent has not reported?", "How do I register a remote agent?"],
    }


def _rca_question(db: Session) -> dict:
    feed = investigation_feed(db, limit=5)
    investigations = feed.get("investigations") or []
    if not investigations:
        answer = (
            "No root-cause investigation has been generated. Run one from AI Intelligence "
            "after collecting enough live measurements."
        )
        data = {"investigations": [], "status": feed.get("status")}
    else:
        lines = []
        for item in investigations[:3]:
            lines.append(
                f"• {item.get('problem')} — probable cause: {item.get('probable_cause')}; "
                f"confidence {int(float(item.get('confidence') or 0) * 100)}%."
            )
        answer = "Most recent root-cause investigations:\n" + "\n".join(lines)
        data = {"investigations": investigations}
    return {
        "answer": answer,
        "data": data,
        "sources": ["root-cause investigations", "network measurements"],
        "confidence": 0.7,
        "suggestions": ["Run a root-cause investigation.", "What evidence supports the cause?"],
    }


def _recommendation_question(db: Session) -> dict:
    feed = recommendation_feed(db)
    items = feed.get("recommendations") or []
    if not items:
        answer = "No optimisation recommendations are available from the current evidence window."
    else:
        top = items[:5]
        answer = "Top measured optimisation opportunities:\n" + "\n".join(
            f"• [{item.get('severity')}] {item.get('title')} — {item.get('description', '')[:160]}"
            for item in top
        )
    return {
        "answer": answer,
        "data": {"summary": feed.get("summary"), "recommendations": items[:10]},
        "sources": ["network measurements", "optimisation engine"],
        "confidence": 0.72,
        "suggestions": ["Why is this recommended?", "What is the expected impact?"],
    }


def answer_question(db: Session, question: str) -> dict:
    text = (question or "").strip()
    if not text:
        raise ValueError("A question is required")
    lowered = text.lower()

    if any(word in lowered for word in ("security", "threat", "attack", "port scan", "fan-out", "brute", "malware")):
        result = _security_question(db)
    elif any(word in lowered for word in ("incident", "ticket", "issue", "outage")):
        result = _incident_question(db)
    elif any(word in lowered for word in ("cpu", "memory", "disk", "host", "server", "process", "temperature", "ram")):
        result = _host_question(db)
    elif any(word in lowered for word in ("agent", "remote", "heartbeat", "telemetry source")):
        result = _agent_question(db)
    elif any(word in lowered for word in ("latency", "packet loss", "network", "internet", "dns", "tcp", "bandwidth", "traffic", "throughput")):
        result = _network_question(db)
    elif any(word in lowered for word in ("root cause", "rca", "why", "cause")):
        result = _rca_question(db)
    elif any(word in lowered for word in ("recommend", "optimi", "improve", "fix", "first", "priority")):
        result = _priority_question(db)
    else:
        result = _overview(db)

    result.update(
        {
            "question": text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "grounded_in_live_data": True,
            "method": "Deterministic database-grounded responder v1",
            "disclaimer": DISCLAIMER,
            "limitations": LIMITATIONS,
            "llm_configured": False,
        }
    )
    return result
