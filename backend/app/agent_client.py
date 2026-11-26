"""Standalone NetPulse remote monitoring agent.

Run this on any authorised Windows, Linux or macOS host that can reach the
NetPulse API:

    set NETPULSE_API_URL=https://netpulse.example.com
    set NETPULSE_AGENT_TOKEN=np_xxx
    python -m app.agent_client

The agent collects real psutil telemetry, posts it to /api/agents/telemetry and
sends a heartbeat every five minutes. It never runs commands, scans remote hosts
or changes configuration. Network errors are retried with exponential backoff so
the agent reconnects automatically after an outage.

Environment:
    NETPULSE_API_URL          Base API URL (default http://127.0.0.1:8000)
    NETPULSE_AGENT_TOKEN      Token shown once when the agent is registered
    NETPULSE_AGENT_INTERVAL   Seconds between samples (default 60, minimum 15)
    NETPULSE_PING_TARGET      Optional target for one ICMP latency measurement
    NETPULSE_VERIFY_TLS       "1" or "0" (default 1; set 0 only for a self-signed lab API)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app.monitoring.diagnostics import perform_diagnostic
from app.monitoring.host_agent import collect_host_snapshot

logger = logging.getLogger("netpulse.agent")


def _post(url: str, token: str, payload: dict, verify_tls: bool, timeout: float = 15.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Token": token,
            "User-Agent": f"NetPulse-Agent/1.0 ({platform.system()})",
        },
    )
    context = None
    if url.startswith("https://") and not verify_tls:
        context = ssl._create_unverified_context()  # noqa: S323 - explicit lab opt-out
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def build_payload(snapshot: dict, ping_target: str | None) -> dict:
    payload = {
        "hostname": snapshot["details"].get("hostname") or socket.gethostname(),
        "os_name": snapshot["details"].get("platform"),
        "agent_version": "remote-agent-1.0",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "cpu_pct": snapshot.get("cpu_pct"),
        "memory_pct": snapshot.get("memory_pct"),
        "disk_pct": snapshot.get("disk_pct"),
        "swap_pct": snapshot.get("swap_pct"),
        "load_1": snapshot.get("load_1"),
        "load_5": snapshot.get("load_5"),
        "load_15": snapshot.get("load_15"),
        "uptime_seconds": snapshot.get("uptime_seconds"),
        "process_count": snapshot.get("process_count"),
        "temperature_c": snapshot.get("temperature_c"),
        "bytes_sent_total": snapshot.get("bytes_sent_total"),
        "bytes_recv_total": snapshot.get("bytes_recv_total"),
        "status": snapshot.get("status") or "ok",
        "details": {
            "interfaces": (snapshot.get("details") or {}).get("interfaces", [])[:8],
            "disks": (snapshot.get("details") or {}).get("disks", [])[:8],
            "top_processes": (snapshot.get("details") or {}).get("top_processes", [])[:6],
            "connections": (snapshot.get("details") or {}).get("connections", {}),
            "link_speed_mbps": snapshot.get("link_speed_mbps"),
            "agent_platform": platform.platform(),
        },
    }
    if ping_target:
        result = perform_diagnostic(ping_target)
        if result.is_online:
            payload["latency_ms"] = result.latency_ms
            payload["packet_loss_pct"] = result.packet_loss_pct
            payload["details"]["ping_target"] = ping_target
        else:
            payload["packet_loss_pct"] = 100.0
            payload["details"]["ping_error"] = result.error_message
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NetPulse remote monitoring agent")
    parser.add_argument(
        "--api-url",
        default=os.getenv("NETPULSE_API_URL", "http://127.0.0.1:8000"),
        help="NetPulse API base URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("NETPULSE_AGENT_TOKEN", ""),
        help="Agent token from the registration response",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("NETPULSE_AGENT_INTERVAL", "60")),
        help="Seconds between telemetry samples (minimum 15)",
    )
    parser.add_argument(
        "--ping-target",
        default=os.getenv("NETPULSE_PING_TARGET", ""),
        help="Optional ICMP target for a latency measurement",
    )
    parser.add_argument("--once", action="store_true", help="Send one sample and exit")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
    )
    if not args.token:
        logger.error(
            "No agent token supplied. Set NETPULSE_AGENT_TOKEN or pass --token. "
            "Create the agent in Settings → Monitoring agents and copy the token once."
        )
        return 2
    interval = max(15, args.interval)
    base = args.api_url.rstrip("/")
    telemetry_url = f"{base}/api/agents/telemetry"
    heartbeat_url = f"{base}/api/agents/heartbeat"
    verify_tls = os.getenv("NETPULSE_VERIFY_TLS", "1") not in {"0", "false", "False"}
    ping_target = args.ping_target.strip() or None

    logger.info("Starting agent for %s → %s", socket.gethostname(), base)
    backoff = interval
    last_heartbeat = 0.0
    while True:
        try:
            snapshot = collect_host_snapshot()
            payload = build_payload(snapshot, ping_target)
            result = _post(telemetry_url, args.token, payload, verify_tls)
            logger.info(
                "Telemetry accepted (metric id=%s, incident=%s)",
                (result.get("metric") or {}).get("id"),
                result.get("incident_created"),
            )
            backoff = interval
            now = time.monotonic()
            if now - last_heartbeat >= 300:
                try:
                    _post(
                        heartbeat_url,
                        args.token,
                        {
                            "hostname": socket.gethostname(),
                            "agent_version": "remote-agent-1.0",
                            "status": "online",
                            "details": {"platform": platform.platform()},
                        },
                        verify_tls,
                    )
                    last_heartbeat = now
                    logger.info("Heartbeat accepted")
                except Exception as exc:
                    logger.warning("Heartbeat failed: %s", exc)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            logger.error("Server rejected telemetry: HTTP %s %s", exc.code, detail)
            if exc.code in {401, 403}:
                logger.error("Token invalid or agent disabled; stopping until re-registered.")
                return 3
            if exc.code == 429:
                logger.warning("Rate limited; backing off")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Connection failed (%s); retrying in %ss", exc, backoff)
            time.sleep(backoff)
            backoff = min(300, max(interval, backoff * 2))
            continue
        except KeyboardInterrupt:
            logger.info("Agent stopped by operator")
            return 0
        except Exception:
            logger.exception("Unexpected agent error; continuing")
        if args.once:
            return 0
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Agent stopped by operator")
            return 0


if __name__ == "__main__":
    sys.exit(main())
