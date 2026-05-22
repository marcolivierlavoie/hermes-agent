from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bif669-core-system-status.py"
spec = importlib.util.spec_from_file_location("bif669_core_system_status", SCRIPT)
assert spec is not None
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_system_inventory_covers_remaining_core_systems():
    assert set(mod.SYSTEMS) >= {
        "homeassistant_zigbee_slzb",
        "adguard_dns",
        "immich_postgres",
        "node_red",
        "nas_dsm",
        "unifi",
    }


def test_all_systems_are_status_only_and_hardcoded():
    forbidden = ("reboot", "restart", "shutdown", "delete", "rm ", "poweroff", "wakeonlan")
    for system_id, spec in mod.SYSTEMS.items():
        serialized = repr(spec).lower()
        assert system_id
        assert "description" in spec
        assert not any(word in serialized for word in forbidden)


def test_credential_check_sanitizes_token_alias_output(monkeypatch):
    class Proc:
        returncode = 0
        stdout = "ha_token: available source=1password"

    monkeypatch.setattr(mod, "CREDENTIAL_HELPER", Path("/tmp/fake-helper"))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(mod.subprocess, "run", lambda *args, **kwargs: Proc())

    result = mod.credential_check("ha_token")

    assert result == {"ok": True, "source": "1password", "detail": "alias available source=1password"}
    assert "ha_token" not in result["detail"]
    assert "token" not in result["detail"].lower()


def test_resolve_credential_helper_falls_back_to_marco_home(monkeypatch):
    calls = []

    def fake_exists(self):
        calls.append(str(self))
        return str(self) == "/Users/marco/.local/bin/get_credential.sh"

    monkeypatch.setattr(mod.Path, "home", lambda: Path("/Users/marco/.hermes/profiles/forge/home"))
    monkeypatch.setattr(Path, "exists", fake_exists)

    assert mod.resolve_credential_helper() == Path("/Users/marco/.local/bin/get_credential.sh")
    assert "/Users/marco/.hermes/profiles/forge/home/.local/bin/get_credential.sh" in calls


def test_collect_system_skip_live_does_not_call_network(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "credential_check", lambda alias: {"ok": True, "source": "1password", "detail": "alias available source=1password"})
    monkeypatch.setattr(mod, "http_status", lambda check: calls.append(("http", check)) or {"ok": False})
    monkeypatch.setattr(mod, "tcp_status", lambda check: calls.append(("tcp", check)) or {"ok": False})
    monkeypatch.setattr(mod, "proxmox_status", lambda command: calls.append(("proxmox", command)) or {"ok": False})

    result = mod.collect_system("nas_dsm", skip_live=True)

    assert result["mutates"] is False
    assert result["live_checks_skipped"] is True
    assert calls == []


def test_summarize_counts_nested_ok_values_but_not_status_root():
    report = {"ok": False, "mutates": False, "child": {"ok": True}, "other": [{"ok": False}]}

    assert mod.summarize(report) == {"checks": 2, "ok": 1, "failed": 1}
