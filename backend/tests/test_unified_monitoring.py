"""Unified monitoring: host telemetry, remote agents, copilot and topology."""

from __future__ import annotations

import uuid


def _agent_name() -> str:
    return f"pytest-agent-{uuid.uuid4().hex[:8]}"


def test_host_overview_reports_empty_state(client, admin_headers):
    response = client.get("/api/host/overview?hours=24", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent"]["kind"] == "local"
    assert "summary" in body
    assert "system" in body
    assert body["disclaimer"]
    assert body["empty"] is True
    assert body["latest"] is None


def test_host_collection_is_disabled_in_test_environment(client, admin_headers):
    response = client.post("/api/host/collect", headers=admin_headers)
    assert response.status_code == 409
    assert "test environment" in response.json()["detail"]


def test_agent_registration_telemetry_and_management(client, admin_headers, accounts):
    name = _agent_name()
    viewer_headers = accounts["viewer"]["headers"]

    forbidden = client.post(
        "/api/agents",
        headers=viewer_headers,
        json={"name": name},
    )
    assert forbidden.status_code == 403

    created = client.post(
        "/api/agents",
        headers=admin_headers,
        json={"name": name, "hostname": "branch-01", "os_name": "Linux"},
    )
    assert created.status_code == 201, created.text
    registration = created.json()
    agent_id = registration["agent"]["id"]
    token = registration["token"]
    assert token.startswith("np_")
    assert registration["agent"]["status"] == "never"
    assert registration["agent"]["kind"] == "remote"

    duplicate = client.post("/api/agents", headers=admin_headers, json={"name": name})
    assert duplicate.status_code == 409

    unauthorised = client.post(
        "/api/agents/telemetry",
        json={"cpu_pct": 12.5, "memory_pct": 40.0, "disk_pct": 55.0},
    )
    assert unauthorised.status_code == 401

    telemetry = client.post(
        "/api/agents/telemetry",
        headers={"X-Agent-Token": token},
        json={
            "hostname": "branch-01",
            "os_name": "Linux",
            "agent_version": "pytest-1.0",
            "cpu_pct": 12.5,
            "memory_pct": 40.0,
            "disk_pct": 55.0,
            "process_count": 120,
            "temperature_c": 48.5,
            "details": {"interfaces": [], "connections": {"distinct_ports_by_host": {}}},
        },
    )
    assert telemetry.status_code == 200, telemetry.text
    accepted = telemetry.json()
    assert accepted["accepted"] is True
    assert accepted["metric"]["cpu_pct"] == 12.5
    assert accepted["metric"]["source"] == "agent"
    assert accepted["metric"]["data_origin"] == "live"

    heartbeat = client.post(
        "/api/agents/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={"hostname": "branch-01", "agent_version": "pytest-1.0"},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["ok"] is True

    listed = client.get("/api/agents", headers=admin_headers)
    assert listed.status_code == 200
    listed_body = listed.json()
    remote = next(item for item in listed_body["agents"] if item["id"] == agent_id)
    assert remote["status"] == "online"
    assert remote["latest"]["cpu_pct"] == 12.5
    assert remote["token_prefix"]
    assert "token" not in remote

    history = client.get(f"/api/agents/{agent_id}/metrics", headers=admin_headers)
    assert history.status_code == 200
    assert history.json()["count"] >= 1
    assert history.json()["samples"][-1]["memory_pct"] == 40.0

    rotated = client.post(f"/api/agents/{agent_id}/rotate-token", headers=admin_headers)
    assert rotated.status_code == 200
    assert rotated.json()["token"] != token

    old_token = client.post(
        "/api/agents/telemetry",
        headers={"X-Agent-Token": token},
        json={"cpu_pct": 30.0},
    )
    assert old_token.status_code == 401

    disabled = client.patch(
        f"/api/agents/{agent_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    blocked = client.post(
        "/api/agents/telemetry",
        headers={"X-Agent-Token": rotated.json()["token"]},
        json={"cpu_pct": 30.0},
    )
    assert blocked.status_code in {401, 403}

    deleted = client.delete(f"/api/agents/{agent_id}", headers=admin_headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_local_agent_cannot_be_edited(client, admin_headers):
    local = client.get("/api/agents", headers=admin_headers).json()["agents"]
    local_agent = next(item for item in local if item["kind"] == "local")
    response = client.patch(
        f"/api/agents/{local_agent['id']}",
        headers=admin_headers,
        json={"name": "Renamed local"},
    )
    assert response.status_code == 400


def test_dashboard_exposes_unified_fields(client, admin_headers):
    response = client.get("/api/dashboard/overview?hours=24", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    summary = body["summary"]
    for key in (
        "total_devices",
        "online_devices",
        "offline_devices",
        "network_health_pct",
        "active_incidents",
        "security_threats",
        "incoming_bps",
        "outgoing_bps",
        "bandwidth_utilization_pct",
        "cpu_pct",
        "memory_pct",
    ):
        assert key in summary
    assert "agents_summary" in body
    assert "health_trend" in body
    assert "availability" in body
    assert "security" in body
    assert "recent_incidents" in body
    assert "data_freshness" in body
    assert "connection" in body
    assert body["generated_at"]


def test_copilot_is_grounded_in_stored_data(client, admin_headers):
    overview = client.post(
        "/api/copilot/ask",
        headers=admin_headers,
        json={"question": "Give me the current operational overview"},
    )
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["grounded_in_live_data"] is True
    assert body["sources"]
    assert body["disclaimer"]
    assert "Devices" in body["answer"]

    security = client.post(
        "/api/copilot/ask",
        headers=admin_headers,
        json={"question": "What security findings need attention?"},
    )
    assert security.status_code == 200
    assert security.json()["data"]["summary"]["posture_score"] >= 0

    suggestions = client.get("/api/copilot/suggestions", headers=admin_headers)
    assert suggestions.status_code == 200
    assert len(suggestions.json()["suggestions"]) >= 4


def test_topology_is_labelled_as_inferred(client, admin_headers):
    response = client.get("/api/topology", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_inferred"] is True
    assert body["nodes"]
    assert any(node["kind"] == "host" for node in body["nodes"])
    assert body["notes"]


def test_security_feed_reports_threat_intel_state(client, admin_headers):
    response = client.get("/api/intelligence/security", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["threat_intel"]["enabled"] is False
    assert body["threat_intel"]["api_key_configured"] is False
    assert body["threat_intel"]["tokens_stored_client_side"] is False


def test_host_thresholds_can_be_updated(client, admin_headers):
    updated = client.patch(
        "/api/settings",
        headers=admin_headers,
        json={
            "cpu_threshold_pct": 85,
            "memory_threshold_pct": 88,
            "disk_threshold_pct": 92,
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["cpu_threshold_pct"] == 85
    assert body["memory_threshold_pct"] == 88
    assert body["disk_threshold_pct"] == 92
    assert body["host_metric_interval_seconds"] >= 15
