"""Regression tests for scripts/restart-hermes-gateway.sh verification gates."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "restart-hermes-gateway.sh"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run_restart_helper(tmp_path: Path, *, sudo_body: str, curl_rc: int = 0) -> subprocess.CompletedProcess[str]:
    log = tmp_path / "gateway.log"
    log.write_text(
        "2026-05-25 21:00:00 INFO gateway.run: previous startup\n"
        "2026-05-25 21:01:00 WARNING gateway.run: Shutdown context: old line\n"
    )

    state = tmp_path / "launchctl_state"
    state.write_text("before")
    launchctl = tmp_path / "launchctl"
    sudo = tmp_path / "sudo"
    curl = tmp_path / "curl"

    _write_executable(
        launchctl,
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "print" ]]; then
  if [[ "$(cat {state})" == "before" ]]; then
    printf 'service = {{\\n    state = running\\n    pid = 111\\n    runs = 5\\n}}\\n'
  else
    printf 'service = {{\\n    state = running\\n    pid = 222\\n    runs = 6\\n}}\\n'
  fi
  exit 0
fi
exit 2
""",
    )
    _write_executable(
        sudo,
        f"""#!/usr/bin/env bash
set -euo pipefail
{sudo_body}
printf after > {state}
""",
    )
    _write_executable(
        curl,
        f"""#!/usr/bin/env bash
exit {curl_rc}
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "HERMES_GATEWAY_LAUNCHCTL": str(launchctl),
            "HERMES_GATEWAY_SUDO": str(sudo),
            "HERMES_GATEWAY_CURL": str(curl),
            "HERMES_GATEWAY_LOG": str(log),
            "HERMES_GATEWAY_LAUNCHD_WAIT_SECONDS": "1",
            "HERMES_GATEWAY_LOG_WAIT_SECONDS": "1",
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


def test_restart_helper_rejects_shutdown_only_log_freshness(tmp_path: Path) -> None:
    result = _run_restart_helper(
        tmp_path,
        sudo_body="printf '2026-05-25 21:02:00 WARNING gateway.run: Shutdown context: signal=SIGTERM\\n' >> $HERMES_GATEWAY_LOG",
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "post-restart startup markers are incomplete" in combined
    assert "Hermes gateway restart verified successfully" not in combined


def test_restart_helper_requires_startup_markers_and_api_health(tmp_path: Path) -> None:
    result = _run_restart_helper(
        tmp_path,
        sudo_body=(
            "cat >> $HERMES_GATEWAY_LOG <<'EOF'\n"
            "2026-05-25 21:02:00 INFO gateway.run: biff_runtime_diagnostic: {\"pid\": 222}\n"
            "2026-05-25 21:02:01 INFO gateway.platforms.api_server: [Api_Server] API server listening on http://127.0.0.1:8642 (model: hermes-agent)\n"
            "2026-05-25 21:02:02 INFO gateway.platforms.discord: [Discord] Connected as Hermes#9283\n"
            "2026-05-25 21:02:03 INFO gateway.run: ✓ discord connected\n"
            "EOF"
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Gateway runtime verification OK: pid=222" in result.stdout
    assert "Hermes gateway restart verified successfully" in result.stdout


def test_restart_helper_rejects_same_launchd_pid(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    log.write_text("before\n")
    launchctl = tmp_path / "launchctl"
    sudo = tmp_path / "sudo"
    curl = tmp_path / "curl"
    _write_executable(
        launchctl,
        """#!/usr/bin/env bash
printf 'service = {\\n    state = running\\n    pid = 111\\n    runs = 5\\n}\\n'
""",
    )
    _write_executable(sudo, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(curl, "#!/usr/bin/env bash\nexit 0\n")
    env = os.environ.copy()
    env.update(
        {
            "HERMES_GATEWAY_LAUNCHCTL": str(launchctl),
            "HERMES_GATEWAY_SUDO": str(sudo),
            "HERMES_GATEWAY_CURL": str(curl),
            "HERMES_GATEWAY_LOG": str(log),
            "HERMES_GATEWAY_LAUNCHD_WAIT_SECONDS": "1",
            "HERMES_GATEWAY_LOG_WAIT_SECONDS": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )

    assert result.returncode != 0
    assert "fresh PID" in (result.stdout + result.stderr)
