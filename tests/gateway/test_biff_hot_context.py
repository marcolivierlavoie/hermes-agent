from pathlib import Path

from gateway import biff_hot_context as hot


def test_biff_hot_context_is_discord_only(monkeypatch, tmp_path):
    monkeypatch.setattr(hot, "get_hermes_home", lambda: Path(tmp_path))

    assert hot.build_biff_hot_context({}, platform_key="slack") == ""


def test_biff_hot_context_includes_cached_role_and_kanban_config(monkeypatch, tmp_path):
    hot.clear_biff_hot_context_cache()
    home = Path(tmp_path)
    (home / "SOUL.md").write_text(
        "Biff routes Kanban work through Ranger.\nForge handles implementation.\nUnrelated line.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hot, "get_hermes_home", lambda: home)
    monkeypatch.setattr(hot, "_kanban_lines", lambda limit=5: ["- K-001: Test card [ready, ranger]"])

    text = hot.build_biff_hot_context(
        {"kanban": {"dispatch_in_gateway": False, "orchestration": "manual"}},
        platform_key="discord",
        session_key="s1",
    )

    assert "Biff Hot Context" in text
    assert "gateway dispatch=off" in text
    assert "orchestration=manual" in text
    assert "Biff routes Kanban work through Ranger" in text
    assert "K-001" in text


def test_biff_hot_context_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(hot, "get_hermes_home", lambda: Path(tmp_path))

    text = hot.build_biff_hot_context(
        {"biff": {"platforms": {"discord": {"hot_context": False}}}},
        platform_key="discord",
    )

    assert text == ""


def test_biff_hot_context_uses_compact_identity_for_short_direct_turns(monkeypatch, tmp_path):
    hot.clear_biff_hot_context_cache()
    home = Path(tmp_path)
    (home / "SOUL.md").write_text(
        "Forge handles implementation.\nRanger handles Kanban hygiene.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hot, "get_hermes_home", lambda: home)
    monkeypatch.setattr(hot, "_kanban_lines", lambda limit=5: ["- BIF-999: Heavy card [ready, biff]"])

    text = hot.build_biff_hot_context(
        {"kanban": {"dispatch_in_gateway": False}},
        platform_key="discord",
        session_key="compact",
        query="hi",
    )

    assert "Biff Compact Identity" in text
    assert "Discord #hermes is Marco's primary live command surface" in text
    assert "role names alone are conversation" in text
    assert "Care/Spark/Radar" in text
    assert "BIF-999" not in text
    assert "Forge handles implementation" not in text


def test_biff_hot_context_keeps_full_context_for_agentic_work(monkeypatch, tmp_path):
    hot.clear_biff_hot_context_cache()
    home = Path(tmp_path)
    (home / "SOUL.md").write_text(
        "Forge handles implementation.\nRanger handles Kanban hygiene.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hot, "get_hermes_home", lambda: home)
    monkeypatch.setattr(hot, "_kanban_lines", lambda limit=5: ["- BIF-999: Heavy card [ready, biff]"])

    text = hot.build_biff_hot_context(
        {"kanban": {"dispatch_in_gateway": False}},
        platform_key="discord",
        session_key="full",
        query="Continue BIF-1521 and verify the runtime behavior.",
    )

    assert "Biff Hot Context" in text
    assert "Biff Compact Identity" not in text
    assert "BIF-999" in text
    assert "Forge handles implementation" in text


def test_biff_compact_identity_can_be_rolled_back(monkeypatch, tmp_path):
    hot.clear_biff_hot_context_cache()
    home = Path(tmp_path)
    (home / "SOUL.md").write_text("Forge handles implementation.\n", encoding="utf-8")
    monkeypatch.setattr(hot, "get_hermes_home", lambda: home)
    monkeypatch.setattr(hot, "_kanban_lines", lambda limit=5: ["- BIF-999: Heavy card [ready, biff]"])

    text = hot.build_biff_hot_context(
        {"biff": {"platforms": {"discord": {"compact_identity": False}}}},
        platform_key="discord",
        session_key="rollback",
        query="hi",
    )

    assert "Biff Hot Context" in text
    assert "Biff Compact Identity" not in text
    assert "BIF-999" in text
