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
