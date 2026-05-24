import asyncio
from pathlib import Path

from gateway.runtime_policy import gateway_runtime_policy_violations


def _diag(runtime: Path, venv: Path) -> dict:
    return {
        "cwd": str(runtime),
        "cwd_resolved": str(runtime.resolve()),
        "canonical_runtime_dir": str(runtime),
        "canonical_runtime_dir_resolved": str(runtime.resolve()),
        "canonical_venv": str(venv),
        "canonical_venv_resolved": str(venv.resolve()),
        "sys_executable": str(venv / "bin" / "python"),
        "sys_executable_resolved": str((venv / "bin" / "python").resolve()),
        "virtual_env": str(venv),
        "virtual_env_resolved": str(venv.resolve()),
        "module_origins": {
            "gateway.run": str((runtime / "gateway" / "run.py").resolve()),
            "gateway.session_hygiene": str((runtime / "gateway" / "session_hygiene.py").resolve()),
            "hermes_cli.main": str((runtime / "hermes_cli" / "main.py").resolve()),
        },
    }


def test_gateway_runtime_policy_accepts_canonical_runtime(tmp_path):
    runtime = tmp_path / "hermes-agent-biff-runtime"
    venv = runtime / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")

    assert gateway_runtime_policy_violations(_diag(runtime, venv)) == []


def test_gateway_runtime_policy_accepts_venv_python_symlink_resolving_outside_venv(tmp_path):
    runtime = tmp_path / "hermes-agent-biff-runtime"
    venv = runtime / "venv"
    real_python = tmp_path / "Python.framework" / "Versions" / "Current" / "bin" / "python3"
    (venv / "bin").mkdir(parents=True)
    real_python.parent.mkdir(parents=True)
    real_python.write_text("", encoding="utf-8")

    diag = _diag(runtime, venv)
    diag["sys_executable"] = str(venv / "bin" / "python")
    diag["sys_executable_resolved"] = str(real_python.resolve())

    assert gateway_runtime_policy_violations(diag) == []


def test_gateway_runtime_policy_rejects_old_split_brain_virtualenv(tmp_path):
    runtime = tmp_path / "hermes-agent-biff-runtime"
    old_runtime = tmp_path / "hermes-agent"
    venv = runtime / "venv"
    old_venv = old_runtime / "venv"
    (venv / "bin").mkdir(parents=True)
    (old_venv / "bin").mkdir(parents=True)
    (old_venv / "bin" / "python").write_text("", encoding="utf-8")

    diag = _diag(runtime, venv)
    diag["virtual_env"] = str(old_venv)
    diag["virtual_env_resolved"] = str(old_venv.resolve())
    diag["sys_executable"] = str(old_venv / "bin" / "python")
    diag["sys_executable_resolved"] = str((old_venv / "bin" / "python").resolve())

    violations = gateway_runtime_policy_violations(diag)

    assert any("VIRTUAL_ENV" in violation for violation in violations)
    assert any("Python executable" in violation for violation in violations)


def test_gateway_runtime_policy_rejects_module_outside_canonical_runtime(tmp_path):
    runtime = tmp_path / "hermes-agent-biff-runtime"
    old_runtime = tmp_path / "hermes-agent"
    venv = runtime / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")

    diag = _diag(runtime, venv)
    diag["module_origins"]["hermes_cli.main"] = str(old_runtime / "hermes_cli" / "main.py")

    violations = gateway_runtime_policy_violations(diag)

    assert any("hermes_cli.main" in violation for violation in violations)

def test_start_gateway_enforce_mode_returns_false_on_runtime_policy_violation(monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(
        "gateway.runtime_policy.collect_gateway_runtime_diagnostics",
        lambda: {"cwd": "/tmp/old-runtime"},
    )
    monkeypatch.setattr(
        "gateway.runtime_policy.format_gateway_runtime_diagnostics",
        lambda diag: "cwd=/tmp/old-runtime",
    )
    monkeypatch.setattr(
        "gateway.runtime_policy.gateway_runtime_policy_violations",
        lambda diag: ["cwd /tmp/old-runtime does not match canonical runtime"],
    )
    monkeypatch.setenv("HERMES_BIFF_RUNTIME_POLICY", "enforce")

    assert asyncio.run(gateway_run.start_gateway(config=None, verbosity=None)) is False


def test_start_gateway_enforce_mode_checks_policy_before_replace_side_effects(monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr("hermes_logging.setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(
        "gateway.runtime_policy.collect_gateway_runtime_diagnostics",
        lambda: {"cwd": "/tmp/old-runtime"},
    )
    monkeypatch.setattr(
        "gateway.runtime_policy.format_gateway_runtime_diagnostics",
        lambda diag: "cwd=/tmp/old-runtime",
    )
    monkeypatch.setattr(
        "gateway.runtime_policy.gateway_runtime_policy_violations",
        lambda diag: ["cwd /tmp/old-runtime does not match canonical runtime"],
    )
    monkeypatch.setattr(
        "gateway.status.get_running_pid",
        lambda: (_ for _ in ()).throw(AssertionError("duplicate-instance guard should not run before policy enforcement")),
    )
    monkeypatch.setenv("HERMES_BIFF_RUNTIME_POLICY", "enforce")

    assert asyncio.run(gateway_run.start_gateway(config=None, replace=True, verbosity=None)) is False
