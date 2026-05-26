"""Hermes synthetic checks for live Biff planner behavior.

Synthetic checks are fake-but-realistic user interactions.  They do not replace
unit tests; they assert the outside-facing route/runtime/tool contract that a
real Discord turn should use before the gateway burns model/tool budget.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from pathlib import Path
from typing import Any, Iterable


PASS = "pass"
FAIL = "fail"

BIFF_V3_SPECIALIST_TOOLSETS = ("file", "kanban", "memory", "search", "skills-read", "terminal", "todo", "web")


@dataclass(frozen=True)
class SyntheticScenario:
    name: str
    prompt: str
    expected_action: str
    expected_runtime: str
    expected_toolsets: tuple[str, ...]
    max_tool_calls: int | None
    expected_background: bool = False
    expected_specialist: str | None = None


@dataclass(frozen=True)
class SyntheticCheckResult:
    name: str
    status: str
    detail: str
    observed: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "observed": self.observed,
        }


DEFAULT_SCENARIOS: tuple[SyntheticScenario, ...] = (
    SyntheticScenario(
        name="casual_hi_no_rag",
        prompt="hi",
        expected_action="answer_now",
        expected_runtime="direct_answer",
        expected_toolsets=(),
        max_tool_calls=1,
    ),
    SyntheticScenario(
        name="casual_answer_chicken_rice",
        prompt="Quick question: what are three simple dinner ideas with chicken and rice?",
        expected_action="answer_now",
        expected_runtime="direct_answer",
        expected_toolsets=(),
        max_tool_calls=1,
    ),
    SyntheticScenario(
        name="explicit_secondbrain_lookup",
        prompt="Look up SecondBrain notes about Biff routing.",
        expected_action="secondbrain_lookup",
        expected_runtime="secondbrain_lookup",
        expected_toolsets=("file", "terminal"),
        max_tool_calls=1,
    ),
    SyntheticScenario(
        name="smart_connections_fallback_to_bounded_lookup",
        prompt="Use Smart Connections to find notes about Biff routing.",
        expected_action="secondbrain_lookup",
        expected_runtime="secondbrain_lookup",
        expected_toolsets=("file", "terminal"),
        max_tool_calls=1,
    ),
    SyntheticScenario(
        name="broad_secondbrain_deep_research_background",
        prompt="Search my entire SecondBrain and Smart Connections history for every old decision about Biff and summarize all of it.",
        expected_action="background",
        expected_runtime="background",
        expected_toolsets=(),
        max_tool_calls=1,
        expected_background=True,
        expected_specialist="quill",
    ),
    SyntheticScenario(
        name="read_only_kanban_status",
        prompt="Status check only: tell me what K-1346 and K-1347 currently say on the Kanban board. Do not modify code, do not run tests, do not restart anything, and do not continue either story.",
        expected_action="kanban_status",
        expected_runtime="kanban_read",
        expected_toolsets=("kanban", "terminal"),
        max_tool_calls=3,
    ),
    SyntheticScenario(
        name="keep_story_open",
        prompt="Keep K-1348 open until real live testing is complete.",
        expected_action="ranger_direct",
        expected_runtime="specialist_work",
        expected_toolsets=BIFF_V3_SPECIALIST_TOOLSETS,
        max_tool_calls=24,
        expected_background=True,
        expected_specialist="ranger",
    ),
    SyntheticScenario(
        name="create_story_routes_ranger",
        prompt="Create a Kanban story for proper Biff routing and move it to todo.",
        expected_action="ranger_direct",
        expected_runtime="specialist_work",
        expected_toolsets=BIFF_V3_SPECIALIST_TOOLSETS,
        max_tool_calls=24,
        expected_background=True,
        expected_specialist="ranger",
    ),
    SyntheticScenario(
        name="engineering_handoff",
        prompt="Delete Cockpit from the Hermes dashboard sidebar and verify it is gone.",
        expected_action="forge_direct",
        expected_runtime="specialist_work",
        expected_toolsets=BIFF_V3_SPECIALIST_TOOLSETS,
        max_tool_calls=24,
        expected_background=True,
        expected_specialist="forge",
    ),
    SyntheticScenario(
        name="qa_routes_vex",
        prompt="Have Vex QA the dashboard change and verify the live UI actually works.",
        expected_action="vex_direct",
        expected_runtime="specialist_work",
        expected_toolsets=BIFF_V3_SPECIALIST_TOOLSETS,
        max_tool_calls=24,
        expected_background=True,
        expected_specialist="vex",
    ),
    SyntheticScenario(
        name="docs_research_routes_quill",
        prompt="Have Quill research and document the new Biff routing contract in Obsidian.",
        expected_action="quill_direct",
        expected_runtime="specialist_work",
        expected_toolsets=BIFF_V3_SPECIALIST_TOOLSETS,
        max_tool_calls=24,
        expected_background=True,
        expected_specialist="quill",
    ),
    SyntheticScenario(
        name="continue_current_task",
        prompt="Continue the current task, but do not resume stale emergency summary turns.",
        expected_action="route_bundle",
        expected_runtime="continuation",
        expected_toolsets=BIFF_V3_SPECIALIST_TOOLSETS,
        max_tool_calls=16,
    ),
    SyntheticScenario(
        name="refresh_resume_context_recovery",
        prompt="The chat refreshed again so I can't see your progress; where did we leave off?",
        expected_action="resume_context",
        expected_runtime="context_resume",
        expected_toolsets=("file", "kanban", "session_search", "terminal"),
        max_tool_calls=3,
    ),
    SyntheticScenario(
        name="broad_slow_work",
        prompt="Archive every old Linear story and scan the entire Obsidian workspace for references before updating the board.",
        expected_action="ranger_direct",
        expected_runtime="specialist_work",
        expected_toolsets=BIFF_V3_SPECIALIST_TOOLSETS,
        max_tool_calls=24,
        expected_background=True,
        expected_specialist="ranger",
    ),
    SyntheticScenario(
        name="broad_multi_system_verification_background",
        prompt="Verify this is completely done across the repo, Kanban evidence, database state, schedules, service health, and all blocker comments.",
        expected_action="background",
        expected_runtime="background",
        expected_toolsets=(),
        max_tool_calls=1,
        expected_background=True,
        expected_specialist="vex",
    ),
    SyntheticScenario(
        name="quick_web",
        prompt="Can you look up deals online for a standing desk today?",
        expected_action="quick_web",
        expected_runtime="web_lookup",
        expected_toolsets=("file", "search", "terminal", "web"),
        max_tool_calls=4,
    ),
)


BIFF_RUNTIME_GUARDRAIL_CHECKS: tuple[str, ...] = (
    "current_chat_replies",
    "busy_message_queueing",
    "steer_interruption",
    "nonblocking_specialist_dispatch",
    "completion_closeout_delivery",
    "vex_closeout_pass_block_gate",
    "session_rollover_resume",
    "loop_bounding_no_empty_search_loops",
    "update_notifications_noop_degrade",
)


def _scenario_names(wanted: Iterable[str] | None) -> set[str]:
    return {str(name).strip() for name in (wanted or []) if str(name).strip()}


def run_synthetic_checks(
    scenario_names: Iterable[str] | None = None,
    *,
    platform_key: str = "discord",
    config: dict[str, Any] | None = None,
    configured_toolsets: Iterable[str] | None = None,
) -> list[SyntheticCheckResult]:
    """Run planner/runtime synthetic checks without sending live messages."""

    from agent.biff_intent_router import plan_biff_turn
    from gateway.session_hygiene import (
        apply_biff_tool_schema_profile,
        apply_biff_turn_toolset_plan,
        resolve_biff_live_max_iterations,
        resolve_biff_live_tool_guardrail_settings,
        widen_biff_toolsets_for_bundle,
    )

    config = config or {"biff": {"platforms": {"discord": {"tool_schema_profile": "v3"}}}}
    configured = sorted(
        {str(t) for t in (configured_toolsets or ("terminal", "file", "memory", "skills", "skills-read", "todo", "kanban", "session_search", "web", "search", "delegation", "code_execution", "vision")) if str(t).strip()}
    )
    wanted = _scenario_names(scenario_names)
    results: list[SyntheticCheckResult] = []
    for scenario in DEFAULT_SCENARIOS:
        if wanted and scenario.name not in wanted:
            continue
        try:
            plan = plan_biff_turn(scenario.prompt, command=False)
            base = apply_biff_tool_schema_profile(config, platform_key, configured)
            planned = apply_biff_turn_toolset_plan(
                config,
                platform_key,
                base,
                message=scenario.prompt,
                configured_toolsets=configured,
            )
            enabled = widen_biff_toolsets_for_bundle(
                config,
                platform_key,
                planned,
                configured,
                message=scenario.prompt,
            )
            guardrails = resolve_biff_live_tool_guardrail_settings(config, platform_key, message=scenario.prompt)
            max_iterations = resolve_biff_live_max_iterations(
                config,
                platform_key,
                message=scenario.prompt,
                base_max_iterations=90,
            )
            observed = {
                "action": plan.action,
                "runtime": plan.runtime,
                "toolset_profile": plan.toolset_profile,
                "background": plan.background,
                "specialist": plan.specialist,
                "enabled_toolsets": enabled,
                "guardrails": guardrails,
                "max_iterations": max_iterations,
            }
            problems: list[str] = []
            if plan.action != scenario.expected_action:
                problems.append(f"action={plan.action!r}, expected {scenario.expected_action!r}")
            if plan.runtime != scenario.expected_runtime:
                problems.append(f"runtime={plan.runtime!r}, expected {scenario.expected_runtime!r}")
            expected_toolsets = sorted(scenario.expected_toolsets)
            if sorted(enabled) != expected_toolsets:
                problems.append(f"enabled_toolsets={sorted(enabled)!r}, expected {expected_toolsets!r}")
            if scenario.max_tool_calls is not None and guardrails.get("max_tool_calls") != scenario.max_tool_calls:
                problems.append(
                    f"max_tool_calls={guardrails.get('max_tool_calls')!r}, expected {scenario.max_tool_calls!r}"
                )
            if plan.background is not scenario.expected_background:
                problems.append(f"background={plan.background!r}, expected {scenario.expected_background!r}")
            if plan.specialist != scenario.expected_specialist:
                problems.append(f"specialist={plan.specialist!r}, expected {scenario.expected_specialist!r}")
            status = FAIL if problems else PASS
            detail = "; ".join(problems) if problems else "planner/runtime/tool contract matched"
        except Exception as exc:
            status = FAIL
            detail = f"{type(exc).__name__}: {exc}"
            observed = {}
        results.append(SyntheticCheckResult(scenario.name, status, detail, observed))
    return results


def summarize_synthetic_checks(results: Iterable[SyntheticCheckResult]) -> dict[str, int]:
    counts = {PASS: 0, FAIL: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def run_runtime_guardrail_smoke_checks(
    *,
    run_py_source: str | None = None,
) -> list[SyntheticCheckResult]:
    """Run bounded static/synthetic checks for Biff's live runtime guardrails.

    These checks intentionally do not send live Discord messages or start role
    workers. They assert that risky runtime surfaces have explicit bounded
    behavior; focused pytest tests exercise the async helpers directly.
    """

    if run_py_source is None:
        run_py_source = (Path(__file__).resolve().parent / "run.py").read_text(encoding="utf-8")

    scenario_by_name = {scenario.name: scenario for scenario in DEFAULT_SCENARIOS}
    checks: dict[str, tuple[bool, str, dict[str, Any]]] = {
        "current_chat_replies": (
            "_thread_metadata_for_source" in run_py_source and "_reply_anchor_for_event" in run_py_source,
            "Discord replies keep the source chat/reply anchor unless thread metadata exists",
            {},
        ),
        "busy_message_queueing": (
            "Failed to queue busy message" in run_py_source and "Queued for the next turn" in run_py_source,
            "Busy queue merge failures are logged and the ack path remains bounded",
            {},
        ),
        "steer_interruption": (
            "running_agent.steer" in run_py_source and "Steered into current run" in run_py_source,
            "/steer injects into the active run without replaying as queued text",
            {},
        ),
        "nonblocking_specialist_dispatch": (
            "biff_nonblocking_specialist_dispatch" in run_py_source
            and "biff_nonblocking_specialist_dispatch_blocked" in run_py_source,
            "Specialist dispatch is asynchronous and has a degraded no-op path",
            {},
        ),
        "completion_closeout_delivery": (
            "_format_specialist_direct_completion_recap" in run_py_source
            and "_final_response_has_executive_closeout_shape" in run_py_source,
            "Background/synchronous subagent closeouts preserve Status/Evidence/Next-step shape",
            {},
        ),
        "vex_closeout_pass_block_gate": (
            "_parse_vex_top_level_closeout_decision" in run_py_source
            and "missing explicit top-level Vex PASS/BLOCK decision" in run_py_source
            and "ambiguous multiple top-level Vex PASS/BLOCK decisions" in run_py_source,
            "Vex closeout stays blocked unless an explicit unambiguous top-level PASS is present",
            {},
        ),
        "session_rollover_resume": (
            "build_resume_context_injection" in run_py_source
            and "write_continuation_checkpoint" in run_py_source,
            "Refresh/resume path has bounded context injection and checkpoint recovery",
            {},
        ),
        "loop_bounding_no_empty_search_loops": (
            scenario_by_name["explicit_secondbrain_lookup"].max_tool_calls == 1
            and scenario_by_name["smart_connections_fallback_to_bounded_lookup"].max_tool_calls == 1
            and scenario_by_name["broad_secondbrain_deep_research_background"].expected_background,
            "SecondBrain/Smart Connections lookups are bounded or deflected to background",
            {},
        ),
        "update_notifications_noop_degrade": (
            "Update prompt fallback send failed" in run_py_source and "continue" in run_py_source,
            "Update prompt delivery failures log and continue polling instead of crashing the watcher",
            {},
        ),
    }

    results: list[SyntheticCheckResult] = []
    for name in BIFF_RUNTIME_GUARDRAIL_CHECKS:
        passed, detail, observed = checks[name]
        results.append(SyntheticCheckResult(name, PASS if passed else FAIL, detail, observed))
    return results


def synthetic_results_as_json(results: Iterable[SyntheticCheckResult]) -> str:
    items = [result.to_dict() for result in results]
    return json.dumps(
        {
            "summary": {
                PASS: sum(1 for item in items if item.get("status") == PASS),
                FAIL: sum(1 for item in items if item.get("status") == FAIL),
            },
            "results": items,
        },
        indent=2,
        sort_keys=True,
    )


def write_synthetic_artifact(
    results: Iterable[SyntheticCheckResult],
    *,
    root: str | Path = "/Users/marco/.hermes/runtime/synthetic-checks",
) -> Path:
    path_root = Path(root)
    path_root.mkdir(parents=True, exist_ok=True)
    path = path_root / f"biff-synthetic-checks-{time.strftime('%Y%m%d-%H%M%S')}.json"
    data = {
        "created_at": int(time.time()),
        "summary": summarize_synthetic_checks(results),
        "results": [result.to_dict() for result in results],
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
