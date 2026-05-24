import json
import sqlite3

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
