import json
import time
import sqlite3

from scripts import verify_secondbrain_smart_connections as smart_verify
from scripts.verify_secondbrain_smart_connections import verify


def _sqlite(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, path TEXT)")
        conn.execute("INSERT INTO embeddings(path) VALUES ('note.md')")
        conn.commit()
    finally:
        conn.close()


def test_secondbrain_verifier_passes_with_sqlite_and_schedule(tmp_path):
    vault = tmp_path / "SecondBrain"
    db_dir = vault / ".smart-connections"
    db_dir.mkdir(parents=True)
    db = db_dir / "index.sqlite"
    _sqlite(db)
    schedule = db_dir / "last_refresh.json"
    schedule.write_text(json.dumps({"ok": True}), encoding="utf-8")

    result = verify(str(vault))

    assert result["status"] == "PASS"
    assert any(c["component"] == "sqlite" and c["status"] == "PASS" for c in result["checked"])
    assert str(db) in result["evidence_paths"]


def test_secondbrain_verifier_warns_when_schedule_missing(tmp_path):
    vault = tmp_path / "SecondBrain"
    db_dir = vault / ".smart-connections"
    db_dir.mkdir(parents=True)
    _sqlite(db_dir / "index.sqlite")

    result = verify(str(vault))

    assert result["status"] == "WARN"
    assert any(c["component"] == "refresh_schedule" and c["status"] == "WARN" for c in result["checked"])
    assert "schedule" in " ".join(result["follow_up"]).lower()


def test_secondbrain_verifier_fails_when_sqlite_missing(tmp_path):
    vault = tmp_path / "SecondBrain"
    vault.mkdir()

    result = verify(str(vault))

    assert result["status"] == "FAIL"
    assert "no SQLite" in " ".join(result["follow_up"])


def test_secondbrain_verifier_times_out_slow_sqlite_discovery(tmp_path, monkeypatch):
    vault = tmp_path / "SecondBrain"
    vault.mkdir()

    def slow_find_sqlite_files(_vault, _explicit):
        time.sleep(1)
        return []

    monkeypatch.setattr(smart_verify, "_find_sqlite_files", slow_find_sqlite_files)

    started = time.monotonic()
    result = verify(str(vault), timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert result["status"] == "FAIL"
    assert result["state"] == "timed_out"
    assert result["checked"] == [
        {"component": "smart_connections", "status": "unavailable", "reason": "timed_out"}
    ]
    assert result["evidence_paths"] == []
    assert result["follow_up"] == [
        "Smart Connections status check timed out before completing; retry with a healthy local/iCloud/FileProvider state."
    ]
