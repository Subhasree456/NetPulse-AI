"""Real host telemetry collection with psutil.

This module collects only operating-system measurements from the machine running
the NetPulse backend (the "local system" agent). Remote agents post the same
shape of payload over an authenticated API. Nothing is fabricated: if psutil
cannot read a metric, the field is stored as NULL and the observation is marked
``partial``.

Connection details are read from the host's own socket table. They are used for
defensive heuristics (connection volume, distinct-port fan-out) and are never
used to perform scanning or packet capture.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import platform
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

import psutil
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.intelligence.common import iso
from app.models import Agent, HostMetric, NetworkMeasurement

logger = logging.getLogger("netpulse.host_agent")

LOCAL_KIND = "local"
REMOTE_KIND = "remote"
MAX_ENDPOINTS = 25
MAX_TOP_PROCESSES = 6


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_agent_token() -> tuple[str, str, str]:
    """Return (plaintext token, hash, prefix). Plaintext is shown exactly once."""
    token = f"np_{secrets.token_urlsafe(32)}"
    return token, hash_token(token), token[:12]


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def agent_is_online(agent: Agent, now: datetime | None = None) -> bool:
    if not agent.is_active or agent.last_seen_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    seen = aware(agent.last_seen_at)
    return bool(seen and now - seen <= timedelta(seconds=settings.host_offline_after_seconds))


def effective_status(agent: Agent, now: datetime | None = None) -> str:
    if not agent.is_active:
        return "disabled"
    if agent.last_seen_at is None:
        return "never"
    return "online" if agent_is_online(agent, now) else "offline"


def ensure_local_agent(db: Session) -> Agent:
    """Return the single local-system agent, creating it on first use."""
    agent = db.scalar(select(Agent).where(Agent.kind == LOCAL_KIND).order_by(Agent.id.asc()))
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc)
    if agent is None:
        token, token_hash, prefix = new_agent_token()
        agent = Agent(
            name=f"{hostname} · Local system",
            kind=LOCAL_KIND,
            hostname=hostname,
            ip_address=_primary_ip(),
            os_name=f"{platform.system()} {platform.release()}",
            agent_version="local-psutil-1.0",
            token_hash=token_hash,
            token_prefix=prefix,
            status="online",
            last_seen_at=now,
            is_active=True,
            metadata_json={
                "scope": "backend-host",
                "collection": "psutil",
                "authorized": True,
                "note": "Automatically registered local monitoring source.",
            },
        )
        db.add(agent)
        db.flush()
    else:
        agent.hostname = hostname
        agent.ip_address = _primary_ip()
        agent.os_name = f"{platform.system()} {platform.release()}"
        agent.last_seen_at = now
        agent.status = "online"
    return agent


def _primary_ip() -> str | None:
    try:
        for addr_list in psutil.net_if_addrs().values():
            for addr in addr_list:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    return addr.address
    except Exception:
        pass
    return None


def _safe(value, fallback=None):
    try:
        return value()
    except Exception:
        return fallback


def _temperature() -> float | None:
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:
        return None
    try:
        groups = reader()
    except Exception:
        return None
    values: list[float] = []
    for entries in (groups or {}).values():
        for entry in entries:
            current = getattr(entry, "current", None)
            if current is not None and 0 < float(current) < 150:
                values.append(float(current))
    return round(max(values), 1) if values else None


def _top_processes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for process in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
            info = process.info
            rows.append(
                {
                    "pid": info.get("pid"),
                    "name": (info.get("name") or "unknown")[:80],
                    "memory_pct": round(float(info.get("memory_percent") or 0), 2),
                    "cpu_pct": round(float(info.get("cpu_percent") or 0), 2),
                }
            )
    except Exception:
        return []
    rows.sort(key=lambda item: item["memory_pct"], reverse=True)
    return rows[:MAX_TOP_PROCESSES]


def _interface_summary() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        addresses = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for name, addr_list in addresses.items():
            rendered = [
                addr.address
                for addr in addr_list
                if addr.family in (socket.AF_INET, socket.AF_INET6)
            ]
            if not rendered:
                continue
            stat = stats.get(name)
            rows.append(
                {
                    "name": name,
                    "addresses": rendered[:4],
                    "is_up": bool(stat.isup) if stat else False,
                    "speed_mbps": stat.speed if stat and stat.speed else None,
                }
            )
    except Exception:
        return []
    rows.sort(key=lambda item: (not item["is_up"], item["name"]))
    return rows[:8]


def _disk_summary() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for part in psutil.disk_partitions(all=False)[:8]:
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            rows.append(
                {
                    "mount": part.mountpoint,
                    "filesystem": part.fstype,
                    "used_gb": round(usage.used / 1_073_741_824, 2),
                    "total_gb": round(usage.total / 1_073_741_824, 2),
                    "used_pct": round(usage.percent, 2),
                }
            )
    except Exception:
        return []
    return rows


def _connection_summary() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "total": 0,
        "established": 0,
        "listen": 0,
        "public_remote_endpoints": [],
        "distinct_ports_by_host": {},
        "collection_status": "ok",
    }
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception as exc:
        summary["collection_status"] = f"unavailable: {type(exc).__name__}"
        return summary

    summary["total"] = len(connections)
    summary["established"] = sum(1 for conn in connections if conn.status == psutil.CONN_ESTABLISHED)
    summary["listen"] = sum(1 for conn in connections if conn.status == psutil.CONN_LISTEN)
    endpoints: dict[tuple[str, int], dict[str, Any]] = {}
    ports_by_host: dict[str, set[int]] = {}
    for conn in connections:
        if not conn.raddr:
            continue
        ip = str(conn.raddr.ip)
        port = int(conn.raddr.port)
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not address.is_global:
            continue
        ports_by_host.setdefault(ip, set()).add(port)
        endpoints[(ip, port)] = {
            "ip": ip,
            "port": port,
            "status": conn.status,
        }
    summary["public_remote_endpoints"] = list(endpoints.values())[:MAX_ENDPOINTS]
    summary["distinct_ports_by_host"] = {
        ip: len(ports) for ip, ports in sorted(ports_by_host.items(), key=lambda item: len(item[1]), reverse=True)[:10]
    }
    return summary


def collect_host_snapshot() -> dict[str, Any]:
    """Collect one real host observation. Individual failures become NULL fields."""
    status = "ok"
    try:
        cpu = float(psutil.cpu_percent(interval=0.2))
    except Exception:
        cpu = None
        status = "partial"
    try:
        memory = psutil.virtual_memory()
        memory_pct = float(memory.percent)
        memory_total = int(memory.total)
    except Exception:
        memory_pct = None
        memory_total = None
        status = "partial"
    try:
        swap = psutil.swap_memory()
        swap_pct = float(swap.percent)
    except Exception:
        swap_pct = None

    disk_pct = None
    try:
        root = os.path.abspath(os.sep)
        disk_pct = float(psutil.disk_usage(root).percent)
    except Exception:
        status = "partial"

    load_1 = load_5 = load_15 = None
    getloadavg = getattr(psutil, "getloadavg", None)
    if getloadavg is not None:
        try:
            load_1, load_5, load_15 = (round(float(value), 3) for value in getloadavg())
        except Exception:
            pass

    try:
        uptime = max(0.0, datetime.now(timezone.utc).timestamp() - psutil.boot_time())
    except Exception:
        uptime = None

    process_count = None
    try:
        process_count = len(psutil.pids())
    except Exception:
        status = "partial"

    counters = None
    try:
        counters = psutil.net_io_counters()
    except Exception:
        status = "partial"

    link_speed = None
    try:
        speeds = [
            stat.speed
            for stat in psutil.net_if_stats().values()
            if stat.isup and stat.speed and stat.speed > 0
        ]
        link_speed = float(max(speeds)) if speeds else None
    except Exception:
        pass

    return {
        "cpu_pct": round(cpu, 2) if cpu is not None else None,
        "memory_pct": round(memory_pct, 2) if memory_pct is not None else None,
        "disk_pct": round(disk_pct, 2) if disk_pct is not None else None,
        "swap_pct": round(swap_pct, 2) if swap_pct is not None else None,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "uptime_seconds": round(uptime, 1) if uptime is not None else None,
        "process_count": process_count,
        "temperature_c": _temperature(),
        "bytes_sent_total": int(counters.bytes_sent) if counters else None,
        "bytes_recv_total": int(counters.bytes_recv) if counters else None,
        "link_speed_mbps": link_speed,
        "status": status,
        "details": {
            "interfaces": _interface_summary(),
            "disks": _disk_summary(),
            "top_processes": _top_processes(),
            "connections": _connection_summary(),
            "memory_total_bytes": memory_total,
            "platform": f"{platform.system()} {platform.release()}",
            "hostname": socket.gethostname(),
            "collection_scope": "host",
        },
    }


def _rates(previous: HostMetric | None, snapshot: dict[str, Any], now: datetime) -> dict[str, float | None]:
    if previous is None or snapshot.get("bytes_sent_total") is None or snapshot.get("bytes_recv_total") is None:
        return {"upload_bps": None, "download_bps": None, "net_utilization_pct": None}
    recorded = aware(previous.recorded_at)
    elapsed = (now - recorded).total_seconds() if recorded else 0
    if not 0 < elapsed <= 900:
        return {"upload_bps": None, "download_bps": None, "net_utilization_pct": None}
    sent = snapshot["bytes_sent_total"] - int(previous.bytes_sent_total or 0)
    received = snapshot["bytes_recv_total"] - int(previous.bytes_recv_total or 0)
    if sent < 0 or received < 0:
        return {"upload_bps": None, "download_bps": None, "net_utilization_pct": None}
    upload = round(sent / elapsed, 2)
    download = round(received / elapsed, 2)
    total_bps = (upload + download) * 8
    speed = snapshot.get("link_speed_mbps") or 0
    utilization = round(total_bps / (speed * 1_000_000) * 100, 3) if speed else None
    return {"upload_bps": upload, "download_bps": download, "net_utilization_pct": utilization}


def _latest_network_measurement(db: Session) -> NetworkMeasurement | None:
    return db.scalar(
        select(NetworkMeasurement)
        .where(NetworkMeasurement.data_origin == "live")
        .order_by(NetworkMeasurement.recorded_at.desc())
        .limit(1)
    )


def record_host_metric(
    db: Session,
    agent: Agent,
    snapshot: dict[str, Any],
    *,
    source: str = "psutil",
    data_origin: str = "live",
    agent_recorded_at: datetime | None = None,
) -> HostMetric:
    now = datetime.now(timezone.utc)
    previous = db.scalar(
        select(HostMetric)
        .where(HostMetric.agent_id == agent.id)
        .order_by(HostMetric.recorded_at.desc())
        .limit(1)
    )
    rates = _rates(previous, snapshot, now)
    for key in ("upload_bps", "download_bps", "net_utilization_pct"):
        if snapshot.get(key) is not None:
            rates[key] = snapshot[key]
    latest_network = _latest_network_measurement(db)
    metric = HostMetric(
        agent_id=agent.id,
        recorded_at=now,
        agent_recorded_at=agent_recorded_at,
        cpu_pct=snapshot.get("cpu_pct"),
        memory_pct=snapshot.get("memory_pct"),
        disk_pct=snapshot.get("disk_pct"),
        swap_pct=snapshot.get("swap_pct"),
        load_1=snapshot.get("load_1"),
        load_5=snapshot.get("load_5"),
        load_15=snapshot.get("load_15"),
        uptime_seconds=snapshot.get("uptime_seconds"),
        process_count=snapshot.get("process_count"),
        temperature_c=snapshot.get("temperature_c"),
        bytes_sent_total=snapshot.get("bytes_sent_total"),
        bytes_recv_total=snapshot.get("bytes_recv_total"),
        upload_bps=rates["upload_bps"],
        download_bps=rates["download_bps"],
        net_utilization_pct=rates["net_utilization_pct"],
        latency_ms=(
            snapshot.get("latency_ms")
            if snapshot.get("latency_ms") is not None
            else (latest_network.latency_ms if latest_network else None)
        ),
        packet_loss_pct=(
            snapshot.get("packet_loss_pct")
            if snapshot.get("packet_loss_pct") is not None
            else (latest_network.packet_loss_pct if latest_network else None)
        ),
        source=source,
        data_origin=data_origin,
        status=str(snapshot.get("status") or "ok"),
        details={
            **(snapshot.get("details") or {}),
            "link_speed_mbps": snapshot.get("link_speed_mbps"),
            "network_scope": "ICMP latency/loss is measured network-wide by the probe engine",
        },
    )
    db.add(metric)
    agent.last_seen_at = now
    agent.status = "online"
    db.flush()
    return metric


def collect_local_metrics(db: Session) -> HostMetric:
    agent = ensure_local_agent(db)
    snapshot = collect_host_snapshot()
    metric = record_host_metric(db, agent, snapshot, source="psutil")
    _prune_old_metrics(db)
    db.commit()
    db.refresh(metric)
    return metric


def _prune_old_metrics(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.host_metric_retention_days)
    db.execute(delete(HostMetric).where(HostMetric.recorded_at < cutoff))


def metric_as_dict(metric: HostMetric | None) -> dict | None:
    if metric is None:
        return None
    return {
        "id": metric.id,
        "agent_id": metric.agent_id,
        "recorded_at": iso(metric.recorded_at),
        "agent_recorded_at": iso(metric.agent_recorded_at),
        "cpu_pct": metric.cpu_pct,
        "memory_pct": metric.memory_pct,
        "disk_pct": metric.disk_pct,
        "swap_pct": metric.swap_pct,
        "load_1": metric.load_1,
        "load_5": metric.load_5,
        "load_15": metric.load_15,
        "uptime_seconds": metric.uptime_seconds,
        "process_count": metric.process_count,
        "temperature_c": metric.temperature_c,
        "bytes_sent_total": metric.bytes_sent_total,
        "bytes_recv_total": metric.bytes_recv_total,
        "upload_bps": metric.upload_bps,
        "download_bps": metric.download_bps,
        "net_utilization_pct": metric.net_utilization_pct,
        "latency_ms": metric.latency_ms,
        "packet_loss_pct": metric.packet_loss_pct,
        "source": metric.source,
        "data_origin": metric.data_origin,
        "status": metric.status,
        "details": metric.details or {},
    }
