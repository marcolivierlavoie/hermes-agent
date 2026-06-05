import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "watchdog-alert-intake.py"


def run_intake(*args, input_text=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


def test_known_down_alert_creates_single_remediation_run_record(tmp_path):
    state_path = tmp_path / "watchdog-alert-intake.json"
    alert = {
        "source": "n8n_failure_watchdog.py",
        "message": "n8n unreachable: API did not respond at https://biff.tail460c2.ts.net:5678/api/v1 (TimeoutError)",
        "timestamp": "2026-05-23T12:00:00+00:00",
    }

    proc = run_intake("--state", str(state_path), "--alert-json", json.dumps(alert), "--now", "2026-05-23T12:00:00+00:00")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["action"] == "remediation_record_created"
    assert payload["spam_suppressed"] is False
    assert payload["incident"]["alert_source"] == "n8n_failure_watchdog.py"
    assert payload["incident"]["service"] == "n8n_production"
    assert payload["incident"]["timestamp"] == "2026-05-23T12:00:00+00:00"
    assert payload["incident"]["detected_failure"] == "n8n API unreachable"
    assert payload["incident"]["current_next_step"] == "run gated remediation rule n8n_production_down"
    assert payload["incident"]["attempt_count"] == 1
    assert payload["incident"]["linear_dedupe_key"] == "watchdog:n8n_production:down"
    assert payload["incident"]["discord_dedupe_key"] == "watchdog:n8n_production:down"

    state = json.loads(state_path.read_text())
    assert list(state["active_incidents"].keys()) == ["watchdog:n8n_production:down"]


def test_repeated_alert_during_cooldown_updates_state_without_stdout_spam(tmp_path):
    state_path = tmp_path / "watchdog-alert-intake.json"
    alert = {
        "source": "uptime_kuma_ct130_watchdog.py",
        "message": "Uptime Kuma CT130 watchdog: DOWN - https://biff.tail460c2.ts.net:3001/ timed out",
        "timestamp": "2026-05-23T12:00:00+00:00",
    }
    first = run_intake("--state", str(state_path), "--alert-json", json.dumps(alert), "--now", "2026-05-23T12:00:00+00:00")
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["incident"]["service"] == "uptimekuma_ct_130"

    repeat_alert = {**alert, "timestamp": "2026-05-23T12:05:00+00:00"}
    repeat = run_intake("--state", str(state_path), "--alert-json", json.dumps(repeat_alert), "--now", "2026-05-23T12:05:00+00:00")

    assert repeat.returncode == 0, repeat.stderr
    assert repeat.stdout == ""
    state = json.loads(state_path.read_text())
    incident = state["active_incidents"]["watchdog:uptimekuma_ct_130:down"]
    assert incident["attempt_count"] == 1
    assert incident["alert_count"] == 2
    assert incident["last_seen"] == "2026-05-23T12:05:00+00:00"
    assert incident["last_suppressed_reason"] == "cooldown"


def test_alert_after_cooldown_updates_existing_record_without_duplicate_keys(tmp_path):
    state_path = tmp_path / "watchdog-alert-intake.json"
    alert = {
        "source": "uptime_kuma_ct130_watchdog.py",
        "message": "Uptime Kuma CT130 watchdog: DOWN",
        "timestamp": "2026-05-23T12:00:00+00:00",
    }
    first = run_intake(
        "--state", str(state_path), "--alert-json", json.dumps(alert), "--now", "2026-05-23T12:00:00+00:00", "--cooldown-seconds", "300"
    )
    assert first.returncode == 0, first.stderr

    later_alert = {**alert, "timestamp": "2026-05-23T12:06:00+00:00"}
    later = run_intake(
        "--state", str(state_path), "--alert-json", json.dumps(later_alert), "--now", "2026-05-23T12:06:00+00:00", "--cooldown-seconds", "300"
    )

    assert later.returncode == 0, later.stderr
    payload = json.loads(later.stdout)
    assert payload["action"] == "remediation_record_updated"
    assert payload["incident"]["incident_key"] == "watchdog:uptimekuma_ct_130:down"
    assert payload["incident"]["attempt_count"] == 2
    assert payload["incident"]["linear_dedupe_key"] == "watchdog:uptimekuma_ct_130:down"
    assert payload["incident"]["discord_dedupe_key"] == "watchdog:uptimekuma_ct_130:down"


def test_unknown_or_non_down_alert_is_ignored_without_state_or_spam(tmp_path):
    state_path = tmp_path / "watchdog-alert-intake.json"
    alert = {"source": "unknown", "message": "random informational log", "timestamp": "2026-05-23T12:00:00+00:00"}

    proc = run_intake("--state", str(state_path), "--alert-json", json.dumps(alert), "--now", "2026-05-23T12:00:00+00:00")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert not state_path.exists()


def test_recovered_alert_resolves_active_incident_silently_by_default(tmp_path):
    state_path = tmp_path / "watchdog-alert-intake.json"
    down = {
        "source": "n8n_failure_watchdog.py",
        "message": "n8n unreachable: API did not respond",
        "timestamp": "2026-05-23T12:00:00+00:00",
    }
    assert run_intake("--state", str(state_path), "--alert-json", json.dumps(down), "--now", "2026-05-23T12:00:00+00:00").returncode == 0

    recovered = {**down, "message": "n8n recovered: API reachable again", "timestamp": "2026-05-23T12:10:00+00:00"}
    proc = run_intake("--state", str(state_path), "--alert-json", json.dumps(recovered), "--now", "2026-05-23T12:10:00+00:00")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    state = json.loads(state_path.read_text())
    assert state["active_incidents"] == {}
    assert state["resolved_incidents"][0]["service"] == "n8n_production"
    assert state["resolved_incidents"][0]["status"] == "resolved"
