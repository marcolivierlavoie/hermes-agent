import importlib.util
import json
import subprocess
import sys
from pathlib import Path

WATCHDOG = Path("/Users/marco/.hermes/hermes-agent-biff-runtime/scripts/uptime-kuma-ct130-watchdog.py")


def load_watchdog_module():
    spec = importlib.util.spec_from_file_location("uptime_kuma_ct130_watchdog_under_test", WATCHDOG)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_remediation_defaults_disabled_until_enable_env(monkeypatch, tmp_path):
    module = load_watchdog_module()
    monkeypatch.delenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE", raising=False)
    monkeypatch.delenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE", raising=False)
    runner = tmp_path / "watchdog-remediate.py"
    runner.write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(module, "REMEDIATION_RUNNER", runner)

    def fail_run(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("runner should not execute unless enable env is set")

    monkeypatch.setattr(module.subprocess, "run", fail_run)
    outcome = module.maybe_remediate_uptime_kuma_down({}, now=1000)

    assert outcome["status"] == "disabled_until_enabled_env"
    assert outcome["live_default"] is False
    assert outcome["enable_env"] == "UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE"


def test_remediation_disable_env_wins_over_enable(monkeypatch):
    module = load_watchdog_module()
    monkeypatch.setenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE", "1")
    monkeypatch.setenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE", "1")

    def fail_run(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("runner should not execute when kill switch is set")

    monkeypatch.setattr(module.subprocess, "run", fail_run)
    outcome = module.maybe_remediate_uptime_kuma_down({}, now=1000)

    assert outcome["status"] == "disabled_by_env"
    assert outcome["kill_switch_env"] == "UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE"


def test_remediation_cooldown_blocks_restart_loop(monkeypatch, tmp_path):
    module = load_watchdog_module()
    monkeypatch.setenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE", "1")
    monkeypatch.delenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE", raising=False)
    runner = tmp_path / "watchdog-remediate.py"
    runner.write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(module, "REMEDIATION_RUNNER", runner)
    monkeypatch.setattr(module, "REMEDIATION_COOLDOWN_SECONDS", 1800)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps({"mode": "remediated"}), stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    state = {"uptime_kuma_ct130_remediation_last_attempt_at": 1000}
    outcome = module.maybe_remediate_uptime_kuma_down(state, now=1100)

    assert outcome["status"] == "cooldown"
    assert outcome["seconds_until_next_attempt"] == 1700
    assert calls == []


def test_remediation_invokes_gated_runner_with_live_env_and_sanitizes(monkeypatch, tmp_path):
    module = load_watchdog_module()
    monkeypatch.setenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE", "1")
    monkeypatch.delenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE", raising=False)
    monkeypatch.delenv("BIF669_WATCHDOG_REMEDIATION_ENABLE", raising=False)
    runner = tmp_path / "watchdog-remediate.py"
    runner.write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(module, "REMEDIATION_RUNNER", runner)
    monkeypatch.setattr(module, "REMEDIATION_COOLDOWN_SECONDS", 1800)

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "mode": "remediated",
                    "command_output_tail": "token=super-secret-value and normal text",
                }
            ),
            stderr="Authorization: Bearer abcdef...90",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    state = {}
    outcome = module.maybe_remediate_uptime_kuma_down(state, now=2000)

    assert outcome["status"] == "invoked"
    assert outcome["runner_rc"] == 0
    assert state["uptime_kuma_ct130_remediation_last_attempt_at"] == 2000
    assert captured["command"] == [
        sys.executable,
        str(runner),
        "--rule",
        "uptimekuma_ct_down",
        "--event",
        "down",
        "--target",
        "uptimekuma_ct_130",
        "--execute",
        "--allow-rule",
        "uptimekuma_ct_down",
        "--allow-production",
    ]
    assert captured["env"]["BIF669_WATCHDOG_REMEDIATION_ENABLE"] == "1"
    serialized = json.dumps(outcome)
    assert "super-secret-value" not in serialized
    assert "[REDACTED]" in serialized


def test_simulated_down_cli_stays_non_executing_without_enable_env(monkeypatch, tmp_path, capsys):
    module = load_watchdog_module()
    monkeypatch.delenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE", raising=False)
    monkeypatch.delenv("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE", raising=False)
    monkeypatch.setattr(module, "STATE_PATH", tmp_path / "state.json")

    rc = module.main(["--simulate-down"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "initial_down"
    assert payload["ok"] is False
    assert payload["remediation"]["status"] == "disabled_until_enabled_env"
