#!/usr/bin/env python3
"""Create and verify an isolated Mnemosyne memory-provider sandbox.

This harness intentionally writes only under the provided sandbox HERMES_HOME.
It installs a minimal user memory-provider plugin at
$HERMES_HOME/plugins/mnemosyne and configures Hermes to select it. The plugin is
local-file backed by default so the compatibility path can be validated without
installing Mnemosyne packages, mutating production ~/.hermes, or allowing host
LLM calls.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SANDBOX = REPO_ROOT / ".sandbox" / "mnemosyne-hermes-home"
PRODUCTION_HERMES_HOME = Path("/Users/marco/.hermes").resolve()

PLUGIN_INIT = r'''
"""Sandbox Mnemosyne memory provider for Hermes.

Safety defaults:
- MNEMOSYNE_DATA_DIR is required and must live under HERMES_HOME.
- Host/LLM extraction is disabled unless config explicitly enables it.
- Retrieval and write sizes are capped conservatively.
- Export/import/restore operate on JSON snapshots inside the provider data dir.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider


class MnemosyneMemoryProvider(MemoryProvider):
    MAX_TOP_K = 5
    DEFAULT_TOP_K = 3
    MAX_MEMORY_CHARS = 1200
    MAX_RESULTS_CHARS = 4000

    @property
    def name(self) -> str:
        return "mnemosyne"

    def __init__(self) -> None:
        self.hermes_home = Path(os.environ.get("HERMES_HOME", "")).expanduser()
        self.data_dir = Path(os.environ.get("MNEMOSYNE_DATA_DIR", "")).expanduser()
        self.config: Dict[str, Any] = {}
        self.session_id = ""

    def _config_path(self) -> Path:
        return self.hermes_home / "mnemosyne" / "config.json"

    def _store_path(self) -> Path:
        return self.data_dir / "memories.jsonl"

    def _snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    def _load_config(self) -> Dict[str, Any]:
        cfg = {
            "host_llm_enabled": False,
            "top_k": self.DEFAULT_TOP_K,
            "max_memory_chars": self.MAX_MEMORY_CHARS,
            "max_results_chars": self.MAX_RESULTS_CHARS,
        }
        path = self._config_path()
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cfg.update(loaded)
            except Exception:
                pass
        env_llm = os.environ.get("MNEMOSYNE_HOST_LLM_ENABLED")
        if env_llm is not None:
            cfg["host_llm_enabled"] = env_llm.strip().lower() in {"1", "true", "yes", "on"}
        else:
            raw_llm = cfg.get("host_llm_enabled", False)
            cfg["host_llm_enabled"] = raw_llm if isinstance(raw_llm, bool) else str(raw_llm).strip().lower() in {"1", "true", "yes", "on"}
        cfg["top_k"] = max(1, min(int(cfg.get("top_k", self.DEFAULT_TOP_K)), self.MAX_TOP_K))
        cfg["max_memory_chars"] = max(1, min(int(cfg.get("max_memory_chars", self.MAX_MEMORY_CHARS)), self.MAX_MEMORY_CHARS))
        cfg["max_results_chars"] = max(256, min(int(cfg.get("max_results_chars", self.MAX_RESULTS_CHARS)), self.MAX_RESULTS_CHARS))
        return cfg

    def _validate_paths(self) -> None:
        if not self.hermes_home:
            raise RuntimeError("HERMES_HOME is required for mnemosyne sandbox provider")
        if not self.data_dir:
            raise RuntimeError("MNEMOSYNE_DATA_DIR is required for mnemosyne sandbox provider")
        home = self.hermes_home.resolve()
        data = self.data_dir.resolve()
        try:
            data.relative_to(home)
        except ValueError as exc:
            raise RuntimeError(f"MNEMOSYNE_DATA_DIR must be under HERMES_HOME: {data}") from exc

    def is_available(self) -> bool:
        try:
            self._validate_paths()
            return True
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = kwargs.get("hermes_home") or os.environ.get("HERMES_HOME", "")
        if hermes_home:
            self.hermes_home = Path(hermes_home).expanduser()
        self.data_dir = Path(os.environ.get("MNEMOSYNE_DATA_DIR", self.hermes_home / "mnemosyne" / "data")).expanduser()
        self._validate_paths()
        self.config = self._load_config()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots_dir().mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        if not self._store_path().exists():
            self._store_path().write_text("", encoding="utf-8")

    def system_prompt_block(self) -> str:
        return "Mnemosyne memory provider active. Host LLM extraction disabled; use explicit mnemosyne_* tools only."

    def _read_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        path = self._store_path()
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
        return rows

    def _write_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path().with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + ("\n" if rows else ""), encoding="utf-8")
        tmp.replace(self._store_path())

    def _remember(self, content: str, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self.initialize(self.session_id or "manual") if not self.config else None
        content = (content or "").strip()[: self.config.get("max_memory_chars", self.MAX_MEMORY_CHARS)]
        if not content:
            return {"ok": False, "error": "empty content"}
        rows = self._read_rows()
        rec = {
            "id": f"mnemo-{int(time.time() * 1000)}-{len(rows) + 1}",
            "content": content,
            "metadata": metadata or {},
            "session_id": self.session_id,
            "created_at": time.time(),
        }
        rows.append(rec)
        self._write_rows(rows)
        return {"ok": True, "id": rec["id"]}

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        result = self._search(query, top_k=self.config.get("top_k", self.DEFAULT_TOP_K) if self.config else self.DEFAULT_TOP_K)
        memories = result.get("memories", [])
        if not memories:
            return ""
        lines = ["Mnemosyne relevant memories:"]
        for item in memories:
            lines.append(f"- {item['content']}")
        return "\n".join(lines)[: self.config.get("max_results_chars", self.MAX_RESULTS_CHARS)]

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Deliberately no automatic LLM extraction. Store only a conservative explicit transcript marker.
        if not self.config:
            self.initialize(session_id or self.session_id or "sync")
        if self.config.get("host_llm_enabled", False):
            # Reserved for a future explicit integration; disabled by default and not implemented here.
            return

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {"name": "mnemosyne_search", "description": "Search sandbox Mnemosyne memories.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": self.MAX_TOP_K}}, "required": ["query"]}},
            {"name": "mnemosyne_remember", "description": "Add one explicit sandbox Mnemosyne memory.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "metadata": {"type": "object"}}, "required": ["content"]}},
            {"name": "mnemosyne_export", "description": "Export a JSON snapshot of sandbox Mnemosyne memory.", "parameters": {"type": "object", "properties": {"label": {"type": "string"}}}},
            {"name": "mnemosyne_import", "description": "Import memories from a JSON snapshot file under MNEMOSYNE_DATA_DIR.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "mnemosyne_restore", "description": "Restore sandbox Mnemosyne memory from a snapshot created by mnemosyne_export.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        ]

    def _search(self, query: str, top_k: int | None = None) -> Dict[str, Any]:
        if not self.config:
            self.initialize(self.session_id or "search")
        terms = {t.lower() for t in (query or "").split() if t.strip()}
        k = max(1, min(int(top_k or self.config.get("top_k", self.DEFAULT_TOP_K)), self.MAX_TOP_K))
        scored = []
        for row in self._read_rows():
            content = str(row.get("content", ""))
            lower = content.lower()
            score = sum(1 for t in terms if t in lower) if terms else 0
            if score or not terms:
                scored.append((score, row))
        scored.sort(key=lambda x: (x[0], x[1].get("created_at", 0)), reverse=True)
        memories = [r for _, r in scored[:k]]
        return {"ok": True, "top_k": k, "memories": memories}

    def _export(self, label: str = "") -> Dict[str, Any]:
        if not self.config:
            self.initialize(self.session_id or "export")
        safe_label = "".join(c for c in (label or "snapshot") if c.isalnum() or c in ("-", "_"))[:40] or "snapshot"
        out = self._snapshots_dir() / f"{int(time.time())}-{safe_label}.json"
        payload = {"provider": "mnemosyne", "version": 1, "host_llm_enabled": self.config.get("host_llm_enabled", False), "memories": self._read_rows()}
        out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return {"ok": True, "path": str(out), "count": len(payload["memories"])}

    def _assert_under_data_dir(self, path: str) -> Path:
        p = Path(path).expanduser().resolve()
        root = self.data_dir.resolve()
        try:
            p.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("snapshot path must be under MNEMOSYNE_DATA_DIR") from exc
        return p

    def _import_or_restore(self, path: str, *, restore: bool) -> Dict[str, Any]:
        if not self.config:
            self.initialize(self.session_id or "restore")
        src = self._assert_under_data_dir(path)
        payload = json.loads(src.read_text(encoding="utf-8"))
        incoming = payload.get("memories", payload if isinstance(payload, list) else [])
        if not isinstance(incoming, list):
            raise RuntimeError("snapshot must contain a memories list")
        current = [] if restore else self._read_rows()
        existing_ids = {r.get("id") for r in current}
        for row in incoming:
            if isinstance(row, dict) and row.get("id") not in existing_ids:
                current.append(row)
                existing_ids.add(row.get("id"))
        self._write_rows(current)
        return {"ok": True, "mode": "restore" if restore else "import", "count": len(current)}

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            if tool_name == "mnemosyne_search":
                return json.dumps(self._search(args.get("query", ""), args.get("top_k")))
            if tool_name == "mnemosyne_remember":
                return json.dumps(self._remember(args.get("content", ""), args.get("metadata") or {}))
            if tool_name == "mnemosyne_export":
                return json.dumps(self._export(args.get("label", "")))
            if tool_name == "mnemosyne_import":
                return json.dumps(self._import_or_restore(args.get("path", ""), restore=False))
            if tool_name == "mnemosyne_restore":
                return json.dumps(self._import_or_restore(args.get("path", ""), restore=True))
            return json.dumps({"ok": False, "error": f"unknown tool {tool_name}"})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})


def register(ctx) -> None:
    ctx.register_memory_provider(MnemosyneMemoryProvider())
'''

PLUGIN_YAML = """name: mnemosyne\ndescription: Sandbox Mnemosyne memory provider (local-file compatibility mode)\nversion: 0.1.0\nkind: exclusive\n"""


def assert_safe_sandbox(hermes_home: Path) -> None:
    resolved = hermes_home.expanduser().resolve()
    if resolved == PRODUCTION_HERMES_HOME:
        raise RuntimeError(f"refusing to use production HERMES_HOME as sandbox: {resolved}")
    try:
        resolved.relative_to(PRODUCTION_HERMES_HOME)
    except ValueError:
        return
    # Allow the checked-out Biff runtime worktree's own .sandbox directory, but
    # fail closed for normal profile/config/memory paths under ~/.hermes.
    allowed = (REPO_ROOT / ".sandbox").resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError(f"sandbox must not live under production ~/.hermes except repo .sandbox: {resolved}") from exc


def write_sandbox(hermes_home: Path, force: bool = False) -> None:
    assert_safe_sandbox(hermes_home)
    if hermes_home.exists() and force:
        shutil.rmtree(hermes_home)
    hermes_home.mkdir(parents=True, exist_ok=True)
    plugin_dir = hermes_home / "plugins" / "mnemosyne"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(PLUGIN_INIT, encoding="utf-8")
    (plugin_dir / "plugin.yaml").write_text(PLUGIN_YAML, encoding="utf-8")

    data_dir = hermes_home / "profiles" / "mnemosyne-sandbox" / "mnemosyne" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (hermes_home / "mnemosyne").mkdir(parents=True, exist_ok=True)
    (hermes_home / "mnemosyne" / "config.json").write_text(
        json.dumps(
            {
                "host_llm_enabled": False,
                "top_k": 3,
                "max_memory_chars": 1200,
                "max_results_chars": 4000,
                "data_dir": str(data_dir),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (hermes_home / "config.yaml").write_text(
        textwrap.dedent(
            f"""
            memory:
              provider: mnemosyne
            mnemosyne:
              data_dir: {data_dir}
              host_llm_enabled: false
              top_k: 3
              max_memory_chars: 1200
              max_results_chars: 4000
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (hermes_home / ".env").write_text(f"MNEMOSYNE_DATA_DIR={data_dir}\nMNEMOSYNE_HOST_LLM_ENABLED=false\n", encoding="utf-8")


def run_verify(hermes_home: Path) -> dict:
    data_dir = hermes_home / "profiles" / "mnemosyne-sandbox" / "mnemosyne" / "data"
    env = os.environ.copy()
    env.update(
        {
            "HERMES_HOME": str(hermes_home),
            "MNEMOSYNE_DATA_DIR": str(data_dir),
            "MNEMOSYNE_HOST_LLM_ENABLED": "false",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    code = r'''
import json
from plugins.memory import discover_memory_providers, load_memory_provider
from agent.memory_manager import MemoryManager

providers = discover_memory_providers()
mnemosyne = [p for p in providers if p[0] == "mnemosyne"]
assert len(mnemosyne) == 1, providers
provider = load_memory_provider("mnemosyne")
assert provider is not None
assert provider.is_available()
provider.initialize("sandbox-session", hermes_home=__import__("os").environ["HERMES_HOME"], platform="cli")
assert provider.config["host_llm_enabled"] is False
assert provider.config["top_k"] <= provider.MAX_TOP_K
mgr = MemoryManager()
mgr.add_provider(provider)
mgr.add_provider(load_memory_provider("mnemosyne"))
assert [p.name for p in mgr.providers] == ["mnemosyne"]
tools = mgr.get_all_tool_schemas()
names = [t["name"] for t in tools]
assert len(names) == len(set(names)), names
# Simulate run_agent duplicate-guard when general plugin and MemoryProvider paths coexist.
existing = [{"type": "function", "function": tools[0]}]
existing_names = {t.get("function", {}).get("name") for t in existing}
for schema in tools:
    if schema["name"] in existing_names:
        continue
    existing.append({"type": "function", "function": schema})
    existing_names.add(schema["name"])
assert len([t for t in existing if t["function"]["name"] == tools[0]["name"]]) == 1
assert mgr.build_system_prompt().count("Mnemosyne memory provider active") == 1
remember = json.loads(provider.handle_tool_call("mnemosyne_remember", {"content": "sandbox rollback proof token"}))
assert remember["ok"]
search = json.loads(provider.handle_tool_call("mnemosyne_search", {"query": "rollback", "top_k": 99}))
assert search["ok"] and search["top_k"] == provider.MAX_TOP_K and len(search["memories"]) == 1
exported = json.loads(provider.handle_tool_call("mnemosyne_export", {"label": "rollback-proof"}))
assert exported["ok"] and exported["count"] == 1
second = json.loads(provider.handle_tool_call("mnemosyne_remember", {"content": "temporary memory removed by restore"}))
assert second["ok"]
restored = json.loads(provider.handle_tool_call("mnemosyne_restore", {"path": exported["path"]}))
assert restored["ok"] and restored["count"] == 1
post = json.loads(provider.handle_tool_call("mnemosyne_search", {"query": "temporary", "top_k": 5}))
assert post["ok"] and len(post["memories"]) == 0
imported = json.loads(provider.handle_tool_call("mnemosyne_import", {"path": exported["path"]}))
assert imported["ok"] and imported["count"] == 1
print(json.dumps({
    "mnemosyne_discovery_count": len(mnemosyne),
    "provider_available": provider.is_available(),
    "data_dir": __import__("os").environ["MNEMOSYNE_DATA_DIR"],
    "host_llm_enabled": provider.config["host_llm_enabled"],
    "top_k_cap": provider.MAX_TOP_K,
    "tool_names": names,
    "tool_name_count": len(names),
    "system_prompt_mentions": mgr.build_system_prompt().count("Mnemosyne memory provider active"),
    "export_path": exported["path"],
    "restore_count": restored["count"],
}, sort_keys=True))
'''
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def rollback(hermes_home: Path) -> None:
    assert_safe_sandbox(hermes_home)
    if hermes_home.exists():
        shutil.rmtree(hermes_home)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-home", type=Path, default=DEFAULT_SANDBOX)
    parser.add_argument("--force", action="store_true", help="delete/recreate sandbox before install")
    parser.add_argument("--verify", action="store_true", help="run isolated verification after install")
    parser.add_argument("--rollback", action="store_true", help="remove the sandbox home and exit")
    args = parser.parse_args()

    sandbox = args.sandbox_home.expanduser().resolve()
    if args.rollback:
        rollback(sandbox)
        print(json.dumps({"rolled_back": str(sandbox), "exists": sandbox.exists()}, sort_keys=True))
        return 0

    write_sandbox(sandbox, force=args.force)
    result = {"sandbox_home": str(sandbox), "installed": True}
    if args.verify:
        result["verification"] = run_verify(sandbox)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
