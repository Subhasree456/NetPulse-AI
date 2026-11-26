"""Network and internet connectivity checks.

The checks in this module are ordinary DNS lookups and TCP handshakes to
targets configured by the administrator. They are used for availability and
response-time measurements only. No scanning, probing of arbitrary ports, or
packet capture is performed.
"""

from __future__ import annotations

import ipaddress
import logging
import platform
import re
import socket
import subprocess
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.intelligence.common import iso
from app.models import NetworkProbe

logger = logging.getLogger("netpulse.internet")


def dns_check(host: str, attempts: int = 3) -> dict:
    times: list[float] = []
    errors: list[str] = []
    addresses: list[str] = []
    for _ in range(max(1, attempts)):
        started = time.perf_counter()
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            elapsed = (time.perf_counter() - started) * 1000
            times.append(round(elapsed, 2))
            for info in infos[:4]:
                address = info[4][0]
                if address not in addresses:
                    addresses.append(address)
        except OSError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    success = bool(times)
    return {
        "protocol": "dns",
        "target": host,
        "port": 443,
        "success": success,
        "latency_ms": round(sum(times) / len(times), 2) if times else None,
        "packet_loss_pct": round((attempts - len(times)) / attempts * 100, 2),
        "error_message": None if success else (errors[-1] if errors else "DNS lookup failed"),
        "detail": {
            "attempts": attempts,
            "resolved_addresses": addresses,
            "resolver": "operating-system resolver",
        },
    }


def tcp_check(host: str, port: int, attempts: int = 3, timeout: float = 2.0) -> dict:
    times: list[float] = []
    errors: list[str] = []
    for _ in range(max(1, attempts)):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                elapsed = (time.perf_counter() - started) * 1000
                times.append(round(elapsed, 2))
        except OSError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    success = bool(times)
    return {
        "protocol": "tcp",
        "target": host,
        "port": port,
        "success": success,
        "latency_ms": round(sum(times) / len(times), 2) if times else None,
        "packet_loss_pct": round((attempts - len(times)) / attempts * 100, 2),
        "error_message": None if success else (errors[-1] if errors else "TCP connect failed"),
        "detail": {"attempts": attempts, "timeout_seconds": timeout},
    }


def parse_tcp_targets(raw: str) -> list[tuple[str, int]]:
    targets: list[tuple[str, int]] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            continue
        host, port_text = item.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            continue
        if not host or not 0 < port < 65536:
            continue
        targets.append((host.strip(), port))
    return targets[:8]


def _record(db: Session, result: dict) -> NetworkProbe:
    row = NetworkProbe(
        protocol=result["protocol"],
        target=result["target"],
        port=result.get("port"),
        success=bool(result["success"]),
        latency_ms=result.get("latency_ms"),
        packet_loss_pct=result.get("packet_loss_pct"),
        error_message=result.get("error_message"),
        detail=result.get("detail") or {},
        data_origin="live",
        recorded_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def run_network_probes(db: Session) -> dict:
    """Run the configured DNS and TCP checks and persist real observations."""
    results: list[dict] = []
    if settings.network_dns_target.strip():
        results.append(dns_check(settings.network_dns_target.strip()))
    for host, port in parse_tcp_targets(settings.network_tcp_targets):
        results.append(tcp_check(host, port))
    rows = [_record(db, result) for result in results]
    db.commit()
    return {
        "results": [
            {
                **result,
                "id": row.id,
                "recorded_at": iso(row.recorded_at),
            }
            for result, row in zip(results, rows)
        ],
        "successful": sum(1 for result in results if result["success"]),
        "total": len(results),
        "message": f"Ran {len(results)} real DNS/TCP connectivity check(s).",
    }


def latest_probes(db: Session, limit_per_protocol: int = 1) -> dict[str, list[dict]]:
    output: dict[str, list[dict]] = {}
    for protocol in ("dns", "tcp"):
        rows = db.scalars(
            select(NetworkProbe)
            .where(NetworkProbe.protocol == protocol)
            .order_by(NetworkProbe.recorded_at.desc())
            .limit(limit_per_protocol)
        ).all()
        output[protocol] = [
            {
                "id": row.id,
                "protocol": row.protocol,
                "target": row.target,
                "port": row.port,
                "success": row.success,
                "latency_ms": row.latency_ms,
                "packet_loss_pct": row.packet_loss_pct,
                "error_message": row.error_message,
                "detail": row.detail or {},
                "data_origin": row.data_origin,
                "recorded_at": iso(row.recorded_at),
            }
            for row in rows
        ]
    return output


def probe_history(db: Session, hours: int = 24, limit: int = 1000) -> list[dict]:
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.scalars(
        select(NetworkProbe)
        .where(NetworkProbe.recorded_at >= cutoff)
        .order_by(NetworkProbe.recorded_at.asc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "protocol": row.protocol,
            "target": row.target,
            "port": row.port,
            "success": row.success,
            "latency_ms": row.latency_ms,
            "packet_loss_pct": row.packet_loss_pct,
            "error_message": row.error_message,
            "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        }
        for row in rows
    ]


def default_gateway() -> str | None:
    """Read the host's default gateway without changing any configuration."""
    system = platform.system().lower()
    try:
        if system == "windows":
            completed = subprocess.run(
                ["ipconfig"], capture_output=True, text=True, timeout=6, shell=False, check=False
            )
            for line in completed.stdout.splitlines():
                if "default gateway" in line.lower():
                    candidate = line.split(":", 1)[-1].strip()
                    try:
                        ipaddress.ip_address(candidate)
                        return candidate
                    except ValueError:
                        continue
        elif system == "darwin":
            completed = subprocess.run(
                ["route", "-n", "get", "default"],
                capture_output=True,
                text=True,
                timeout=6,
                shell=False,
                check=False,
            )
            match = re.search(r"gateway:\s*([0-9a-fA-F:.]+)", completed.stdout)
            if match:
                return match.group(1)
        else:
            completed = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=6,
                shell=False,
                check=False,
            )
            match = re.search(r"default\s+via\s+([0-9a-fA-F:.]+)", completed.stdout)
            if match:
                return match.group(1)
    except Exception:
        logger.debug("Default gateway could not be read", exc_info=True)
    return None
