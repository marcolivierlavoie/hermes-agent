from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "biff-sysadmin-status.py"
spec = importlib.util.spec_from_file_location("biff_sysadmin_status", SCRIPT)
assert spec is not None
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_summarize_counts_nested_ok_values():
    report = {
        "a": {"ok": True},
        "b": {"ok": False, "children": [{"ok": True}]},
        "c": [1, {"ok": True}],
    }

    assert mod.summarize(report) == {"checks": 4, "ok": 3, "failed": 1}


def test_credential_status_never_requires_secret_value(monkeypatch):
    monkeypatch.setattr(mod, "CREDENTIAL_HELPER", Path("/tmp/fake-helper"))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(mod, "run_cmd", lambda cmd, timeout=15: (True, "homeassistant: available source=1password"))

    result = mod.credential_status("homeassistant")

    assert result["ok"] is True
    assert result["source"] == "1password"
    assert "available" in result["detail"]
    assert "token" not in result["detail"].lower()


def test_resolve_credential_helper_falls_back_to_marco_home(monkeypatch):
    def fake_exists(self):
        return str(self) == "/Users/marco/.local/bin/get_credential.sh"

    monkeypatch.setattr(mod.Path, "home", lambda: Path("/Users/marco/.hermes/profiles/forge/home"))
    monkeypatch.setattr(Path, "exists", fake_exists)

    assert mod.resolve_credential_helper() == Path("/Users/marco/.local/bin/get_credential.sh")


def test_remediation_dry_run_requires_dry_run_mode(monkeypatch):
    monkeypatch.setattr(mod, "run_cmd", lambda cmd, timeout=20: (True, '{"mode":"dry_run","rule":"adguard_dns_down"}'))

    result = mod.remediation_dry_run("adguard_dns_down", "down", "adguard_dns_ct_101")

    assert result["ok"] is True
    assert result["result"]["mode"] == "dry_run"
