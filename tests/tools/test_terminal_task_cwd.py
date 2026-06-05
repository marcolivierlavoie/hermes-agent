"""Regression tests for task/session cwd propagation in terminal_tool."""

import json

import tools.terminal_tool as terminal_tool


def _minimal_terminal_config(cwd="/default"):
    return {
        "env_type": "local",
        "cwd": cwd,
        "timeout": 60,
    }


def test_foreground_command_uses_registered_task_cwd_for_existing_environment(monkeypatch):
    """ACP can update task cwd after the local env exists; foreground must honor it."""
    calls = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"output": "ok", "returncode": 0}

    task_id = "acp-session-1"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/acp"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type: {"approved": True},
    )

    result = json.loads(terminal_tool.terminal_tool(command="pwd", task_id=task_id))

    assert result["exit_code"] == 0
    assert calls == [("pwd", {"timeout": 60, "cwd": "/workspace/acp"})]


def test_explicit_workdir_still_wins_over_registered_task_cwd(monkeypatch):
    calls = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            calls.append(kwargs)
            return {"output": "ok", "returncode": 0}

    task_id = "acp-session-1"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/acp"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type: {"approved": True},
    )

    result = json.loads(
        terminal_tool.terminal_tool(
            command="pwd",
            task_id=task_id,
            workdir="/explicit/workdir",
        )
    )

    assert result["exit_code"] == 0
    assert calls == [{"timeout": 60, "cwd": "/explicit/workdir"}]


def test_get_env_config_falls_back_when_process_cwd_deleted(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()

    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(fallback))

    def missing_cwd():
        raise FileNotFoundError("cwd vanished")

    monkeypatch.setattr(terminal_tool.os, "getcwd", missing_cwd)

    config = terminal_tool._get_env_config()

    assert config["cwd"] == str(fallback)


def test_get_env_config_keeps_explicit_terminal_cwd_when_process_cwd_deleted(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", "/definitely/missing/hermes-cwd")

    def missing_cwd():
        raise FileNotFoundError("cwd vanished")

    monkeypatch.setattr(terminal_tool.os, "getcwd", missing_cwd)

    config = terminal_tool._get_env_config()

    # The deleted process cwd guard chooses a safe default, but an explicit
    # TERMINAL_CWD still remains the user/config override. LocalEnvironment has
    # the per-command ancestor recovery guard for stale explicit cwd values.
    assert config["cwd"] == "/definitely/missing/hermes-cwd"


def test_get_env_config_uses_home_when_process_cwd_deleted_without_terminal_cwd(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.delenv("TERMINAL_CWD", raising=False)

    def missing_cwd():
        raise FileNotFoundError("cwd vanished")

    monkeypatch.setattr(terminal_tool.os, "getcwd", missing_cwd)

    config = terminal_tool._get_env_config()

    assert config["cwd"] == terminal_tool.os.path.expanduser("~")


def test_docker_mount_cwd_falls_back_when_process_cwd_deleted(monkeypatch, tmp_path):
    fallback = tmp_path / "docker-host-cwd"
    fallback.mkdir()

    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "true")
    monkeypatch.setenv("TERMINAL_CWD", str(fallback))

    def missing_cwd():
        raise FileNotFoundError("cwd vanished")

    monkeypatch.setattr(terminal_tool.os, "getcwd", missing_cwd)

    config = terminal_tool._get_env_config()

    assert config["cwd"] == "/workspace"
    assert config["host_cwd"] == str(fallback)
