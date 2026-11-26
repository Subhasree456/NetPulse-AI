"""Optional, cached threat-intelligence enrichment.

The feature is deliberately off by default. When an administrator configures an
AbuseIPDB API key through the server environment, public remote addresses found in
the local socket table can be scored by that documented service. Results are
cached server-side, lookups are capped per scan, and the API key is never sent to
or read by the browser.

If the feature is disabled or the provider fails, the caller gets an explicit
status instead of a made-up score.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.intelligence.common import aware, iso
from app.models import ThreatIntelCache

logger = logging.getLogger("netpulse.threat_intel")

PROVIDER = "abuseipdb"
ENDPOINT = "https://api.abuseipdb.com/api/v2/check"


def _serialise(row: ThreatIntelCache, status: str) -> dict:
    return {
        "indicator": row.indicator,
        "provider": row.provider,
        "status": status,
        "score": row.score,
        "total_reports": row.total_reports,
        "country": row.country,
        "isp": row.isp,
        "usage_type": row.usage_type,
        "error": row.error_message,
        "fetched_at": iso(row.fetched_at),
        "expires_at": iso(row.expires_at),
        "documented_api": ENDPOINT,
    }


def _disabled(ip: str, why: str = "Threat-intelligence lookup is disabled by configuration.") -> dict:
    return {
        "indicator": ip,
        "provider": settings.threat_intel_provider,
        "status": "disabled",
        "score": None,
        "total_reports": None,
        "country": None,
        "isp": None,
        "usage_type": None,
        "error": why,
        "fetched_at": None,
        "expires_at": None,
        "documented_api": ENDPOINT,
    }


def _is_public(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(address.is_global)


def _fetch(ip: str) -> dict:
    query = urllib.parse.urlencode({"ipAddress": ip, "maxAgeInDays": 90})
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}",
        headers={
            "Key": settings.abuseipdb_api_key,
            "Accept": "application/json",
            "User-Agent": "NetPulse-AI/1.0 (defensive monitoring)",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - fixed provider URL
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") or {}
    return {
        "score": float(data.get("abuseConfidenceScore") or 0),
        "total_reports": int(data.get("totalReports") or 0),
        "country": data.get("countryCode"),
        "isp": (data.get("isp") or "")[:255] or None,
        "usage_type": (data.get("usageType") or "")[:120] or None,
        "raw": data,
    }


def check_ip(db: Session, ip: str, *, refresh: bool = False) -> dict:
    """Return a cached or fresh threat-intelligence result for a public IP."""
    if not settings.threat_intel_enabled:
        return _disabled(ip)
    if not settings.abuseipdb_api_key:
        return _disabled(ip, "THREAT_INTEL_ENABLED is true but ABUSEIPDB_API_KEY is not configured.")
    if not _is_public(ip):
        return {
            **_disabled(ip, "Private, loopback and reserved addresses are not looked up."),
            "status": "skipped_private",
        }

    now = datetime.now(timezone.utc)
    cached = db.scalar(
        select(ThreatIntelCache).where(
            ThreatIntelCache.indicator == ip,
            ThreatIntelCache.provider == settings.threat_intel_provider,
        )
    )
    if cached is not None and not refresh:
        expires = aware(cached.expires_at)
        if expires and expires > now:
            return _serialise(cached, "cached")

    try:
        data = _fetch(ip)
    except urllib.error.HTTPError as exc:
        logger.warning("Threat intelligence HTTP %s for %s", exc.code, ip)
        return {
            **_disabled(ip, f"Provider returned HTTP {exc.code}."),
            "status": "rate_limited" if exc.code == 429 else "error",
        }
    except Exception as exc:  # network or parsing failure
        logger.warning("Threat intelligence lookup failed for %s: %s", ip, type(exc).__name__)
        return {**_disabled(ip, f"Lookup failed: {type(exc).__name__}."), "status": "error"}

    expires_at = now + timedelta(minutes=settings.threat_intel_cache_minutes)
    if cached is None:
        cached = ThreatIntelCache(
            indicator=ip,
            provider=settings.threat_intel_provider,
            fetched_at=now,
            expires_at=expires_at,
        )
        db.add(cached)
    cached.score = data["score"]
    cached.total_reports = data["total_reports"]
    cached.country = data["country"]
    cached.isp = data["isp"]
    cached.usage_type = data["usage_type"]
    cached.raw = data["raw"]
    cached.error_message = None
    cached.status = "ok"
    cached.fetched_at = now
    cached.expires_at = expires_at
    db.flush()
    return _serialise(cached, "ok" if cached.status == "ok" else cached.status)


def enrich_many(db: Session, ips: list[str]) -> list[dict]:
    """Look up at most the configured number of indicators per scan."""
    results: list[dict] = []
    seen: set[str] = set()
    for ip in ips:
        if ip in seen:
            continue
        seen.add(ip)
        if len(results) >= settings.threat_intel_max_lookups_per_scan:
            break
        results.append(check_ip(db, ip))
    return results


def threat_intel_status(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    cached_rows = db.scalars(select(ThreatIntelCache).order_by(ThreatIntelCache.fetched_at.desc()).limit(50)).all()
    return {
        "enabled": bool(settings.threat_intel_enabled),
        "provider": settings.threat_intel_provider,
        "api_key_configured": bool(settings.abuseipdb_api_key),
        "cache_minutes": settings.threat_intel_cache_minutes,
        "max_lookups_per_scan": settings.threat_intel_max_lookups_per_scan,
        "cached": len(cached_rows),
        "tokens_stored_client_side": False,
        "documented_api": ENDPOINT,
        "usage": (
            "Set THREAT_INTEL_ENABLED=true and ABUSEIPDB_API_KEY in the backend environment. "
            "The key is used server-side only. Lookups are cached and capped."
        ),
        "entries": [
            {
                "indicator": row.indicator,
                "score": row.score,
                "total_reports": row.total_reports,
                "country": row.country,
                "isp": row.isp,
                "fetched_at": iso(row.fetched_at),
                "expires_at": iso(row.expires_at),
                "expired": bool(aware(row.expires_at) and aware(row.expires_at) <= now),
            }
            for row in cached_rows
        ],
        "generated_at": now.isoformat(),
        "note": (
            "No threat-intelligence value is invented. When disabled, the security scan "
            "continues with local evidence only."
        ),
    }
