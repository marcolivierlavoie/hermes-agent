"""Durable handoff packet helpers for gateway self-restarts.

The pre-restart gateway process writes a small JSON packet before it begins
shutdown/restart.  A later startup-side flow can use the packet to confirm the
restart back to the initiating conversation and mark the packet completed or
failed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home

EXPECTED_GATEWAY_SERVICE_LABEL = "ai.hermes.gateway"
RESTART_HANDOFF_FILENAME = ".gateway_restart_handoff.json"


def restart_handoff_path(home: Path | str | None = None) -> Path:
    """Return the durable gateway restart handoff packet path."""
    base = Path(home) if home is not None else get_hermes_home()
    return base / RESTART_HANDOFF_FILENAME


def _fsync_directory(path: Path) -> None:
    """Best-effort fsync for the containing directory after atomic replace."""
    if os.name == "nt":
        return
    dir_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def durable_json_write(path: Path | str, payload: Mapping[str, Any]) -> None:
    """Atomically write JSON and fsync both the temp file and directory.

    ``utils.atomic_json_write`` already fsyncs the file before ``os.replace``;
    this helper adds a directory fsync so the name swap itself is durable across
    abrupt gateway termination or host crash on POSIX filesystems.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.stem}_",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(target))
        _fsync_directory(target.parent)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _source_to_target(source: Any) -> dict[str, Any]:
    platform = getattr(source, "platform", None)
    platform_value = getattr(platform, "value", platform)
    target = {
        "platform": platform_value,
        "chat_id": getattr(source, "chat_id", None),
        "thread_id": getattr(source, "thread_id", None),
        "chat_type": getattr(source, "chat_type", None),
        "guild_id": getattr(source, "guild_id", None),
        "parent_chat_id": getattr(source, "parent_chat_id", None),
        "message_id": getattr(source, "message_id", None),
    }
    return {key: value for key, value in target.items() if value is not None}


def _source_to_requester(source: Any) -> dict[str, Any] | None:
    requester = {
        "user_id": getattr(source, "user_id", None),
        "user_id_alt": getattr(source, "user_id_alt", None),
        "user_name": getattr(source, "user_name", None),
        "is_bot": getattr(source, "is_bot", None),
    }
    cleaned = {key: value for key, value in requester.items() if value is not None}
    return cleaned or None


def _launchd_snapshot(service_label: str) -> dict[str, Any] | None:
    """Best-effort launchd state/PID/run-count snapshot without sudo."""
    launchctl = shutil.which("launchctl") or "/bin/launchctl"
    if not Path(launchctl).exists():
        return None
    target = f"system/{service_label}"
    try:
        proc = subprocess.run(
            [launchctl, "print", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return {"target": target, "available": False}

    snapshot: dict[str, Any] = {"target": target, "available": True}
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if " = " not in stripped:
            continue
        key, value = stripped.split(" = ", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "state":
            snapshot["state"] = value
        elif key == "pid":
            try:
                snapshot["pid"] = int(value)
            except ValueError:
                snapshot["pid"] = value
        elif key in {"runs", "run count"}:
            try:
                snapshot["run_count"] = int(value)
            except ValueError:
                snapshot["run_count"] = value
    return snapshot


def build_pre_restart_handoff(
    event: Any,
    *,
    expected_service_label: str = EXPECTED_GATEWAY_SERVICE_LABEL,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the pending pre-restart handoff packet for an inbound command."""
    source = getattr(event, "source", None)
    message_id = getattr(event, "message_id", None)
    target = _source_to_target(source)
    if message_id and "message_id" not in target:
        target["message_id"] = message_id

    packet: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id or f"gateway-restart-{uuid.uuid4().hex}",
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "expected_service_label": expected_service_label,
        "conversation_target": target,
        "requester": _source_to_requester(source),
        "pre_restart": {
            "pid": os.getpid(),
            "launchd": _launchd_snapshot(expected_service_label),
        },
    }
    if getattr(event, "platform_update_id", None) is not None:
        packet["platform_update_id"] = event.platform_update_id
    return packet


def persist_pre_restart_handoff(
    event: Any,
    *,
    home: Path | str | None = None,
    expected_service_label: str = EXPECTED_GATEWAY_SERVICE_LABEL,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build and durably persist a pending gateway restart handoff packet."""
    packet = build_pre_restart_handoff(
        event,
        expected_service_label=expected_service_label,
        request_id=request_id,
    )
    durable_json_write(restart_handoff_path(home), packet)
    return packet
