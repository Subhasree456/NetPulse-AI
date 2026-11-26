"""Unified topology derived from persisted observations.

The graph is deliberately honest: most edges are *inferred* from subnet
membership, gateway configuration and agent registration, because NetPulse does
not perform LLDP/SNMP discovery or packet capture. Every edge carries
``inferred: true`` and the response states the method.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.intelligence.common import aware, iso
from app.models import AccessPoint, Agent, Device, HostMetric, NetworkMeasurement
from app.monitoring.host_agent import effective_status
from app.monitoring.internet import default_gateway

router = APIRouter(prefix="/topology", tags=["Topology"])


def _same_subnet(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        a = ipaddress.ip_address(left)
        b = ipaddress.ip_address(right)
    except ValueError:
        return False
    if a.version != 4 or b.version != 4:
        return False
    return ipaddress.ip_network(f"{a}/24", strict=False) == ipaddress.ip_network(f"{b}/24", strict=False)


@router.get("")
def topology(db: DbSession, _: CurrentUser) -> dict:
    gateway = default_gateway()
    latest_measurement = db.scalar(
        select(NetworkMeasurement)
        .where(NetworkMeasurement.data_origin == "live")
        .order_by(NetworkMeasurement.recorded_at.desc())
        .limit(1)
    )
    agents = list(db.scalars(select(Agent).order_by(Agent.kind.asc(), Agent.id.asc())).all())
    devices = list(
        db.scalars(select(Device).order_by(Device.last_seen_at.desc()).limit(80)).all()
    )
    access_points = list(db.scalars(select(AccessPoint).order_by(AccessPoint.id.asc())).all())

    local_agent = next((agent for agent in agents if agent.kind == "local"), None)
    local_metric = (
        db.scalar(
            select(HostMetric)
            .where(HostMetric.agent_id == local_agent.id)
            .order_by(HostMetric.recorded_at.desc())
            .limit(1)
        )
        if local_agent
        else None
    )

    nodes: list[dict] = []
    edges: list[dict] = []

    nodes.append(
        {
            "id": "internet",
            "label": f"Internet · {settings.monitor_target}",
            "kind": "internet",
            "status": "online" if latest_measurement and latest_measurement.is_online else "unknown",
            "detail": {
                "latency_ms": latest_measurement.latency_ms if latest_measurement else None,
                "packet_loss_pct": latest_measurement.packet_loss_pct if latest_measurement else None,
                "recorded_at": iso(latest_measurement.recorded_at) if latest_measurement else None,
            },
        }
    )
    if gateway:
        nodes.append(
            {
                "id": "gateway",
                "label": f"Default gateway · {gateway}",
                "kind": "gateway",
                "status": "online" if latest_measurement and latest_measurement.is_online else "unknown",
                "detail": {"ip_address": gateway, "source": "operating-system routing table"},
            }
        )
        edges.append(
            {
                "source": "gateway",
                "target": "internet",
                "label": "uplink",
                "inferred": True,
                "reason": "The host routes internet probes through its default gateway.",
            }
        )

    host_ip = (
        (local_metric.details or {}).get("interfaces", [{}])[0].get("addresses", [None])[0]
        if local_metric
        else None
    )
    nodes.append(
        {
            "id": "host",
            "label": local_agent.name if local_agent else "Monitoring host",
            "kind": "host",
            "status": effective_status(local_agent) if local_agent else "unknown",
            "detail": {
                "ip_address": host_ip,
                "cpu_pct": local_metric.cpu_pct if local_metric else None,
                "memory_pct": local_metric.memory_pct if local_metric else None,
                "disk_pct": local_metric.disk_pct if local_metric else None,
                "recorded_at": iso(local_metric.recorded_at) if local_metric else None,
            },
        }
    )
    if gateway:
        edges.append(
            {
                "source": "host",
                "target": "gateway",
                "label": "local segment",
                "inferred": True,
                "reason": "Host address and default gateway are read from the local OS.",
            }
        )

    remote_agents = [agent for agent in agents if agent.kind == "remote"]
    for agent in remote_agents:
        node_id = f"agent-{agent.id}"
        nodes.append(
            {
                "id": node_id,
                "label": agent.name,
                "kind": "agent",
                "status": effective_status(agent),
                "detail": {
                    "hostname": agent.hostname,
                    "ip_address": agent.ip_address,
                    "last_seen_at": iso(agent.last_seen_at),
                },
            }
        )
        edges.append(
            {
                "source": node_id,
                "target": "host",
                "label": "telemetry (HTTPS)",
                "inferred": False,
                "reason": "Agent is registered and posts telemetry to the NetPulse API.",
            }
        )

    for ap in access_points:
        node_id = f"ap-{ap.id}"
        nodes.append(
            {
                "id": node_id,
                "label": ap.name,
                "kind": "access_point",
                "status": ap.status,
                "detail": {
                    "ip_address": ap.ip_address,
                    "location": ap.location,
                    "simulated": ap.is_simulated,
                    "last_checked_at": iso(ap.last_checked_at),
                },
            }
        )
        if gateway and _same_subnet(ap.ip_address, gateway):
            edges.append(
                {
                    "source": node_id,
                    "target": "gateway",
                    "label": "same subnet",
                    "inferred": True,
                    "reason": "Access point address shares the gateway /24.",
                }
            )

    for device in devices:
        node_id = f"device-{device.id}"
        nodes.append(
            {
                "id": node_id,
                "label": device.display_name or device.hostname or device.ip_address,
                "kind": "device",
                "status": "approved" if device.is_approved else "unrecognized",
                "detail": {
                    "ip_address": device.ip_address,
                    "mac_masked": device.mac_masked,
                    "hostname": device.hostname,
                    "last_seen_at": iso(device.last_seen_at),
                    "approved": device.is_approved,
                },
            }
        )
        target = "gateway" if gateway and _same_subnet(device.ip_address, gateway) else "host"
        edges.append(
            {
                "source": node_id,
                "target": target,
                "label": "ARP neighbour",
                "inferred": True,
                "reason": "Observed in the read-only local ARP cache.",
            }
        )

    counts: dict[str, int] = {}
    for node in nodes:
        counts[node["kind"]] = counts.get(node["kind"], 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "by_kind": counts,
            "gateway": gateway,
            "internet_online": bool(latest_measurement and latest_measurement.is_online),
            "approved_devices": sum(1 for device in devices if device.is_approved),
            "unrecognized_devices": sum(1 for device in devices if not device.is_approved),
        },
        "method": "Subnet, gateway, ARP and registration correlation",
        "is_inferred": True,
        "notes": [
            "Most links are inferred from /24 subnet membership and the operating system routing table.",
            "NetPulse does not perform LLDP, SNMP or packet capture, so this is not a physical topology map.",
            "Simulated access points are labelled in their node detail.",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
