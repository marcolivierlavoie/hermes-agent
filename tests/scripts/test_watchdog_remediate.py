import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_uptimekuma_ct130_rule_is_gated_production_dry_run():
    proc = run_remediate(
        "--rule",
        "uptimekuma_ct_down",
        "--event",
        "down",
        "--target",
        "uptimekuma_ct_130",
        "--execute",
        "--allow-rule",
        "uptimekuma_ct_down",
        env={"BIF669_WATCHDOG_REMEDIATION_ENABLE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "dry_run"
    assert payload["action_class"] == "proxmox_uptimekuma_ct130_restart"
    assert payload["production"] is True
    assert payload["gates"] == {
        "allow_rule_flag": True,
        "execute_flag": True,
        "global_enable_env": True,
        "production_allowed": False,
    }
    assert payload["planned_command"] == ["scripts/restart-uptimekuma-ct130.sh"]


def test_uptimekuma_ct130_helper_is_hardcoded_to_single_ct_and_host():
    helper = REPO_ROOT / "scripts" / "restart-uptimekuma-ct130.sh"
    text = helper.read_text()

    assert 'readonly PROXMOX_HOST="root@192.168.1.248"' in text
    assert 'readonly CT_ID="130"' in text
    assert 'pct reboot "$CT_ID"' in text
    assert 'pct status "$CT_ID"' in text
    assert "https://biff.tail460c2.ts.net:3001/" in text
    assert "pct reboot $1" not in text
    assert 'pct reboot "$1"' not in text
    assert "shutdown" not in text
    assert "reboot now" not in text


def test_adguard_dns_ct101_rule_is_gated_production_dry_run():
    proc = run_remediate(
        "--rule",
        "adguard_dns_down",
        "--event",
        "down",
        "--target",
        "adguard_dns_ct_101",
        "--execute",
        "--allow-rule",
        "adguard_dns_down",
        env={"BIF669_WATCHDOG_REMEDIATION_ENABLE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "dry_run"
    assert payload["action_class"] == "proxmox_adguard_dns_ct101_restart"
    assert payload["production"] is True
    assert payload["gates"] == {
        "allow_rule_flag": True,
        "execute_flag": True,
        "global_enable_env": True,
        "production_allowed": False,
    }
    assert payload["planned_command"] == ["scripts/restart-adguard-dns-ct101.sh"]


def test_adguard_dns_ct101_helper_is_hardcoded_to_single_ct_and_host_with_smokes():
    helper = REPO_ROOT / "scripts" / "restart-adguard-dns-ct101.sh"
    text = helper.read_text()

    assert 'readonly PROXMOX_HOST="root@192.168.1.248"' in text
    assert 'readonly CT_ID="101"' in text
    assert 'readonly ADGUARD_HTTP_URL="https://biff.tail460c2.ts.net:3000/"' in text
    assert 'readonly ADGUARD_DNS_SERVER="192.168.1.162"' in text
    assert 'pct reboot "$CT_ID"' in text
    assert 'pct status "$CT_ID"' in text
    assert "check_http_ready_once" in text
    assert "check_dns_ready_once" in text
    assert "wait_for_http_ready" in text
    assert "wait_for_dns_ready" in text
    assert "pct reboot $1" not in text
    assert 'pct reboot "$1"' not in text
    assert "pct reboot 130" not in text
    assert "shutdown" not in text
    assert "reboot now" not in text
    assert "uci " not in text
    assert "nmcli " not in text


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


@pytest.mark.parametrize("blocked_class", ["guest_restart", "guest_reboot", "host_reboot"])
def test_broad_guest_or_host_action_classes_remain_blocked(tmp_path, blocked_class):
    bad_rules = json.loads(RULES.read_text())
    uptime_rule = next(rule for rule in bad_rules["rules"] if rule["id"] == "uptimekuma_ct_down")
    uptime_rule["action_class"] = blocked_class
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(bad_rules))

    proc = run_remediate(
        "--rules",
        str(path),
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
        env={"BIF669_WATCHDOG_REMEDIATION_ENABLE": "1"},
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stderr)
    assert payload["mode"] == "blocked"
    assert f"blocked action class: {blocked_class}" in payload["error"]


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
