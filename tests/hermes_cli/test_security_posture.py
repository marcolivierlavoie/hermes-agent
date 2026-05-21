"""Focused tests for the read-only Security/Trust posture endpoint."""

from __future__ import annotations

import platform


def test_security_posture_shape_and_safety(monkeypatch):
    from hermes_cli import security_posture

    monkeypatch.setattr(security_posture, "_collect_listeners", lambda: {
        "collector": "lsof -nP -iTCP -sTCP:LISTEN",
        "available": True,
        "error": None,
        "listeners": [
            {"process": "SafeApp", "pid": "42", "port": "443", "scope": "public", "address": "all-interfaces:443"},
            {"process": "DevApp", "pid": "43", "port": "9119", "scope": "public", "address": "all-interfaces:9119"},
            {"process": "LocalApp", "pid": "44", "port": "3000", "scope": "loopback", "address": "127.0.0.x:3000"},
        ],
    })
    monkeypatch.setattr(security_posture, "_collect_versions", lambda: {"node": "v1", "npm": "1", "python": "Python 3", "openssl": "OpenSSL", "os": "TestOS", "hermes": "test"})
    monkeypatch.setattr(security_posture, "_collect_compliance", lambda: {
        "secret_paths_exposed": 0,
        "secret_values_exposed": 0,
        "env_key_count": 2,
        "known_secret_env_key_count": 1,
        "secret_like_config_key_count": 0,
        "config_category_count": 1,
        "config_categories": ["model"],
    })
    monkeypatch.setattr(security_posture, "_collect_package_updates", lambda: {"apt": {"status": "not_applicable", "reason": "macOS host; apt drift is N/A"}})
    monkeypatch.setattr(security_posture, "_collect_service_status", lambda: {"manager": "launchd", "available": True, "summary": "launchd available"})
    monkeypatch.delenv("HERMES_SECURITY_ALLOWED_PUBLIC_PORTS", raising=False)

    posture = security_posture.build_security_posture()

    for key in ["score", "label", "summary", "findings", "compliance", "versions", "packageUpdates", "exposure", "network", "note"]:
        assert key in posture
    assert posture["read_only"] is True
    assert posture["network"]["masked_ips"] is True
    assert posture["network"]["raw_paths_or_values"] is False
    assert posture["compliance"]["secret_paths_exposed"] == 0
    assert posture["compliance"]["secret_values_exposed"] == 0
    assert posture["exposure"]["unapproved_public_listeners"] == 1
    finding = posture["findings"][0]
    for key in ["id", "severity", "title", "detail", "why", "items", "recommendations", "nextProbe"]:
        assert key in finding
    assert "9119" in finding["detail"] or any("9119" in item for item in finding["items"])


def test_macos_package_updates_are_na(monkeypatch):
    from hermes_cli import security_posture

    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    result = security_posture._collect_package_updates()

    assert result["apt"]["status"] == "not_applicable"
    assert "macOS" in result["apt"]["reason"]


def test_security_endpoint_returns_public_read_only_shape(monkeypatch, _isolate_hermes_home):
    from starlette.testclient import TestClient
    import hermes_cli.web_server as web_server

    monkeypatch.setattr(web_server, "get_running_pid", lambda: None)
    monkeypatch.setattr(web_server, "read_runtime_status", lambda: None)
    monkeypatch.setattr("hermes_cli.security_posture.build_security_posture", lambda: {
        "score": 100,
        "label": "Good",
        "summary": "No unapproved public listeners found by safe local collectors.",
        "findings": [],
        "compliance": {"secret_paths_exposed": 0, "secret_values_exposed": 0},
        "versions": {"os": "TestOS"},
        "packageUpdates": {"apt": {"status": "not_applicable"}},
        "exposure": {"listeners_total": 0},
        "network": {"masked_ips": True, "raw_paths_or_values": False},
        "note": "Read-only local posture summary.",
        "read_only": True,
    })

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    response = client.get("/api/security")

    assert response.status_code == 200
    data = response.json()
    assert data["score"] == 100
    assert data["read_only"] is True
    assert data["network"]["masked_ips"] is True
