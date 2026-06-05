"""Gateway runtime policy and diagnostics for Biff production launch paths.

The live Biff gateway intentionally runs from the customized runtime tree, not
from an arbitrary checkout.  This module centralizes the small amount of path
policy needed by startup diagnostics, launch wrappers, and doctor checks.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

_DEFAULT_BIFF_RUNTIME = Path("/Users/marco/.hermes/hermes-agent-biff-runtime")
_REQUIRED_MODULES = (
    "gateway.run",
    "gateway.session_hygiene",
    "hermes_cli.main",
)


def _resolve(path: str | os.PathLike[str] | None) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return str(Path(path).expanduser())


def _is_relative_to(path: str | None, root: str | None) -> bool:
    if not path or not root:
        return False
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def canonical_runtime_dir() -> Path:
    raw = os.getenv("HERMES_CANONICAL_RUNTIME_DIR")
    if raw:
        return Path(raw).expanduser()
    if _DEFAULT_BIFF_RUNTIME.exists():
        return _DEFAULT_BIFF_RUNTIME
    return Path(__file__).resolve().parents[1]


def canonical_venv_dir(runtime_dir: Path | None = None) -> Path:
    raw = os.getenv("HERMES_CANONICAL_VENV")
    if raw:
        return Path(raw).expanduser()
    runtime = runtime_dir or canonical_runtime_dir()
    return runtime / "venv"


def module_origin(module_name: str) -> str | None:
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception:
        return None
    origin = getattr(spec, "origin", None)
    if not origin or origin == "built-in":
        return origin
    return str(Path(origin).resolve())


def collect_gateway_runtime_diagnostics(modules: Iterable[str] = _REQUIRED_MODULES) -> dict[str, Any]:
    runtime = canonical_runtime_dir()
    venv = canonical_venv_dir(runtime)
    env_virtual = os.getenv("VIRTUAL_ENV")
    return {
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "cwd_resolved": _resolve(os.getcwd()),
        "canonical_runtime_dir": str(runtime),
        "canonical_runtime_dir_resolved": _resolve(runtime),
        "canonical_venv": str(venv),
        "canonical_venv_resolved": _resolve(venv),
        "sys_executable": sys.executable,
        "sys_executable_resolved": _resolve(sys.executable),
        "sys_prefix": sys.prefix,
        "base_prefix": getattr(sys, "base_prefix", ""),
        "virtual_env": env_virtual,
        "virtual_env_resolved": _resolve(env_virtual),
        "config_path": str(Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser() / "config.yaml"),
        "pythonpath": os.getenv("PYTHONPATH", ""),
        "sys_path_first": list(sys.path[:8]),
        "module_origins": {name: module_origin(name) for name in modules},
    }


def gateway_runtime_policy_violations(diag: dict[str, Any] | None = None) -> list[str]:
    diag = diag or collect_gateway_runtime_diagnostics()
    runtime_resolved = diag.get("canonical_runtime_dir_resolved")
    venv_raw = diag.get("canonical_venv")
    venv_resolved = diag.get("canonical_venv_resolved")
    violations: list[str] = []

    if diag.get("cwd_resolved") != runtime_resolved:
        violations.append(
            f"cwd {diag.get('cwd')} does not match canonical runtime {diag.get('canonical_runtime_dir')}"
        )

    virtual_env_raw = diag.get("virtual_env")
    virtual_env_resolved = diag.get("virtual_env_resolved")
    virtual_env_matches_raw = virtual_env_raw == venv_raw
    virtual_env_matches_resolved = bool(
        venv_resolved and virtual_env_resolved and virtual_env_resolved == venv_resolved
    )
    if not (virtual_env_matches_raw or virtual_env_matches_resolved):
        violations.append(
            f"VIRTUAL_ENV {diag.get('virtual_env')} does not match canonical venv {venv_raw}"
        )

    executable_raw = diag.get("sys_executable")
    executable_resolved = diag.get("sys_executable_resolved")
    # Runtime venvs can be symlinks, and virtualenv Python shims can themselves
    # resolve outside the apparent venv (for example to a framework/Homebrew
    # Python).  Accept either raw or resolved executable paths under either the
    # canonical venv path or its resolved target; reject only when all views point
    # elsewhere.
    executable_in_raw_venv = _is_relative_to(str(executable_raw), str(venv_raw))
    executable_raw_in_resolved_venv = _is_relative_to(str(executable_raw), str(venv_resolved))
    executable_in_resolved_venv = _is_relative_to(str(executable_resolved), str(venv_resolved))
    if venv_resolved and executable_resolved and not (
        executable_in_raw_venv or executable_raw_in_resolved_venv or executable_in_resolved_venv
    ):
        violations.append(
            f"Python executable {diag.get('sys_executable')} is not inside canonical venv {venv_raw}"
        )

    for module_name, origin in (diag.get("module_origins") or {}).items():
        if not origin or not _is_relative_to(str(origin), runtime_resolved):
            violations.append(
                f"{module_name} resolves to {origin!r}, outside canonical runtime {diag.get('canonical_runtime_dir')}"
            )

    return violations


def format_gateway_runtime_diagnostics(diag: dict[str, Any] | None = None) -> str:
    return json.dumps(diag or collect_gateway_runtime_diagnostics(), sort_keys=True, default=str)
