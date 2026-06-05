from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "infisical_lookup.py"


def load_module():
    spec = importlib.util.spec_from_file_location("infisical_lookup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_fake_infisical(tmp_path: Path, body: str) -> Path:
    exe = tmp_path / "infisical"
    exe.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def test_check_reports_cli_missing(monkeypatch, capsys):
    module = load_module()
    monkeypatch.setenv("PATH", "")

    code = module.main(["--check"], env={})

    assert code == module.EXIT_CLI_MISSING
    assert "class=cli_missing" in capsys.readouterr().out


def test_check_reports_config_missing_with_cli(monkeypatch, tmp_path, capsys):
    module = load_module()
    write_fake_infisical(tmp_path, "import sys; sys.exit(0)")
    monkeypatch.setenv("PATH", str(tmp_path))

    code = module.main(["--check"], env={})

    assert code == module.EXIT_CONFIG_MISSING
    assert "class=config_missing" in capsys.readouterr().out


def test_lookup_uses_env_token_not_argv_and_never_prints_secret(monkeypatch, tmp_path, capsys):
    module = load_module()
    argv_file = tmp_path / "argv.txt"
    write_fake_infisical(
        tmp_path,
        f"""
        import os, pathlib, sys
        pathlib.Path({str(argv_file)!r}).write_text('\\n'.join(sys.argv), encoding='utf-8')
        assert os.environ.get('INFISICAL_TOKEN') == 'token-secret-value'
        sys.stdout.write('super-secret-value\\n')
        """,
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    code = module.main(
        ["MY_SECRET"],
        env={"INFISICAL_TOKEN": "token-secret-value", "INFISICAL_PROJECT_ID": "project-123"},
    )

    output = capsys.readouterr().out
    assert code == module.EXIT_OK
    assert "class=ok" in output
    assert "bytes=18" in output
    assert "super-secret-value" not in output
    argv = argv_file.read_text(encoding="utf-8")
    assert "token-secret-value" not in argv
    assert "--token" not in argv
    assert "--projectId" in argv


def test_lookup_redacts_config_values_on_auth_failure(monkeypatch, tmp_path, capsys):
    module = load_module()
    write_fake_infisical(
        tmp_path,
        """
        import sys
        sys.stderr.write('Request workspaceId=project-123 token=token-secret-value Response Code: 403 Forbidden')
        sys.exit(1)
        """,
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    code = module.main(
        ["MY_SECRET"],
        env={"INFISICAL_TOKEN": "token-secret-value", "INFISICAL_PROJECT_ID": "project-123"},
    )

    output = capsys.readouterr().out
    assert code == module.EXIT_AUTH_OR_RATE_LIMIT
    assert "class=auth_or_rate_limit" in output
    assert "token-secret-value" not in output
    assert "project-123" not in output
    assert "<redacted>" in output


def test_lookup_classifies_not_found(monkeypatch, tmp_path, capsys):
    module = load_module()
    write_fake_infisical(
        tmp_path,
        """
        import sys
        sys.stderr.write('Error: secret not found')
        sys.exit(1)
        """,
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    code = module.main(
        ["MISSING_SECRET"],
        env={"INFISICAL_TOKEN": "token", "INFISICAL_PROJECT_ID": "project"},
    )

    assert code == module.EXIT_NOT_FOUND
    assert "class=not_found" in capsys.readouterr().out


def test_secure_cache_hit_avoids_infisical_cli(monkeypatch, tmp_path, capsys):
    module = load_module()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(mode=0o700)
    secret_file = cache_dir / "CACHED_SECRET"
    secret_file.write_bytes(b"cached-secret")
    secret_file.chmod(0o600)
    monkeypatch.setenv("PATH", "")

    code = module.main(["CACHED_SECRET", "--cache-dir", str(cache_dir)], env={})

    output = capsys.readouterr().out
    assert code == module.EXIT_CACHE
    assert "class=cache" in output
    assert "bytes=13" in output
    assert "cached-secret" not in output


def test_insecure_cache_permissions_are_config_missing(tmp_path, capsys):
    module = load_module()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(mode=0o777)
    cache_dir.chmod(0o777)

    code = module.main(["CACHED_SECRET", "--cache-dir", str(cache_dir)], env={})

    assert code == module.EXIT_CONFIG_MISSING
    assert "class=config_missing" in capsys.readouterr().out


def test_real_script_status_does_not_need_account(monkeypatch):
    monkeypatch.setenv("PATH", "")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 21
    assert "class=cli_missing" in completed.stdout
