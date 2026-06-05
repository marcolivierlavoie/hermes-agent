from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.mnemosyne_sandbox_harness import assert_safe_sandbox, run_verify, write_sandbox


def test_mnemosyne_discovery_once_and_safety_defaults(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes-home"
    write_sandbox(hermes_home)
    data_dir = hermes_home / "profiles" / "mnemosyne-sandbox" / "mnemosyne" / "data"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MNEMOSYNE_HOST_LLM_ENABLED", "false")

    from plugins.memory import discover_memory_providers, load_memory_provider

    discovered = discover_memory_providers()
    mnemosyne = [item for item in discovered if item[0] == "mnemosyne"]
    assert len(mnemosyne) == 1
    assert mnemosyne[0][2] is True

    provider = load_memory_provider("mnemosyne")
    assert provider is not None
    assert provider.is_available() is True
    provider.initialize("pytest-session", hermes_home=str(hermes_home), platform="cli")
    assert Path(os.environ["MNEMOSYNE_DATA_DIR"]).resolve().is_relative_to(hermes_home.resolve())
    assert provider.config["host_llm_enabled"] is False
    assert provider.config["top_k"] == 3
    assert provider.MAX_TOP_K == 5
    assert provider.MAX_MEMORY_CHARS == 1200


def test_mnemosyne_host_llm_false_string_stays_disabled(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes-home"
    write_sandbox(hermes_home)
    data_dir = hermes_home / "profiles" / "mnemosyne-sandbox" / "mnemosyne" / "data"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MNEMOSYNE_HOST_LLM_ENABLED", "false")
    (hermes_home / "mnemosyne" / "config.json").write_text(
        json.dumps({"host_llm_enabled": "false", "top_k": 99}),
        encoding="utf-8",
    )

    from plugins.memory import load_memory_provider

    provider = load_memory_provider("mnemosyne")
    assert provider is not None
    provider.initialize("pytest-session", hermes_home=str(hermes_home), platform="cli")
    assert provider.config["host_llm_enabled"] is False
    assert provider.config["top_k"] == provider.MAX_TOP_K


def test_harness_refuses_production_hermes_paths():
    production_home = Path("/Users/marco/.hermes")
    for path in [production_home, production_home / "profiles" / "biff" / "mnemosyne"]:
        try:
            assert_safe_sandbox(path)
        except RuntimeError as exc:
            assert "production" in str(exc) or "~/.hermes" in str(exc)
        else:
            raise AssertionError(f"expected production path refusal for {path}")


def test_rollback_refuses_production_hermes_paths():
    repo = Path(__file__).resolve().parents[1]
    for path in [Path("/Users/marco/.hermes"), Path("/Users/marco/.hermes/profiles/biff/mnemosyne")]:
        proc = subprocess.run(
            [sys.executable, "scripts/mnemosyne_sandbox_harness.py", "--sandbox-home", str(path), "--rollback"],
            cwd=repo,
            text=True,
            capture_output=True,
        )
        assert proc.returncode != 0
        assert "production" in proc.stderr or "~/.hermes" in proc.stderr


def test_mnemosyne_no_duplicate_tools_or_context_when_paths_coexist(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes-home"
    write_sandbox(hermes_home)
    data_dir = hermes_home / "profiles" / "mnemosyne-sandbox" / "mnemosyne" / "data"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(data_dir))

    from agent.memory_manager import MemoryManager
    from plugins.memory import load_memory_provider

    first = load_memory_provider("mnemosyne")
    second = load_memory_provider("mnemosyne")
    assert first is not None and second is not None
    first.initialize("pytest-session", hermes_home=str(hermes_home), platform="cli")
    second.initialize("pytest-session", hermes_home=str(hermes_home), platform="cli")

    manager = MemoryManager()
    manager.add_provider(first)
    manager.add_provider(second)
    assert [p.name for p in manager.providers] == ["mnemosyne"]

    schemas = manager.get_all_tool_schemas()
    names = [schema["name"] for schema in schemas]
    assert len(names) == len(set(names))

    # Simulates run_agent's duplicate guard when a plugin path already injected
    # one function and the MemoryProvider path tries to append schemas too.
    injected_tools = [{"type": "function", "function": schemas[0]}]
    existing_names = {tool.get("function", {}).get("name") for tool in injected_tools}
    for schema in schemas:
        name = schema["name"]
        if name in existing_names:
            continue
        injected_tools.append({"type": "function", "function": schema})
        existing_names.add(name)
    assert [tool["function"]["name"] for tool in injected_tools].count(schemas[0]["name"]) == 1
    assert manager.build_system_prompt().count("Mnemosyne memory provider active") == 1


def test_mnemosyne_export_restore_and_rollback_proof(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes-home"
    write_sandbox(hermes_home)
    data_dir = hermes_home / "profiles" / "mnemosyne-sandbox" / "mnemosyne" / "data"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(data_dir))

    from plugins.memory import load_memory_provider

    provider = load_memory_provider("mnemosyne")
    assert provider is not None
    provider.initialize("pytest-session", hermes_home=str(hermes_home), platform="cli")

    remembered = json.loads(provider.handle_tool_call("mnemosyne_remember", {"content": "sandbox rollback proof token"}))
    assert remembered["ok"] is True
    search = json.loads(provider.handle_tool_call("mnemosyne_search", {"query": "rollback", "top_k": 99}))
    assert search["top_k"] == provider.MAX_TOP_K
    assert len(search["memories"]) == 1

    exported = json.loads(provider.handle_tool_call("mnemosyne_export", {"label": "rollback-proof"}))
    assert exported["ok"] is True
    assert Path(exported["path"]).is_file()
    assert Path(exported["path"]).resolve().is_relative_to(data_dir.resolve())

    extra = json.loads(provider.handle_tool_call("mnemosyne_remember", {"content": "temporary memory removed by restore"}))
    assert extra["ok"] is True
    restored = json.loads(provider.handle_tool_call("mnemosyne_restore", {"path": exported["path"]}))
    assert restored == {"ok": True, "mode": "restore", "count": 1}
    post = json.loads(provider.handle_tool_call("mnemosyne_search", {"query": "temporary", "top_k": 5}))
    assert post["memories"] == []
    imported = json.loads(provider.handle_tool_call("mnemosyne_import", {"path": exported["path"]}))
    assert imported == {"ok": True, "mode": "import", "count": 1}


def test_mnemosyne_harness_verify_and_rollback(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    write_sandbox(hermes_home)
    result = run_verify(hermes_home)
    assert result["mnemosyne_discovery_count"] == 1
    assert result["provider_available"] is True
    assert result["host_llm_enabled"] is False
    assert result["top_k_cap"] == 5
    assert result["restore_count"] == 1

    proc = subprocess.run(
        [sys.executable, "scripts/mnemosyne_sandbox_harness.py", "--sandbox-home", str(hermes_home), "--rollback"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["exists"] is False
    assert not hermes_home.exists()
