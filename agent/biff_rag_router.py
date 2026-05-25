"""Latency-budgeted SecondBrain RAG policy for Biff live Discord turns.

This module is intentionally small and read-only.  It decides whether a live
Discord prompt should skip retrieval, do one bounded SQLite FTS lookup, or move
broad retrieval to background work.  Smart Connections is treated as optional
provenance only: if status is unavailable, locked, dataless, or slow/erroring,
the live path falls back to SQLite/skip instead of blocking Discord.
"""

from __future__ import annotations

import errno
import importlib.util
import multiprocessing as mp
import queue
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

DEFAULT_SECOND_BRAIN_DB = Path.home() / ".hermes" / "indexes" / "secondbrain.sqlite"
SECOND_BRAIN_INDEXER = Path.home() / ".hermes" / "scripts" / "secondbrain_sqlite_index.py"
DEFAULT_RAG_LATENCY_MS = 750
DEFAULT_SMART_STATUS_TIMEOUT_MS = 200
DEFAULT_RAG_TOP_N = 3
DEFAULT_RAG_CACHE_TTL_SECONDS = 30
MAX_QUERY_TERMS = 8
MAX_SNIPPET_CHARS = 240
_RAG_CONTEXT_CACHE: dict[tuple[str, str, int, int], tuple[float, str]] = {}

_CASUAL_RE = re.compile(
    r"^\s*(?:hi|hello|hey|yo|sup|thanks|thank you|ok|okay|👍|🙏|lol|nice|cool|gm|gn)[.!?\s]*$",
    re.IGNORECASE,
)
_DIRECT_SKIP_RE = re.compile(
    r"\b(?:dinner ideas|recipe|what time|weather|joke|explain|define|translate|draft a|write a quick|brainstorm)\b",
    re.IGNORECASE,
)
_SECOND_BRAIN_RE = re.compile(
    r"\b(?:second\s*brain|secondbrain|obsidian|vault|notes?|decision(?:s| log)?|memory|memories|mnemosyne|smart connections|smart)\b",
    re.IGNORECASE,
)
_LOOKUP_RE = re.compile(
    r"\b(?:look\s*up|search|find|recall|remember|what did we|where did|show me|pull|retrieve|check)\b",
    re.IGNORECASE,
)
_BROAD_RE = re.compile(
    r"\b(?:all|every|entire|whole|full|deep|comprehensive|exhaustive|history|archive|across)\b.*\b(?:vault|obsidian|second\s*brain|secondbrain|notes?|history|decisions?|workspace|smart connections)\b"
    r"|\b(?:scan|search|audit|inspect|summari[sz]e)\b.*\b(?:all|every|entire|whole|full|deep|comprehensive|exhaustive)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "a", "an", "and", "are", "about", "can", "could", "find", "for", "from", "give", "have",
    "how", "into", "look", "me", "my", "of", "on", "or", "please", "pull", "search", "show", "smart",
    "tell", "the", "to", "up", "use", "what", "where", "with", "you", "your",
}
_SECRET_RE = re.compile(r"(?i)(api[_ -]?key|password|passwd|private[_ -]?key|secret|token|credential)\s*[:=]\s*\S+")


@dataclass(frozen=True)
class BiffRagDecision:
    action: str
    reason: str
    max_latency_ms: int
    max_live_tool_calls: int
    top_n: int = 0
    query: str = ""
    smart_allowed: bool = False
    background: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "max_latency_ms": self.max_latency_ms,
            "max_live_tool_calls": self.max_live_tool_calls,
            "top_n": self.top_n,
            "query": self.query,
            "smart_allowed": self.smart_allowed,
            "background": self.background,
        }


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def shape_fts_query(text: Any, *, max_terms: int = MAX_QUERY_TERMS) -> str:
    """Return a small FTS5 query made of quoted terms joined by OR."""

    body = _compact_text(text).lower()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", body):
        token = token.strip("_-")
        if not token or token in _STOPWORDS or token in terms:
            continue
        terms.append(token)
        if len(terms) >= max(1, int(max_terms)):
            break
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)


def _load_indexer_module() -> Any | None:
    if not SECOND_BRAIN_INDEXER.exists():
        return None
    spec = importlib.util.spec_from_file_location("_biff_secondbrain_sqlite_index", SECOND_BRAIN_INDEXER)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_with_hard_timeout(fn: Callable[[], Any], *, timeout_ms: int) -> tuple[bool, Any]:
    """Run a potentially blocking Smart helper behind a hard live-path guard.

    Prefer a short-lived child process so a stuck iCloud/FileProvider read can be
    terminated instead of leaving a daemon thread blocked after the live Discord
    turn has already fallen back.  When ``fork`` is unavailable (Windows), fall
    back to the previous daemon-thread guard; it still protects caller latency.
    """

    budget_ms = max(1, int(timeout_ms or DEFAULT_SMART_STATUS_TIMEOUT_MS))
    timeout_s = budget_ms / 1000
    if "fork" in mp.get_all_start_methods():
        ctx = mp.get_context("fork")
        proc_results: Any = ctx.Queue(maxsize=1)

        def runner() -> None:
            try:
                proc_results.put(("ok", fn()))
            except BaseException as exc:
                proc_results.put(("error", exc))

        proc = ctx.Process(target=runner, name="biff-smart-status-timeout")
        proc.start()
        proc.join(timeout_s)
        if proc.is_alive():
            proc.terminate()
            proc.join(0.1)
            if proc.is_alive():
                proc.kill()
                proc.join(0.1)
            return False, None
        try:
            kind, value = proc_results.get_nowait()
        except queue.Empty:
            return False, None
        if kind == "error":
            raise value
        return True, value

    results: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def thread_runner() -> None:
        try:
            results.put_nowait(("ok", fn()))
        except BaseException as exc:  # surfaced to caller when it happens inside budget
            results.put_nowait(("error", exc))

    worker = threading.Thread(target=thread_runner, name="biff-smart-status-timeout", daemon=True)
    worker.start()
    try:
        kind, value = results.get(timeout=timeout_s)
    except queue.Empty:
        return False, None
    if kind == "error":
        raise value
    return True, value


def smart_connections_status(*, timeout_ms: int = DEFAULT_SMART_STATUS_TIMEOUT_MS) -> dict[str, Any]:
    """Read Smart Connections status with a tiny live-path hard timeout."""

    budget_ms = max(1, int(timeout_ms or DEFAULT_SMART_STATUS_TIMEOUT_MS))
    started = time.monotonic()

    def read_status() -> Any:
        module = _load_indexer_module()
        if module is None or not hasattr(module, "smart_connections_status"):
            return {"state": "unavailable", "reason": "indexer_status_missing"}
        return module.smart_connections_status(sample_limit=3)

    completed, status = _call_with_hard_timeout(read_status, timeout_ms=budget_ms)
    if not completed:
        return {"state": "unavailable", "reason": "timed_out", "timeout_ms": budget_ms}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if isinstance(status, Mapping):
        out = dict(status)
        out["elapsed_ms"] = elapsed_ms
        return out
    return {"state": "unavailable", "reason": "invalid_status", "elapsed_ms": elapsed_ms}


def _status_or_unavailable() -> tuple[dict[str, Any], str | None]:
    try:
        return smart_connections_status(), None
    except OSError as exc:
        code = getattr(exc, "errno", None)
        if code == errno.EAGAIN:
            return {"state": "unavailable", "reason": "errno 11 FileProvider locked/dataless"}, "errno 11 FileProvider locked/dataless"
        return {"state": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return {"state": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}, f"{type(exc).__name__}: {exc}"


def smart_status_is_available(status: Mapping[str, Any] | None) -> bool:
    status = status if isinstance(status, Mapping) else {}
    return (
        str(status.get("state") or "").strip().lower() == "available"
        and int(status.get("files") or 0) > 0
        and int(status.get("readable_samples") or 0) > 0
    )


def classify_biff_rag_request(text: Any, *, check_smart_status: bool = True) -> BiffRagDecision:
    body = _compact_text(text)
    if not body or _CASUAL_RE.search(body) or (_DIRECT_SKIP_RE.search(body) and not _SECOND_BRAIN_RE.search(body)):
        return BiffRagDecision("skip", "casual/direct prompt", 0, 0)
    if _BROAD_RE.search(body):
        return BiffRagDecision(
            "background",
            "broad/slow SecondBrain retrieval should create a continuation/background handle",
            0,
            1,
            background=True,
        )
    if _SECOND_BRAIN_RE.search(body) and (_LOOKUP_RE.search(body) or "?" in body or "smart connections" in body.lower()):
        status = {"state": "unavailable", "reason": "status_check_skipped"}
        if check_smart_status:
            status, _ = _status_or_unavailable()
        smart_allowed = smart_status_is_available(status)
        return BiffRagDecision(
            "sqlite_fts",
            "explicit bounded SecondBrain lookup",
            DEFAULT_RAG_LATENCY_MS,
            1,
            top_n=DEFAULT_RAG_TOP_N,
            query=shape_fts_query(body),
            smart_allowed=smart_allowed,
        )
    return BiffRagDecision("skip", "no high-confidence retrieval need", 0, 0)


def _clip(value: Any, limit: int) -> str:
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[redacted]", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 18)].rstrip() + " ... [trimmed]"


def _source_attribution(row: Mapping[str, Any]) -> str:
    source = str(row.get("source") or "SecondBrain SQLite FTS").strip()
    path = str(row.get("path") or "").strip()
    title = str(row.get("title") or "").strip()
    label = path or title or "unknown note"
    return _clip(f"{source}: {label}", 180)


def merge_secondbrain_results(
    *result_groups: Sequence[Mapping[str, Any]],
    limit: int = DEFAULT_RAG_TOP_N,
) -> list[dict[str, Any]]:
    """Merge retrieval result groups with stable dedupe and source attribution."""

    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group in result_groups:
        for raw in group or []:
            if not isinstance(raw, Mapping):
                continue
            row = {
                "path": _clip(raw.get("path", ""), 120),
                "title": _clip(raw.get("title", ""), 100),
                "snippet": _clip(raw.get("snippet", ""), MAX_SNIPPET_CHARS),
                "score": float(raw.get("score") or 0.0),
                "source": _clip(raw.get("source") or "SecondBrain SQLite FTS", 80),
            }
            key = (
                row["path"].casefold(),
                row["title"].casefold(),
                row["snippet"].casefold(),
            )
            row["attribution"] = _source_attribution(row)
            existing = merged.get(key)
            if existing is None or row["score"] < float(existing.get("score") or 0.0):
                merged[key] = row
    rows = sorted(merged.values(), key=lambda item: (float(item.get("score") or 0.0), str(item.get("path") or "")))
    return rows[: max(1, min(5, int(limit or DEFAULT_RAG_TOP_N)))]


def _db_cache_fingerprint(db_path: str | Path) -> tuple[str, int]:
    db = Path(db_path).expanduser()
    try:
        return str(db), int(db.stat().st_mtime_ns)
    except OSError:
        return str(db), 0


def query_secondbrain_fts(
    db_path: str | Path = DEFAULT_SECOND_BRAIN_DB,
    query: Any = "",
    *,
    limit: int = DEFAULT_RAG_TOP_N,
) -> list[dict[str, Any]]:
    """Run a read-only, query-shaped SQLite FTS lookup returning top snippets only."""

    db = Path(db_path).expanduser()
    fts_query = shape_fts_query(query)
    if not db.exists() or not fts_query:
        return []
    top_n = max(1, min(5, int(limit or DEFAULT_RAG_TOP_N)))
    try:
        uri = f"file:{db}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.2) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT n.path,
                       n.title,
                       snippet(notes_fts, 2, '[', ']', ' … ', 16) AS snippet,
                       bm25(notes_fts) AS score
                FROM notes_fts
                JOIN notes n ON n.rowid = notes_fts.rowid
                WHERE notes_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (fts_query, top_n),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return []
    out: list[dict[str, Any]] = []
    for row in rows[:top_n]:
        out.append(
            {
                "path": _clip(row["path"], 120),
                "title": _clip(row["title"], 100),
                "snippet": _clip(row["snippet"], MAX_SNIPPET_CHARS),
                "score": float(row["score"]),
                "source": "SecondBrain SQLite FTS",
                "attribution": _clip(f"SecondBrain SQLite FTS: {row['path']}", 180),
            }
        )
    return out


def secondbrain_rag_context(
    text: Any,
    *,
    db_path: str | Path = DEFAULT_SECOND_BRAIN_DB,
    max_chars: int = 1200,
) -> str:
    """Return a compact optional context block for explicit SecondBrain lookup prompts."""

    decision = classify_biff_rag_request(text, check_smart_status=False)
    if decision.action != "sqlite_fts":
        return ""
    db_key, db_mtime = _db_cache_fingerprint(db_path)
    cache_key = (db_key, decision.query or shape_fts_query(text), int(max_chars), db_mtime)
    now = time.monotonic()
    cached = _RAG_CONTEXT_CACHE.get(cache_key)
    if cached and now - cached[0] <= DEFAULT_RAG_CACHE_TTL_SECONDS:
        return cached[1]
    status, status_error = _status_or_unavailable()
    rows = merge_secondbrain_results(
        query_secondbrain_fts(db_path, decision.query or text, limit=decision.top_n),
        limit=decision.top_n,
    )
    if not rows and not status_error:
        return ""
    lines = [
        "## Biff SecondBrain RAG Context",
        f"Policy: {decision.max_latency_ms}ms live budget, {decision.max_live_tool_calls} lookup tool-call budget, top {decision.top_n} SQLite FTS snippets only; no vault mutation.",
    ]
    if smart_status_is_available(status):
        lines.append("- Smart Connections status: available for provenance only; SQLite snippets remain the live lookup source.")
    else:
        reason = status_error or str(status.get("reason") or status.get("state") or "unavailable")
        lines.append(f"- Smart Connections skipped: {reason}.")
    for row in rows:
        lines.append(f"- [{row['attribution']}] {row['title']}: {row['snippet']}")
    context = _clip("\n".join(lines), max_chars)
    _RAG_CONTEXT_CACHE[cache_key] = (now, context)
    return context
