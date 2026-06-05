#!/usr/bin/env python3
"""BIF-681 local A/B benchmark: Linear-first cache path vs native Hermes Kanban.

This benchmark is intentionally local and non-destructive. It creates temporary
fixture SQLite stores for both paths, runs paired representative Biff OS
operations, and writes raw JSON plus a Markdown summary artifact.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA = "bif.linear_first_vs_native_kanban_ab.v1"
OPERATIONS = (
    "quick_status_read",
    "backlog_next_work_query",
    "create_update_story_dry_run",
    "comment_evidence_append_dry_run",
    "closeout_refetch",
    "parent_child_lookup",
)
DEFAULT_ITERATIONS = 40
DEFAULT_SEED = 681
MAX_P50_RATIO = 1.5
MAX_P95_RATIO = 2.0
MAX_CONTEXT_TOKEN_RATIO = 1.5


@dataclass
class Measurement:
    operation: str
    path: str
    iteration: int
    wall_ms: float
    prompt_input_bytes: int
    prompt_input_tokens_est: int
    context_bytes: int
    context_tokens_est: int
    tool_output_bytes: int
    tool_output_tokens_est: int
    cache_event: str
    refresh_count: int
    output: dict[str, Any]


class LinearFirstFixture:
    def __init__(self, path: Path):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.refresh_count = 0
        self._setup()

    def _setup(self) -> None:
        self.con.executescript(
            """
            create table linear_source_issues (
              identifier text primary key,
              title text not null,
              description text,
              state_type text not null,
              priority integer not null default 0,
              owner_role text,
              parent_identifier text,
              source_updated_at integer not null,
              raw_json text not null
            );
            create table linear_cache_issues (
              identifier text primary key,
              title text not null,
              description text,
              state_type text not null,
              priority integer not null default 0,
              owner_role text,
              parent_identifier text,
              source_updated_at integer not null,
              synced_at integer not null,
              raw_json text not null,
              stale_reason text
            );
            create table linear_comments (
              id integer primary key autoincrement,
              issue_identifier text not null,
              body text not null,
              created_at integer not null
            );
            create index idx_linear_cache_state_priority on linear_cache_issues(state_type, priority desc, source_updated_at desc);
            create index idx_linear_cache_parent on linear_cache_issues(parent_identifier);
            create index idx_linear_comments_issue_created on linear_comments(issue_identifier, created_at desc);
            """
        )
        now = int(time.time())
        source_rows = []
        for i in range(1, 81):
            identifier = f"BIF-{680 + i}"
            state = ["backlog", "unstarted", "started", "completed"][i % 4]
            parent = "BIF-681" if 2 <= i <= 9 else None
            raw = {
                "identifier": identifier,
                "title": f"Biff OS representative story {i}",
                "description": "Fixture story used for BIF-681 local Linear-first benchmarking.",
                "state": {"type": state},
                "priority": (i % 5) + 1,
                "parent": {"identifier": parent} if parent else None,
                "url": f"https://linear.app/fixture/issue/{identifier}",
            }
            source_rows.append((identifier, raw["title"], raw["description"], state, raw["priority"], "Forge", parent, now - i, json.dumps(raw, sort_keys=True)))
        self.con.executemany(
            "insert into linear_source_issues values (?,?,?,?,?,?,?,?,?)",
            source_rows,
        )
        # Pre-warm most, but not all, rows so the status operation captures both
        # cache hit and refresh behavior without touching live Linear.
        self.con.execute(
            """
            insert into linear_cache_issues
            select identifier,title,description,state_type,priority,owner_role,parent_identifier,source_updated_at,?,raw_json,null
            from linear_source_issues where identifier != 'BIF-681'
            """,
            (now,),
        )
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def _refresh_from_source(self, identifier: str) -> dict[str, Any] | None:
        row = self.con.execute("select * from linear_source_issues where identifier=?", (identifier,)).fetchone()
        if not row:
            return None
        self.refresh_count += 1
        now = int(time.time())
        self.con.execute(
            """
            insert into linear_cache_issues(identifier,title,description,state_type,priority,owner_role,parent_identifier,source_updated_at,synced_at,raw_json,stale_reason)
            values (?,?,?,?,?,?,?,?,?,?,null)
            on conflict(identifier) do update set
              title=excluded.title, description=excluded.description, state_type=excluded.state_type,
              priority=excluded.priority, owner_role=excluded.owner_role, parent_identifier=excluded.parent_identifier,
              source_updated_at=excluded.source_updated_at, synced_at=excluded.synced_at,
              raw_json=excluded.raw_json, stale_reason=null
            """,
            (row["identifier"], row["title"], row["description"], row["state_type"], row["priority"], row["owner_role"], row["parent_identifier"], row["source_updated_at"], now, row["raw_json"]),
        )
        self.con.commit()
        refreshed = self.con.execute("select * from linear_cache_issues where identifier=?", (identifier,)).fetchone()
        return dict(refreshed) if refreshed else None

    def quick_status_read(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        before = self.refresh_count
        row = self.con.execute("select * from linear_cache_issues where identifier=?", ("BIF-681",)).fetchone()
        cache_event = "hit"
        if row is None:
            cache_event = "miss_refresh"
            issue = self._refresh_from_source("BIF-681")
        else:
            issue = dict(row)
        if issue is None:
            raise RuntimeError("fixture missing BIF-681")
        return {"identifier": issue["identifier"], "title": issue["title"], "state_type": issue["state_type"], "source": "linear_cache"}, cache_event, self.refresh_count - before

    def backlog_next_work_query(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        rows = self.con.execute(
            """
            select identifier,title,state_type,priority,owner_role from linear_cache_issues
            where state_type in ('triage','backlog','unstarted','started')
            order by priority desc, source_updated_at desc limit 8
            """
        ).fetchall()
        return {"source": "linear_cache", "next": [dict(row) for row in rows]}, "hit", 0

    def create_update_story_dry_run(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        identifier = f"BIF-FIXTURE-{iteration:03d}"
        now = int(time.time())
        raw = {"identifier": identifier, "title": "dry-run created fixture", "state": {"type": "started"}, "dry_run": True}
        with self.con:
            self.con.execute(
                "insert into linear_source_issues values (?,?,?,?,?,?,?,?,?)",
                (identifier, "dry-run created fixture", "safe local fixture", "backlog", 3, "Forge", None, now, json.dumps(raw, sort_keys=True)),
            )
            self.con.execute(
                "insert into linear_comments(issue_identifier, body, created_at) values (?,?,?)",
                (identifier, "DRY-RUN create/update fixture; not sent to Linear", now),
            )
            self.con.execute("update linear_source_issues set state_type='started', source_updated_at=? where identifier=?", (now + 1, identifier))
        issue = self._refresh_from_source(identifier)
        if issue is None:
            raise RuntimeError(f"fixture refresh failed for {identifier}")
        return {"identifier": identifier, "state_type": issue["state_type"], "dry_run": True, "source": "linear_source_then_cache"}, "write_refresh", 1

    def comment_evidence_append_dry_run(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        now = int(time.time())
        body = f"BIF-681 dry-run evidence append sample {iteration}; local fixture only."
        with self.con:
            self.con.execute("insert into linear_comments(issue_identifier, body, created_at) values (?,?,?)", ("BIF-681", body, now))
        issue = self._refresh_from_source("BIF-681")
        if issue is None:
            raise RuntimeError("fixture missing BIF-681")
        comments = self.con.execute("select count(*) as n from linear_comments where issue_identifier=?", ("BIF-681",)).fetchone()["n"]
        return {"identifier": "BIF-681", "comments": comments, "state_type": issue["state_type"], "dry_run": True}, "write_refresh", 1

    def closeout_refetch(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        identifier = f"BIF-CLOSE-{iteration:03d}"
        now = int(time.time())
        raw = {"identifier": identifier, "title": "closeout fixture", "state": {"type": "started"}, "dry_run": True}
        with self.con:
            self.con.execute("insert into linear_source_issues values (?,?,?,?,?,?,?,?,?)", (identifier, "closeout fixture", "safe closeout fixture", "started", 2, "Vex", None, now, json.dumps(raw, sort_keys=True)))
            self.con.execute("insert into linear_comments(issue_identifier, body, created_at) values (?,?,?)", (identifier, "Vex PASS fixture evidence", now + 1))
            self.con.execute("update linear_source_issues set state_type='completed', source_updated_at=? where identifier=?", (now + 2, identifier))
        issue = self._refresh_from_source(identifier)
        if issue is None:
            raise RuntimeError(f"fixture refresh failed for {identifier}")
        return {"identifier": identifier, "state_type": issue["state_type"], "refetched": True, "dry_run": True}, "write_refresh", 1

    def parent_child_lookup(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        parent = self.con.execute("select identifier,title from linear_cache_issues where identifier=?", ("BIF-681",)).fetchone()
        children = self.con.execute("select identifier,title,state_type from linear_cache_issues where parent_identifier=? order by identifier", ("BIF-681",)).fetchall()
        if parent is None:
            raise RuntimeError("fixture missing BIF-681 parent")
        return {"parent": dict(parent), "children": [dict(row) for row in children], "source": "linear_cache"}, "hit", 0


class KanbanFixture:
    def __init__(self, path: Path):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self._setup()

    def _setup(self) -> None:
        self.con.executescript(
            """
            create table tasks(
              id text primary key,
              title text not null,
              body text,
              assignee text,
              status text not null,
              priority integer default 0,
              created_by text,
              created_at integer not null,
              started_at integer,
              completed_at integer,
              workspace_kind text default 'scratch',
              workspace_path text,
              tenant text,
              result text,
              display_id text
            );
            create table task_links(parent_id text not null, child_id text not null, primary key(parent_id, child_id));
            create table task_comments(id integer primary key autoincrement, task_id text not null, author text not null, body text not null, created_at integer not null);
            create index idx_tasks_status_priority on tasks(status, priority desc, created_at desc);
            create index idx_task_links_child on task_links(child_id);
            create index idx_task_comments_task_created on task_comments(task_id, created_at desc);
            """
        )
        now = int(time.time())
        rows = []
        for i in range(1, 81):
            tid = f"t_fixture_{i:03d}"
            display = f"K-{680 + i}"
            status = ["todo", "ready", "running", "done"][i % 4]
            rows.append((tid, f"Biff OS representative story {i}", "Fixture story used for BIF-681 native Kanban benchmarking.", "forge", status, (i % 5) + 1, "fixture", now - i, None, now if status == "done" else None, "scratch", None, "biff-os", None, display))
        self.con.executemany("insert into tasks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        # K-681 equivalent is task 1.
        for i in range(2, 10):
            self.con.execute("insert into task_links(parent_id, child_id) values (?,?)", ("t_fixture_001", f"t_fixture_{i:03d}"))
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def quick_status_read(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        row = self.con.execute("select id,display_id,title,status,assignee,priority from tasks where display_id=?", ("K-681",)).fetchone()
        return {"identifier": row["display_id"], "title": row["title"], "status": row["status"], "source": "native_kanban"}, "n/a", 0

    def backlog_next_work_query(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        rows = self.con.execute(
            """
            select id,display_id,title,status,priority,assignee from tasks
            where status in ('triage','todo','ready','running')
            order by priority desc, created_at desc limit 8
            """
        ).fetchall()
        return {"source": "native_kanban", "next": [dict(row) for row in rows]}, "n/a", 0

    def create_update_story_dry_run(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        tid = f"t_dry_{iteration:03d}"
        display = f"K-DRY-{iteration:03d}"
        now = int(time.time())
        with self.con:
            self.con.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tid, "dry-run created fixture", "safe local fixture", "forge", "todo", 3, "fixture", now, None, None, "scratch", None, "biff-os", None, display))
            self.con.execute("update tasks set status='running', started_at=? where id=?", (now + 1, tid))
        row = self.con.execute("select display_id,status from tasks where id=?", (tid,)).fetchone()
        return {"identifier": row["display_id"], "status": row["status"], "dry_run": True, "source": "native_kanban"}, "n/a", 0

    def comment_evidence_append_dry_run(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        now = int(time.time())
        with self.con:
            self.con.execute("insert into task_comments(task_id, author, body, created_at) values (?,?,?,?)", ("t_fixture_001", "forge", f"BIF-681 dry-run evidence append sample {iteration}; local fixture only.", now))
        count = self.con.execute("select count(*) as n from task_comments where task_id=?", ("t_fixture_001",)).fetchone()["n"]
        return {"identifier": "K-681", "comments": count, "dry_run": True, "source": "native_kanban"}, "n/a", 0

    def closeout_refetch(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        tid = f"t_close_{iteration:03d}"
        display = f"K-CLOSE-{iteration:03d}"
        now = int(time.time())
        with self.con:
            self.con.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tid, "closeout fixture", "safe closeout fixture", "vex", "running", 2, "fixture", now, now, None, "scratch", None, "biff-os", None, display))
            self.con.execute("insert into task_comments(task_id, author, body, created_at) values (?,?,?,?)", (tid, "vex", "Vex PASS fixture evidence", now + 1))
            self.con.execute("update tasks set status='done', completed_at=?, result=? where id=?", (now + 2, "closed with Vex fixture evidence", tid))
        row = self.con.execute("select display_id,status,result from tasks where id=?", (tid,)).fetchone()
        return {"identifier": row["display_id"], "status": row["status"], "refetched": True, "dry_run": True}, "n/a", 0

    def parent_child_lookup(self, iteration: int) -> tuple[dict[str, Any], str, int]:
        parent = self.con.execute("select id,display_id,title from tasks where id=?", ("t_fixture_001",)).fetchone()
        children = self.con.execute(
            """
            select t.id,t.display_id,t.title,t.status from task_links l join tasks t on t.id=l.child_id
            where l.parent_id=? order by t.display_id
            """,
            ("t_fixture_001",),
        ).fetchall()
        return {"parent": dict(parent), "children": [dict(row) for row in children], "source": "native_kanban"}, "n/a", 0


def estimate_tokens(text: str) -> int:
    # Conservative local estimate that avoids model/API dependency. It captures
    # relative context pressure for equivalent payloads.
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_prompt(operation: str, path_name: str) -> str:
    return (
        "You are Biff operating Biff OS work. Use the supplied local ledger context. "
        f"Operation={operation}. Path={path_name}. Return concise status, evidence, and blockers only."
    )


def build_context(operation: str, path_name: str, output: dict[str, Any], cache_event: str, refresh_count: int) -> str:
    payload = {
        "operation": operation,
        "ledger_path": path_name,
        "cache_event": cache_event,
        "refresh_count": refresh_count,
        "result": output,
    }
    return compact_json(payload)


def measure(path_name: str, fixture: Any, operation: str, iteration: int) -> Measurement:
    fn: Callable[[int], tuple[dict[str, Any], str, int]] = getattr(fixture, operation)
    start = time.perf_counter_ns()
    output, cache_event, refresh_count = fn(iteration)
    wall_ms = (time.perf_counter_ns() - start) / 1_000_000
    prompt = build_prompt(operation, path_name)
    context = build_context(operation, path_name, output, cache_event, refresh_count)
    tool_output = compact_json(output)
    return Measurement(
        operation=operation,
        path=path_name,
        iteration=iteration,
        wall_ms=wall_ms,
        prompt_input_bytes=len((prompt + context).encode("utf-8")),
        prompt_input_tokens_est=estimate_tokens(prompt + context),
        context_bytes=len(context.encode("utf-8")),
        context_tokens_est=estimate_tokens(context),
        tool_output_bytes=len(tool_output.encode("utf-8")),
        tool_output_tokens_est=estimate_tokens(tool_output),
        cache_event=cache_event,
        refresh_count=refresh_count,
        output=output,
    )


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def summarize(measurements: list[Measurement]) -> dict[str, Any]:
    by_path_op: dict[tuple[str, str], list[Measurement]] = {}
    for m in measurements:
        by_path_op.setdefault((m.path, m.operation), []).append(m)

    operations: dict[str, Any] = {}
    all_linear: list[float] = []
    all_kanban: list[float] = []
    all_linear_tokens: list[int] = []
    all_kanban_tokens: list[int] = []
    for operation in OPERATIONS:
        linear = by_path_op[("linear_first_cache", operation)]
        kanban = by_path_op[("native_kanban", operation)]
        linear_ms = [m.wall_ms for m in linear]
        kanban_ms = [m.wall_ms for m in kanban]
        linear_tokens = [m.prompt_input_tokens_est for m in linear]
        kanban_tokens = [m.prompt_input_tokens_est for m in kanban]
        all_linear.extend(linear_ms)
        all_kanban.extend(kanban_ms)
        all_linear_tokens.extend(linear_tokens)
        all_kanban_tokens.extend(kanban_tokens)
        operations[operation] = {
            "linear_first_cache": stats(linear_ms),
            "native_kanban": stats(kanban_ms),
            "wall_ms_ratio_p50": safe_ratio(statistics.median(linear_ms), statistics.median(kanban_ms)),
            "wall_ms_ratio_p95": safe_ratio(percentile(linear_ms, 95), percentile(kanban_ms, 95)),
            "prompt_input_tokens_est": {
                "linear_first_cache_p50": statistics.median(linear_tokens),
                "native_kanban_p50": statistics.median(kanban_tokens),
                "p50_ratio": safe_ratio(statistics.median(linear_tokens), statistics.median(kanban_tokens)),
            },
            "tool_output_bytes_p50": {
                "linear_first_cache": statistics.median([m.tool_output_bytes for m in linear]),
                "native_kanban": statistics.median([m.tool_output_bytes for m in kanban]),
            },
            "cache_events": count_events(m.cache_event for m in linear),
            "refresh_count_total": sum(m.refresh_count for m in linear),
        }
    overall = {
        "linear_first_cache_wall_ms": stats(all_linear),
        "native_kanban_wall_ms": stats(all_kanban),
        "wall_ms_p50_ratio": safe_ratio(statistics.median(all_linear), statistics.median(all_kanban)),
        "wall_ms_p95_ratio": safe_ratio(percentile(all_linear, 95), percentile(all_kanban, 95)),
        "prompt_input_tokens_est_p50_ratio": safe_ratio(statistics.median(all_linear_tokens), statistics.median(all_kanban_tokens)),
    }
    passed = (
        overall["wall_ms_p50_ratio"] <= MAX_P50_RATIO
        and overall["wall_ms_p95_ratio"] <= MAX_P95_RATIO
        and overall["prompt_input_tokens_est_p50_ratio"] <= MAX_CONTEXT_TOKEN_RATIO
    )
    return {
        "thresholds": {
            "max_wall_ms_p50_ratio": MAX_P50_RATIO,
            "max_wall_ms_p95_ratio": MAX_P95_RATIO,
            "max_prompt_input_tokens_est_p50_ratio": MAX_CONTEXT_TOKEN_RATIO,
        },
        "overall": overall,
        "operations": operations,
        "passed": passed,
        "recommendation": "ready_for_vex" if passed else "do_not_cut_over_keep_native_kanban",
    }


def stats(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "p50": statistics.median(values),
        "p95": percentile(values, 95),
        "max": max(values),
        "samples": len(values),
    }


def safe_ratio(a: float, b: float) -> float:
    return float("inf") if b == 0 else a / b


def count_events(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    overall = summary["overall"]
    lines = [
        "# BIF-681 Linear-first vs native Hermes Kanban local A/B benchmark",
        "",
        f"Schema: `{payload['schema']}`",
        f"Iterations per operation/path: {payload['config']['iterations']}",
        f"Recommendation: **{summary['recommendation']}**",
        f"Passed thresholds: **{summary['passed']}**",
        "",
        "## Overall",
        "",
        "| Metric | Linear-first/cache | Native Kanban | Ratio |",
        "|---|---:|---:|---:|",
        f"| wall p50 ms | {overall['linear_first_cache_wall_ms']['p50']:.3f} | {overall['native_kanban_wall_ms']['p50']:.3f} | {overall['wall_ms_p50_ratio']:.2f} |",
        f"| wall p95 ms | {overall['linear_first_cache_wall_ms']['p95']:.3f} | {overall['native_kanban_wall_ms']['p95']:.3f} | {overall['wall_ms_p95_ratio']:.2f} |",
        f"| prompt/input token estimate p50 | — | — | {overall['prompt_input_tokens_est_p50_ratio']:.2f} |",
        "",
        "## Operations",
        "",
        "| Operation | Linear p50/p95 ms | Kanban p50/p95 ms | p50 ratio | p95 ratio | Linear cache events | Refreshes |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]
    for op, row in summary["operations"].items():
        le = row["linear_first_cache"]
        ka = row["native_kanban"]
        lines.append(
            f"| {op} | {le['p50']:.3f}/{le['p95']:.3f} | {ka['p50']:.3f}/{ka['p95']:.3f} | "
            f"{row['wall_ms_ratio_p50']:.2f} | {row['wall_ms_ratio_p95']:.2f} | "
            f"{row['cache_events']} | {row['refresh_count_total']} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- All writes are safe local fixture writes in temporary SQLite stores; no live Linear mutation, Kanban cleanup, cutover, or gateway restart was performed.",
            "- Token counts are deterministic local estimates (UTF-8 bytes / 4, rounded up) intended to compare relative prompt/context/tool-output pressure without making model API calls.",
            "- The Linear-first path includes cache-hit and cache-miss refresh behavior against a local source fixture that stands in for the Linear API; raw samples are in the JSON artifact.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(output_dir: Path, iterations: int, seed: int) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    measurements: list[Measurement] = []
    with tempfile.TemporaryDirectory(prefix="bif681-ab-") as tmp:
        tmpdir = Path(tmp)
        linear = LinearFirstFixture(tmpdir / "linear_cache.sqlite")
        kanban = KanbanFixture(tmpdir / "kanban.sqlite")
        try:
            # Warm SQLite page cache similarly for both ledgers.
            linear.backlog_next_work_query(-1)
            kanban.backlog_next_work_query(-1)
            for iteration in range(iterations):
                for operation in OPERATIONS:
                    order = ["linear", "kanban"]
                    rng.shuffle(order)
                    for target in order:
                        if target == "linear":
                            measurements.append(measure("linear_first_cache", linear, operation, iteration))
                        else:
                            measurements.append(measure("native_kanban", kanban, operation, iteration))
        finally:
            linear.close()
            kanban.close()
    payload = {
        "schema": SCHEMA,
        "config": {
            "iterations": iterations,
            "seed": seed,
            "operations": list(OPERATIONS),
            "runtime_conditions": "single process, paired randomized order, temporary local SQLite fixture stores, no network, no live mutations",
        },
        "summary": summarize(measurements),
        "raw_measurements": [m.__dict__ for m in measurements],
    }
    json_path = output_dir / "bif-681-linear-first-vs-native-kanban-ab.json"
    md_path = output_dir / "bif-681-linear-first-vs-native-kanban-ab.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(md_path, payload)
    return {"json": json_path, "markdown": md_path}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="evaluations/bif-681", help="artifact output directory")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    paths = run(Path(args.output_dir), args.iterations, args.seed)
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
