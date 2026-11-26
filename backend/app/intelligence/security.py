"""Defensive cybersecurity intelligence from authorised local telemetry.

Signals are deliberately limited to evidence this deployment actually has:
unapproved devices observed in the local ARP cache and authentication failures
recorded by the sign-in endpoint. The scanner never performs offensive actions,
port scans or packet capture, and every finding is clearly sourced.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.intelligence.common import aware, clamp, iso
from app.intelligence.threat_intel import enrich_many, threat_intel_status
from app.models import Agent, AppSetting, Device, HostMetric, LoginAttempt, SecurityEvent
from app.services.notifications import notify

METHOD = "Defensive local telemetry correlation v2"
DISCLAIMER = (
    "Findings come only from authorised local observations (ARP neighbours, sign-in "
    "audit records, and the monitoring host's own socket table). No offensive scanning "
    "or packet capture is performed, and absence of findings is not a guarantee that a "
    "network is secure. Optional threat-intelligence scores are third-party data and are "
    "shown only when a provider key is configured."
)
HOST_WINDOW = timedelta(hours=6)
HOST_MIN_SAMPLES = 8
PORT_FANOUT_THRESHOLD = 15
PORT_FANOUT_HIGH = 30
CONNECTION_SPIKE_MULTIPLIER = 2.5
CONNECTION_SPIKE_MINIMUM = 60
TRAFFIC_SPIKE_MULTIPLIER = 4.0
TRAFFIC_SPIKE_MIN_BPS = 2_000_000
SCAN_SETTING_KEY = "security_last_scan_at"
FAILURE_WINDOW = timedelta(minutes=15)
FAILURE_THRESHOLD = 5
ROGUE_LOOKBACK = timedelta(days=7)
AUTO_RESOLVE_AFTER = timedelta(days=1)


def _scan_timestamp(db: Session) -> datetime | None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == SCAN_SETTING_KEY))
    if row is None or not isinstance(row.value, str):
        return None
    try:
        return datetime.fromisoformat(row.value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_scan_timestamp(db: Session, moment: datetime) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == SCAN_SETTING_KEY))
    payload = moment.isoformat()
    if row is None:
        db.add(AppSetting(key=SCAN_SETTING_KEY, value=payload))
    else:
        row.value = payload


def _open_event(db: Session, fingerprint: str) -> SecurityEvent | None:
    return db.scalar(
        select(SecurityEvent)
        .where(
            SecurityEvent.fingerprint == fingerprint,
            SecurityEvent.status.in_(("Open", "Acknowledged")),
        )
        .order_by(SecurityEvent.detected_at.desc())
        .limit(1)
    )


def _upsert_event(
    db: Session,
    *,
    fingerprint: str,
    values: dict,
) -> tuple[SecurityEvent, bool]:
    existing = _open_event(db, fingerprint)
    if existing is not None:
        for key, value in values.items():
            if key == "status":
                continue
            setattr(existing, key, value)
        existing.updated_at = datetime.now(timezone.utc)
        return existing, False
    row = SecurityEvent(fingerprint=fingerprint, **values)
    db.add(row)
    db.flush()
    return row, True


def _serialise(row: SecurityEvent) -> dict:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "category": row.category,
        "severity": row.severity,
        "status": row.status,
        "source": row.source,
        "device_id": row.device_id,
        "access_point_id": row.access_point_id,
        "title": row.title,
        "description": row.description,
        "evidence": row.evidence or {},
        "recommendation": row.recommendation,
        "confidence": round(row.confidence, 2),
        "fingerprint": row.fingerprint,
        "is_simulated": row.is_simulated,
        "detected_at": iso(row.detected_at),
        "updated_at": iso(row.updated_at),
        "resolved_at": iso(row.resolved_at),
    }


def run_security_scan(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    created: list[SecurityEvent] = []
    updated: list[SecurityEvent] = []

    # Retain a concise 30-day authentication audit only.
    for old in db.scalars(
        select(LoginAttempt).where(LoginAttempt.attempted_at < now - timedelta(days=30))
    ).all():
        db.delete(old)

    # --- Unapproved devices seen recently ---------------------------------
    rogue_devices = list(
        db.scalars(
            select(Device)
            .where(
                Device.is_approved.is_(False),
                Device.is_active.is_(True),
                Device.last_seen_at >= now - ROGUE_LOOKBACK,
            )
            .order_by(Device.last_seen_at.desc())
            .limit(50)
        ).all()
    )
    current_rogue_ids: set[int] = set()
    for device in rogue_devices:
        current_rogue_ids.add(device.id)
        age = now - (aware(device.last_seen_at) or now)
        severity = "High" if age <= timedelta(hours=1) else "Medium"
        fingerprint = f"rogue_device:{device.id}"
        description = (
            f"Device {device.display_name or device.ip_address} at {device.ip_address} has "
            f"been observed on the local subnet but is not approved. Last seen "
            f"{iso(device.last_seen_at)}."
        )
        values = {
            "event_type": "unapproved_device",
            "category": "device",
            "severity": severity,
            "status": "Open",
            "source": "ARP neighbour discovery",
            "device_id": device.id,
            "access_point_id": None,
            "title": "Unapproved device observed",
            "description": description,
            "evidence": {
                "ip_address": device.ip_address,
                "mac_masked": device.mac_masked,
                "hostname": device.hostname,
                "last_seen_at": iso(device.last_seen_at),
                "approved": False,
            },
            "recommendation": (
                "Verify the device with the network owner. If it is legitimate, approve it in "
                "Devices; otherwise locate and isolate it following your incident process."
            ),
            "confidence": 0.9,
            "is_simulated": False,
        }
        row, is_new = _upsert_event(db, fingerprint=fingerprint, values=values)
        (created if is_new else updated).append(row)

    # Resolve device findings whose device is now approved or no longer present.
    for event in db.scalars(
        select(SecurityEvent).where(
            SecurityEvent.event_type == "unapproved_device",
            SecurityEvent.status.in_(("Open", "Acknowledged")),
        )
    ).all():
        if event.device_id is None:
            continue
        device = db.get(Device, event.device_id)
        if device is None or device.is_approved or event.device_id not in current_rogue_ids:
            if device is not None and device.is_approved:
                event.status = "Resolved"
                event.resolved_at = now
                event.description = (
                    f"{event.description} The device was subsequently approved by an operator."
                )

    # --- Authentication failure bursts ------------------------------------
    failure_cutoff = now - FAILURE_WINDOW
    failures = db.execute(
        select(LoginAttempt.ip_address, func.count(LoginAttempt.id))
        .where(
            LoginAttempt.success.is_(False),
            LoginAttempt.attempted_at >= failure_cutoff,
        )
        .group_by(LoginAttempt.ip_address)
    ).all()
    active_bruteforce_ips: set[str] = set()
    for ip_address, count in failures:
        if count < FAILURE_THRESHOLD:
            continue
        active_bruteforce_ips.add(ip_address)
        fingerprint = f"brute_force:{ip_address}"
        values = {
            "event_type": "authentication_failure_burst",
            "category": "authentication",
            "severity": "High",
            "status": "Open",
            "source": "Sign-in audit",
            "device_id": None,
            "access_point_id": None,
            "title": "Repeated sign-in failures",
            "description": (
                f"{count} failed sign-in attempts from {ip_address} in the last "
                f"{int(FAILURE_WINDOW.total_seconds() // 60)} minutes."
            ),
            "evidence": {
                "ip_address": ip_address,
                "failed_attempts": count,
                "window_minutes": int(FAILURE_WINDOW.total_seconds() // 60),
            },
            "recommendation": (
                "Confirm whether the attempts are expected. If not, block the source address "
                "at the edge and review which accounts were targeted."
            ),
            "confidence": 0.92,
            "is_simulated": False,
        }
        row, is_new = _upsert_event(db, fingerprint=fingerprint, values=values)
        (created if is_new else updated).append(row)

    for event in db.scalars(
        select(SecurityEvent).where(
            SecurityEvent.event_type == "authentication_failure_burst",
            SecurityEvent.status.in_(("Open", "Acknowledged")),
        )
    ).all():
        ip_address = (event.evidence or {}).get("ip_address")
        if not ip_address or ip_address in active_bruteforce_ips:
            continue
        recent_failure = db.scalar(
            select(LoginAttempt.id).where(
                LoginAttempt.ip_address == ip_address,
                LoginAttempt.success.is_(False),
                LoginAttempt.attempted_at >= now - AUTO_RESOLVE_AFTER,
            )
        )
        if recent_failure is None:
            event.status = "Resolved"
            event.resolved_at = now

    # --- Host socket-table and throughput heuristics ----------------------
    # These signals describe the monitoring host's own socket table. They are
    # intentionally conservative and are never used to scan a remote system.
    host_agents = list(
        db.scalars(select(Agent).where(Agent.is_active.is_(True))).all()
    )
    host_windows: dict[int, list[HostMetric]] = {}
    intel_candidates: list[str] = []
    for agent in host_agents:
        rows = list(
            db.scalars(
                select(HostMetric)
                .where(
                    HostMetric.agent_id == agent.id,
                    HostMetric.data_origin == "live",
                    HostMetric.recorded_at >= now - HOST_WINDOW,
                )
                .order_by(HostMetric.recorded_at.asc())
                .limit(500)
            ).all()
        )
        if rows:
            host_windows[agent.id] = rows
            connections = (rows[-1].details or {}).get("connections") or {}
            ports_by_host = connections.get("distinct_ports_by_host") or {}
            if isinstance(ports_by_host, dict):
                for remote_ip, count in list(ports_by_host.items())[:10]:
                    try:
                        if int(count) >= PORT_FANOUT_THRESHOLD:
                            intel_candidates.append(str(remote_ip))
                    except (TypeError, ValueError):
                        continue
    intel_map: dict[str, dict] = {}
    if intel_candidates and settings.threat_intel_enabled:
        for result in enrich_many(db, intel_candidates):
            intel_map[result["indicator"]] = result

    active_host_fingerprints: set[str] = set()
    for agent_id, rows in host_windows.items():
        latest = rows[-1]
        details = latest.details or {}
        connections = details.get("connections") or {}
        ports_by_host = connections.get("distinct_ports_by_host") or {}

        if isinstance(ports_by_host, dict):
            for remote_ip, raw_count in list(ports_by_host.items())[:10]:
                try:
                    count = int(raw_count)
                except (TypeError, ValueError):
                    continue
                if count < PORT_FANOUT_THRESHOLD:
                    continue
                fingerprint = f"host_port_fanout:{agent_id}:{remote_ip}"
                active_host_fingerprints.add(fingerprint)
                intel = intel_map.get(str(remote_ip)) or {}
                severity = "High" if count >= PORT_FANOUT_HIGH else "Medium"
                values = {
                    "event_type": "host_port_fanout",
                    "category": "network",
                    "severity": severity,
                    "status": "Open",
                    "source": "Host socket table (psutil)",
                    "device_id": None,
                    "access_point_id": None,
                    "title": "High distinct-port fan-out observed",
                    "description": (
                        f"The monitoring host opened connections to {count} distinct "
                        f"destination ports on {remote_ip} within the observation window. "
                        "This may be normal client behaviour or an indicator of scanning."
                    ),
                    "evidence": {
                        "agent_id": agent_id,
                        "remote_ip": remote_ip,
                        "distinct_ports": count,
                        "observed_at": iso(latest.recorded_at),
                        "scope": "host socket table",
                        "threat_intel": intel or {"status": "disabled"},
                    },
                    "recommendation": (
                        "Confirm the application and user responsible for the connections. "
                        "If they are unexpected, isolate the host and review process and "
                        "firewall logs."
                    ),
                    "confidence": 0.7 if count < PORT_FANOUT_HIGH else 0.82,
                    "is_simulated": False,
                }
                row, is_new = _upsert_event(db, fingerprint=fingerprint, values=values)
                (created if is_new else updated).append(row)

        established_series: list[float] = []
        for row in rows[:-1]:
            value = ((row.details or {}).get("connections") or {}).get("established")
            if value is not None:
                established_series.append(float(value))
        current_established = connections.get("established")
        if (
            len(established_series) >= HOST_MIN_SAMPLES
            and current_established is not None
        ):
            baseline = median(established_series)
            if (
                baseline > 0
                and current_established >= CONNECTION_SPIKE_MINIMUM
                and current_established >= baseline * CONNECTION_SPIKE_MULTIPLIER
            ):
                fingerprint = f"host_connection_spike:{agent_id}"
                active_host_fingerprints.add(fingerprint)
                values = {
                    "event_type": "host_connection_spike",
                    "category": "network",
                    "severity": "Medium",
                    "status": "Open",
                    "source": "Host socket table (psutil)",
                    "device_id": None,
                    "access_point_id": None,
                    "title": "Connection volume spike",
                    "description": (
                        f"Established connections reached {int(current_established)} versus a "
                        f"baseline median of {baseline:.0f} on this monitored host."
                    ),
                    "evidence": {
                        "agent_id": agent_id,
                        "established": int(current_established),
                        "baseline_median": round(baseline, 1),
                        "observed_at": iso(latest.recorded_at),
                    },
                    "recommendation": (
                        "Correlate the spike with application activity. A sustained unexplained "
                        "increase can indicate abuse or a runaway client."
                    ),
                    "confidence": 0.66,
                    "is_simulated": False,
                }
                row, is_new = _upsert_event(db, fingerprint=fingerprint, values=values)
                (created if is_new else updated).append(row)

        traffic_series: list[float] = []
        for row in rows[:-1]:
            download = row.download_bps or 0
            upload = row.upload_bps or 0
            if row.download_bps is not None or row.upload_bps is not None:
                traffic_series.append(float(download + upload))
        current_traffic = float((latest.download_bps or 0) + (latest.upload_bps or 0))
        if len(traffic_series) >= HOST_MIN_SAMPLES and current_traffic >= TRAFFIC_SPIKE_MIN_BPS:
            baseline = median(traffic_series)
            if baseline > 0 and current_traffic >= baseline * TRAFFIC_SPIKE_MULTIPLIER:
                fingerprint = f"host_traffic_spike:{agent_id}"
                active_host_fingerprints.add(fingerprint)
                values = {
                    "event_type": "host_traffic_spike",
                    "category": "network",
                    "severity": "Medium",
                    "status": "Open",
                    "source": "Host interface counters (psutil)",
                    "device_id": None,
                    "access_point_id": None,
                    "title": "Sudden throughput spike",
                    "description": (
                        f"Host throughput reached {current_traffic / 1_000_000:.2f} MB/s, "
                        f"{current_traffic / baseline:.1f}x the recent median of "
                        f"{baseline / 1_000_000:.2f} MB/s."
                    ),
                    "evidence": {
                        "agent_id": agent_id,
                        "current_bps": round(current_traffic, 2),
                        "baseline_bps": round(baseline, 2),
                        "observed_at": iso(latest.recorded_at),
                    },
                    "recommendation": (
                        "Confirm whether a backup, update or media transfer explains the spike. "
                        "Investigate unexpected sustained transfer."
                    ),
                    "confidence": 0.6,
                    "is_simulated": False,
                }
                row, is_new = _upsert_event(db, fingerprint=fingerprint, values=values)
                (created if is_new else updated).append(row)

    for event in db.scalars(
        select(SecurityEvent).where(
            SecurityEvent.event_type.in_(
                ("host_port_fanout", "host_connection_spike", "host_traffic_spike")
            ),
            SecurityEvent.status.in_(("Open", "Acknowledged")),
        )
    ).all():
        if event.fingerprint not in active_host_fingerprints:
            event.status = "Resolved"
            event.resolved_at = now
            event.description = (
                f"{event.description} The condition was not present in the latest scan window."
            )

    _record_scan_timestamp(db, now)

    for row in created:
        if row.severity in {"Critical", "High"}:
            notify(
                db,
                kind="security",
                severity=row.severity,
                title=row.title,
                message=row.description,
                path="/security",
                dedupe_key=f"security:{row.fingerprint}:{now:%Y-%m-%d}",
                metadata={"security_event_id": row.id, "event_type": row.event_type},
            )
    db.commit()

    feed = security_feed(db)
    feed.update(
        {
            "status": "ready",
            "message": (
                f"Scan complete: {len(created)} new finding(s), {len(updated)} existing "
                f"finding(s) refreshed."
            ),
            "last_scan_at": now.isoformat(),
            "method": METHOD,
            "disclaimer": DISCLAIMER,
        }
    )
    return feed


def security_feed(db: Session, limit: int = 100) -> dict:
    rows = list(
        db.scalars(
            select(SecurityEvent)
            .order_by(SecurityEvent.detected_at.desc())
            .limit(limit)
        ).all()
    )
    events = [_serialise(row) for row in rows]
    open_events = [item for item in events if item["status"] in {"Open", "Acknowledged"}]
    last_scan = _scan_timestamp(db)

    unapproved = (
        db.scalar(
            select(func.count(Device.id)).where(
                Device.is_approved.is_(False), Device.is_active.is_(True)
            )
        )
        or 0
    )
    failed_24h = (
        db.scalar(
            select(func.count(LoginAttempt.id)).where(
                LoginAttempt.success.is_(False),
                LoginAttempt.attempted_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            )
        )
        or 0
    )
    deductions = {"Critical": 25, "High": 15, "Medium": 7, "Low": 3}
    posture = clamp(
        100
        - sum(deductions.get(item["severity"], 3) for item in open_events if item["status"] == "Open"),
        0,
        100,
    )
    if last_scan is None:
        status = "not_run"
        message = "Run a defensive scan to assess the locally observable security signals."
    else:
        status = "ready"
        message = (
            "No open defensive findings."
            if not open_events
            else f"{len(open_events)} open finding(s) require review."
        )

    return {
        "status": status,
        "message": message,
        "events": events,
        "summary": {
            "open": len(open_events),
            "acknowledged": sum(1 for item in events if item["status"] == "Acknowledged"),
            "resolved": sum(1 for item in events if item["status"] == "Resolved"),
            "critical": sum(1 for item in open_events if item["severity"] == "Critical"),
            "high": sum(1 for item in open_events if item["severity"] == "High"),
            "medium": sum(1 for item in open_events if item["severity"] == "Medium"),
            "low": sum(1 for item in open_events if item["severity"] == "Low"),
            "unapproved_devices": unapproved,
            "failed_logins_24h": failed_24h,
            "posture_score": round(posture, 1),
        },
        "last_scan_at": iso(last_scan),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": METHOD,
        "threat_intel": threat_intel_status(db),
        "disclaimer": DISCLAIMER,
    }


def resolve_security_event(db: Session, event_id: int, status: str = "Resolved") -> dict | None:
    row = db.get(SecurityEvent, event_id)
    if row is None:
        return None
    row.status = status
    row.resolved_at = datetime.now(timezone.utc) if status == "Resolved" else None
    db.commit()
    db.refresh(row)
    return _serialise(row)
