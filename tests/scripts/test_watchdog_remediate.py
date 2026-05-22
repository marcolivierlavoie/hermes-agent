import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "watchdog-remediate.py"
RULES = REPO_ROOT / "config" / "watchdog-remediation-rules.json"


def run_remediate(*args, env=None):
    merged_env = os.environ.copy()
    merged_env.pop("BIF669_WATCHDOG_REMEDIATION_ENABLE", None)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=merged_env,
        check=False,
    )


def test_default_is_dry_run_even_for_matching_rule():
    proc = run_remediate(
        "--rule",
        "hermes_dashboard_down",
        "--event",
        "down",
        "--target",
        "hermes_dashboard",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "dry_run"
    assert payload["gates"]["execute_flag"] is False
    assert payload["gates"]["global_enable_env"] is False
    assert payload["gates"]["allow_rule_flag"] is False
    assert payload["planned_command"] == ["scripts/restart-hermes-dashboard.sh"]


def test_execute_flag_alone_still_dry_run():
    proc = run_remediate(
        "--rule",
        "hermes_dashboard_down",
        "--event",
        "down",
        "--target",
        "hermes_dashboard",
        "--execute",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "dry_run"
    assert payload["gates"]["execute_flag"] is True
    assert payload["gates"]["global_enable_env"] is False
    assert payload["gates"]["allow_rule_flag"] is False


def test_production_rule_requires_allow_production_gate():
    proc = run_remediate(
        "--rule",
        "n8n_production_down",
        "--event",
        "down",
        "--target",
        "n8n_production",
        "--execute",
        "--allow-rule",
        "n8n_production_down",
        env={"BIF669_WATCHDOG_REMEDIATION_ENABLE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "dry_run"
    assert payload["production"] is True
    assert payload["gates"]["production_allowed"] is False


def test_blocked_action_class_in_rules_file_is_rejected(tmp_path):
    bad_rules = json.loads(RULES.read_text())
    bad_rules["rules"][0]["action_class"] = "hard_power_cycle"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(bad_rules))

    proc = run_remediate(
        "--rules",
        str(path),
        "--rule",
        "hermes_dashboard_down",
        "--event",
        "down",
        "--target",
        "hermes_dashboard",
        "--execute",
        "--allow-rule",
        "hermes_dashboard_down",
        env={"BIF669_WATCHDOG_REMEDIATION_ENABLE": "1"},
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stderr)
    assert payload["mode"] == "blocked"
    assert "blocked action class" in payload["error"]


def test_unsupported_action_class_in_rules_file_is_rejected(tmp_path):
    bad_rules = json.loads(RULES.read_text())
    bad_rules["rules"][0]["action_class"] = "unknown_future_action"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(bad_rules))

    proc = run_remediate(
        "--rules",
        str(path),
        "--rule",
        "hermes_dashboard_down",
        "--event",
        "down",
        "--target",
        "hermes_dashboard",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stderr)
    assert payload["mode"] == "blocked"
    assert "unsupported action class" in payload["error"]


def test_mismatched_event_target_is_blocked():
    proc = run_remediate(
        "--rule",
        "hermes_dashboard_down",
        "--event",
        "down",
        "--target",
        "n8n_production",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stderr)
    assert payload["mode"] == "blocked"
    assert "conditions do not match" in payload["error"]
