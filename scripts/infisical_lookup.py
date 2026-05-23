#!/usr/bin/env python3
"""Safe Infisical pilot lookup adapter for BIF-670.

By default this script never prints secret values. It reports status and byte
counts only, so it is safe to run in logs/CI while validating Infisical wiring.
The credential helper integration uses the explicit --emit-secret-value mode.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Mapping, Sequence

EXIT_OK = 0
EXIT_CACHE = 0
EXIT_CONFIG_MISSING = 20
EXIT_CLI_MISSING = 21
EXIT_NOT_FOUND = 22
EXIT_AUTH_OR_RATE_LIMIT = 23
EXIT_UNEXPECTED = 24

CLASS_OK = "ok"
CLASS_CACHE = "cache"
CLASS_CONFIG_MISSING = "config_missing"
CLASS_CLI_MISSING = "cli_missing"
CLASS_NOT_FOUND = "not_found"
CLASS_AUTH_OR_RATE_LIMIT = "auth_or_rate_limit"
CLASS_UNEXPECTED = "unexpected"

SAFE_SECRET_NAME = re.compile(r"^[A-Za-z0-9_.@:/=-]{1,256}$")
SAFE_CACHE_FILENAME = re.compile(r"^[A-Za-z0-9_.@=-]{1,256}$")


def _redact(text: str, env: Mapping[str, str]) -> str:
    redacted = text
    for key in (
        "INFISICAL_TOKEN",
        "INFISICAL_PROJECT_ID",
        "INFISICAL_WORKSPACE_ID",
        "INFISICAL_API_URL",
    ):
        value = env.get(key)
        if value:
            redacted = redacted.replace(value, "<redacted>")
    redacted = re.sub(r"(?i)(token|authorization)([=: ]+)([^\s,;]+)", r"\1\2<redacted>", redacted)
    return redacted


def _emit(result_class: str, message: str, *, name: str | None = None, bytes_count: int | None = None) -> None:
    fields = [f"class={result_class}"]
    if name:
        fields.append(f"name={name}")
    if bytes_count is not None:
        fields.append(f"bytes={bytes_count}")
    fields.append(f"message={message}")
    print(" ".join(fields))


def _project_id(env: Mapping[str, str]) -> str | None:
    return env.get("INFISICAL_PROJECT_ID") or env.get("INFISICAL_WORKSPACE_ID")


def _cache_lookup(name: str, cache_dir: str | None) -> bytes | None:
    if not cache_dir:
        return None
    if not SAFE_CACHE_FILENAME.fullmatch(name):
        return None
    root = Path(cache_dir).expanduser()
    if not root.is_dir():
        return None
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(f"cache dir must not be group/world accessible: {root}")
    candidate = (root / name).resolve()
    if root.resolve() not in (candidate, *candidate.parents):
        return None
    if not candidate.is_file():
        return None
    file_mode = stat.S_IMODE(candidate.stat().st_mode)
    if file_mode & 0o077:
        raise PermissionError(f"cache file must not be group/world accessible: {candidate}")
    return candidate.read_bytes()


def _classify_failure(output: str) -> tuple[str, int, str]:
    lowered = output.lower()
    if any(term in lowered for term in ("not found", "could not find", "no secret", "does not exist", "404")):
        return CLASS_NOT_FOUND, EXIT_NOT_FOUND, "secret not found"
    if any(
        term in lowered
        for term in (
            "unauthorized",
            "forbidden",
            "malformed",
            "invalid token",
            "access token",
            "permission",
            "rate limit",
            "too many requests",
            "429",
            "401",
            "403",
        )
    ):
        return CLASS_AUTH_OR_RATE_LIMIT, EXIT_AUTH_OR_RATE_LIMIT, "auth, permission, or rate-limit failure"
    return CLASS_UNEXPECTED, EXIT_UNEXPECTED, "infisical command failed"


def _run_infisical(name: str, args: argparse.Namespace, env: Mapping[str, str]) -> tuple[str, int, int, str, bytes]:
    exe = shutil.which("infisical")
    if not exe:
        return CLASS_CLI_MISSING, EXIT_CLI_MISSING, 0, "infisical CLI not found on PATH", b""

    project_id = _project_id(env)
    if not env.get("INFISICAL_TOKEN") or not project_id:
        return (
            CLASS_CONFIG_MISSING,
            EXIT_CONFIG_MISSING,
            0,
            "set INFISICAL_TOKEN and INFISICAL_PROJECT_ID (or INFISICAL_WORKSPACE_ID)",
            b"",
        )

    cmd = [
        exe,
        "secrets",
        "get",
        name,
        "--projectId",
        project_id,
        "--plain",
        "--silent",
        "--log-level",
        "error",
        "--env",
        args.env,
        "--path",
        args.path,
    ]
    run_env = dict(os.environ)
    run_env.update(env)
    run_env.setdefault("INFISICAL_DISABLE_UPDATE_CHECK", "true")

    completed = subprocess.run(
        cmd,
        env=run_env,
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=args.timeout,
        check=False,
    )
    stdout = completed.stdout or b""
    stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace")
    stdout_text = stdout.decode("utf-8", errors="replace")
    if completed.returncode == 0:
        secret = stdout.rstrip(b"\n")
        return CLASS_OK, EXIT_OK, len(secret), "secret retrieved from infisical", secret
    result_class, exit_code, message = _classify_failure(f"{stderr_text}\n{stdout_text}")
    detail = _redact(stderr_text.strip() or stdout_text.strip(), env)
    if detail:
        message = f"{message}: {detail[:300]}"
    return result_class, exit_code, 0, message, b""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BIF-670 safe Infisical lookup adapter")
    parser.add_argument("name", nargs="?", help="single secret name to check/retrieve; value is not printed unless --emit-secret-value is set")
    parser.add_argument("--check", "--status", action="store_true", help="check CLI/config status without retrieving a secret")
    parser.add_argument("--env", default=os.environ.get("INFISICAL_ENV", "dev"), help="Infisical environment slug/name")
    parser.add_argument("--path", default=os.environ.get("INFISICAL_SECRET_PATH", "/"), help="Infisical secret folder path")
    parser.add_argument("--cache-dir", default=os.environ.get("INFISICAL_CACHE_DIR"), help="optional read-only local secure cache directory")
    parser.add_argument("--timeout", type=float, default=15.0, help="infisical CLI timeout in seconds")
    parser.add_argument(
        "--emit-secret-value",
        action="store_true",
        help="emit the secret value to stdout for credential helper integration; never use in logs/CI",
    )
    return parser


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)

    exe = shutil.which("infisical")
    if args.check:
        if not exe:
            _emit(CLASS_CLI_MISSING, "infisical CLI not found on PATH")
            return EXIT_CLI_MISSING
        if not env.get("INFISICAL_TOKEN") or not _project_id(env):
            _emit(CLASS_CONFIG_MISSING, "CLI present; Infisical token/project config missing")
            return EXIT_CONFIG_MISSING
        _emit(CLASS_OK, "CLI present and required env config present")
        return EXIT_OK

    if not args.name:
        parser.error("name is required unless --check/--status is used")
    if not SAFE_SECRET_NAME.fullmatch(args.name):
        _emit(CLASS_CONFIG_MISSING, "unsafe or unsupported secret name format", name=args.name)
        return EXIT_CONFIG_MISSING

    try:
        cached = _cache_lookup(args.name, args.cache_dir)
    except PermissionError as exc:
        _emit(CLASS_CONFIG_MISSING, str(exc), name=args.name)
        return EXIT_CONFIG_MISSING
    if cached is not None:
        if args.emit_secret_value:
            sys.stdout.buffer.write(cached)
            return EXIT_CACHE
        _emit(CLASS_CACHE, "secret found in local secure cache", name=args.name, bytes_count=len(cached))
        return EXIT_CACHE

    result_class, exit_code, bytes_count, message, secret = _run_infisical(args.name, args, env)
    if result_class == CLASS_OK and args.emit_secret_value:
        sys.stdout.buffer.write(secret)
        return exit_code
    _emit(result_class, message, name=args.name, bytes_count=bytes_count if result_class == CLASS_OK else None)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
