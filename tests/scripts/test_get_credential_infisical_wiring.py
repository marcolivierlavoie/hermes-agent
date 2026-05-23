from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap


HELPER = Path("/Users/marco/.local/bin/get_credential.sh")


def write_fake_lookup(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_infisical_lookup.py"
    script.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    script.chmod(0o700)
    return script


def run_helper(args: list[str], *, cache_dir: Path, lookup: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HELPER), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "CREDENTIAL_CACHE_DIR": str(cache_dir),
            "INFISICAL_LOOKUP": str(lookup),
            # Do not let a developer shell token influence these wiring tests.
            "INFISICAL_TOKEN": "",
            "INFISICAL_PROJECT_ID": "",
            "INFISICAL_WORKSPACE_ID": "",
        },
    )


def test_migrated_key_uses_infisical_before_cache(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "n8n_api_key.secret").write_text("from-cache", encoding="utf-8")
    lookup = write_fake_lookup(
        tmp_path,
        """
        import sys
        sys.stdout.write('from-infisical')
        sys.exit(0)
        """,
    )

    result = run_helper(["n8n_api_key"], cache_dir=cache_dir, lookup=lookup)

    assert result.returncode == 0
    assert result.stdout == "from-infisical"
    assert result.stderr == ""


def test_allowlisted_key_falls_through_to_infisical_before_1password(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    invoked = tmp_path / "invoked.txt"
    lookup = write_fake_lookup(
        tmp_path,
        f"""
        from pathlib import Path
        import sys
        Path({str(invoked)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')
        assert '--emit-secret-value' in sys.argv
        assert 'n8n_api_key' in sys.argv
        sys.stdout.write('from-infisical')
        sys.exit(0)
        """,
    )

    result = run_helper(["n8n_api_key"], cache_dir=cache_dir, lookup=lookup)

    assert result.returncode == 0
    assert result.stdout == "from-infisical"
    assert invoked.exists()
    assert result.stderr == ""


def test_check_mode_reports_infisical_source_without_secret_value(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    lookup = write_fake_lookup(
        tmp_path,
        """
        import sys
        if '--emit-secret-value' in sys.argv:
            sys.stdout.write('secret-that-must-not-appear')
        sys.exit(0)
        """,
    )

    result = run_helper(["--check", "govee_api_key"], cache_dir=cache_dir, lookup=lookup)

    assert result.returncode == 0
    assert result.stdout.strip() == "govee_api_key: available source=infisical"
    assert "secret-that-must-not-appear" not in result.stdout
    assert result.stderr == ""


def test_newly_migrated_keys_report_infisical_source_without_secret_value(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    lookup = write_fake_lookup(
        tmp_path,
        """
        import sys
        if '--emit-secret-value' in sys.argv:
            sys.stdout.write('secret-that-must-not-appear')
        sys.exit(0)
        """,
    )

    for key in ["linear_api_key", "discord_bot_token", "kuma_username", "kuma_password", "nas_username", "nas_password"]:
        result = run_helper(["--check", key], cache_dir=cache_dir, lookup=lookup)

        assert result.returncode == 0
        assert result.stdout.strip() == f"{key}: available source=infisical"
        assert "secret-that-must-not-appear" not in result.stdout
        assert result.stderr == ""


def test_non_allowlisted_alias_does_not_use_infisical(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    invoked = tmp_path / "invoked.txt"
    lookup = write_fake_lookup(
        tmp_path,
        f"""
        from pathlib import Path
        Path({str(invoked)!r}).write_text('called', encoding='utf-8')
        raise SystemExit(0)
        """,
    )

    result = run_helper(["--check", "openai_api_key"], cache_dir=cache_dir, lookup=lookup)

    assert result.returncode != 0
    assert not invoked.exists()
    assert "source=infisical" not in result.stdout
