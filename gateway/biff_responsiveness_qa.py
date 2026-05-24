"""Real-life smoke checks for the Biff responsiveness roadmap.

The roadmap is split across Kanban cards K-1291..K-1303 plus follow-on speed
cards such as K-1308. These checks are
deliberately behavior-facing: they exercise the same helpers the Discord
gateway uses for routing, deflection, hot context, budget enforcement, and
progress text.  Cards that do not have an implementation hook yet stay
``pending`` so the umbrella story cannot be closed by vibes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Callable, Iterable, Mapping


PASS = "pass"
FAIL = "fail"
PENDING = "pending"


@dataclass(frozen=True)
class ResponsivenessQACheck:
    card: str
    title: str
    real_life_usage: str
    validator: Callable[[], tuple[str, str]]


@dataclass(frozen=True)
class ResponsivenessQAResult:
    card: str
    title: str
    status: str
    real_life_usage: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "card": self.card,
            "title": self.title,
            "status": self.status,
            "real_life_usage": self.real_life_usage,
            "detail": self.detail,
        }


def _pass(detail: str) -> tuple[str, str]:
    return PASS, detail


def _fail(detail: str) -> tuple[str, str]:
    return FAIL, detail


def _pending(detail: str) -> tuple[str, str]:
    return PENDING, detail


def _available_biff_bundles() -> dict[str, dict[str, str]]:
    return {
        "/biff-hermes-runtime-change": {"name": "Hermes runtime change", "slug": "biff-hermes-runtime-change"},
        "/biff-issue-execution": {"name": "Issue execution", "slug": "biff-issue-execution"},
        "/biff-research-to-decision": {"name": "Research to decision", "slug": "biff-research-to-decision"},
        "/biff-memory-knowledge-governance": {"name": "Memory governance", "slug": "biff-memory-knowledge-governance"},
        "/biff-automation-ownership": {"name": "Automation ownership", "slug": "biff-automation-ownership"},
    }


def _check_roadmap_matrix_exists() -> tuple[str, str]:
    expected = {f"K-{n}" for n in range(1291, 1304)}
    actual = {check.card for check in QA_CHECKS}
    missing = sorted(expected - actual)
    if missing:
        return _fail(f"QA matrix is missing roadmap cards: {', '.join(missing)}")
    return _pass("QA matrix covers K-1291 through K-1303.")


def _check_fast_router_deflects_slow_work() -> tuple[str, str]:
    from gateway.session_hygiene import maybe_build_slow_work_deflection

    prompt = (
        "Please archive every old Linear story and scan the entire Obsidian "
        "workspace for references before updating the board."
    )
    deflection = maybe_build_slow_work_deflection(prompt, platform_key="discord")
    if deflection is None:
        return _fail("Slow broad work did not deflect to a Kanban task spec.")
    if deflection.assignee != "ranger":
        return _fail(f"Slow work deflected to {deflection.assignee!r}, expected ranger.")
    status_question = "What is the state of the gateway right now?"
    if maybe_build_slow_work_deflection(status_question, platform_key="discord") is not None:
        return _fail("A normal status question was incorrectly deflected.")
    return _pass("Broad archive/migration work deflects; quick status questions stay in chat.")


def _check_answer_first_direct_questions_stay_lean() -> tuple[str, str]:
    from agent.biff_bundle_selector import select_biff_bundle_for_prompt

    selection = select_biff_bundle_for_prompt(
        "What folder do I use for Time Machine on the NAS?",
        _available_biff_bundles(),
    )
    if selection is not None:
        return _fail(f"Direct question selected bundle {selection.command_key}; expected no bundle.")
    return _pass("Direct practical questions stay on the lean chat path.")


def _check_prompt_cache_discipline_proxy() -> tuple[str, str]:
    from gateway.session_hygiene import apply_biff_tool_schema_profile

    config = {"biff": {"platforms": {"discord": {"tool_schema_profile": "v3"}}}}
    configured = [
        "terminal",
        "file",
        "memory",
        "skills",
        "todo",
        "kanban",
        "browser",
        "cronjob",
        "delegation",
        "code_execution",
    ]
    first = apply_biff_tool_schema_profile(config, "discord", configured)
    second = apply_biff_tool_schema_profile(config, "discord", configured)
    if first != second:
        return _fail("Discord tool schema profile is not stable across equivalent turns.")
    if "browser" in first or "cronjob" in first or "delegation" in first:
        return _fail(f"Base quick-chat schema still includes heavy toolsets: {first}")
    return _pass("Base Discord schema is deterministic and narrow, protecting cache and prompt size.")


def _check_hot_context_capsule() -> tuple[str, str]:
    from gateway.biff_hot_context import build_biff_hot_context, clear_biff_hot_context_cache

    clear_biff_hot_context_cache()
    config = {
        "kanban": {"dispatch_in_gateway": False, "orchestration": "manual"},
        "biff": {"platforms": {"discord": {"hot_context": True}}},
    }
    capsule = build_biff_hot_context(config, platform_key="discord", session_key="qa", ttl_seconds=30)
    if "Biff Hot Context" not in capsule:
        return _fail("Hot context capsule was not produced for Discord.")
    if "orchestration=manual" not in capsule:
        return _fail("Hot context did not include current Kanban operating mode.")
    if len(capsule) > 2200:
        return _fail(f"Hot context capsule is too large: {len(capsule)} chars.")
    return _pass("Hot context is available, bounded, and includes Kanban operating mode.")


def _check_llm_free_memory_retrieval_pending() -> tuple[str, str]:
    from gateway.biff_fast_memory import build_biff_fast_memory_snapshot

    snapshot = build_biff_fast_memory_snapshot({}, query="Biff Discord Kanban source of truth")
    if "Biff Fast Memory Snapshot" not in snapshot:
        return _fail("Fast memory snapshot was not available.")
    if "LLM-free" not in snapshot:
        return _fail("Fast memory snapshot does not make the no-model-call path explicit.")
    if len(snapshot) > 1400:
        return _fail(f"Fast memory snapshot is too large: {len(snapshot)} chars.")
    return _pass("Structured Mnemosyne digest/recall is available as bounded LLM-free live context.")


def _check_tool_schema_warm_pool_proxy() -> tuple[str, str]:
    from gateway.session_hygiene import widen_biff_toolsets_for_bundle

    config = {"biff": {"platforms": {"discord": {"bundle_tool_widening": True}}}}
    base = ["terminal", "file", "memory", "skills-read", "todo", "kanban"]
    configured = sorted(set(base + ["code_execution", "delegation", "skills", "web", "vision", "browser"]))
    widened = widen_biff_toolsets_for_bundle(
        config,
        "discord",
        base,
        configured,
        bundle_key="biff-hermes-runtime-change",
    )
    if "code_execution" not in widened or "delegation" not in widened:
        return _fail(f"Runtime-change bundle did not widen to specialist toolsets: {widened}")
    if "browser" in widened:
        return _fail("Runtime-change bundle widened to an unrelated browser toolset.")
    return _pass("Quick-chat schema stays lean and bundle-specific escalation adds only relevant tools.")


def _check_large_tool_results_are_pointer_sized() -> tuple[str, str]:
    from gateway.session_hygiene import cap_model_facing_tool_outputs

    raw = "Wrote evidence to /tmp/biff-speed-qa.log\n" + ("x" * 25_000)
    history = [{"role": "tool", "tool_call_id": "qa", "content": raw}]
    capped, stats = cap_model_facing_tool_outputs(
        history,
        session_id="qa-session",
        transcript_ref="qa-transcript",
        max_tool_output_chars=900,
        preview_chars=160,
    )
    content = str(capped[0]["content"])
    if stats.tool_outputs_capped_count != 1:
        return _fail("Large tool output was not capped for model-facing history.")
    if len(content) > 1000:
        return _fail(f"Capped tool output is still too large: {len(content)} chars.")
    if "/tmp/biff-speed-qa.log" not in content:
        return _fail("Capped output lost the recoverable evidence pointer.")
    if history[0]["content"] != raw:
        return _fail("Full raw transcript was mutated while building model-facing history.")
    return _pass("Large tool output is summarized for the model while preserving a file pointer and raw transcript.")


def _check_hard_live_chat_budget() -> tuple[str, str]:
    from tools.chat_guardrails import ChatToolPolicy, apply_chat_tool_policy, clear_chat_tool_policy, set_chat_tool_policy

    task_id = "qa-live-chat-budget"
    try:
        set_chat_tool_policy(
            task_id,
            ChatToolPolicy(max_terminal_timeout=15, max_tool_calls=2, block_broad_shell_search=True),
        )
        args, message = apply_chat_tool_policy(
            "terminal",
            {"command": "grep -rnH TODO .", "timeout": 120},
            task_id=task_id,
        )
        if message is None or "broad shell search" not in message.lower():
            return _fail("Broad recursive shell search was not blocked.")
        args, message = apply_chat_tool_policy(
            "terminal",
            {"command": "rg TODO gateway", "timeout": 120},
            task_id=task_id,
        )
        if message is not None:
            return _fail(f"Scoped rg command was incorrectly blocked: {message}")
        if args.get("timeout") != 15:
            return _fail(f"Terminal timeout was not capped to 15 seconds: {args}")
        _args, message = apply_chat_tool_policy("file", {"path": "x"}, task_id=task_id)
        if message is None or "tool budget" not in message.lower():
            return _fail("Tool-call budget did not stop the third live-chat tool call.")
        return _pass("Broad searches are blocked, shell time is capped, and tool budget stops overrun.")
    finally:
        clear_chat_tool_policy(task_id)


def _check_parallel_chat_lane_pending() -> tuple[str, str]:
    from gateway.biff_parallel_chat import should_use_biff_parallel_chat_lane

    casual, casual_reason = should_use_biff_parallel_chat_lane(
        "What can I make for dinner with eggs and rice?",
        platform_key="discord",
        running_agent=True,
    )
    status, status_reason = should_use_biff_parallel_chat_lane(
        "Can you check gateway status?",
        platform_key="discord",
        running_agent=True,
    )
    workflow, workflow_reason = should_use_biff_parallel_chat_lane(
        "Implement the remaining speed stories and update Kanban.",
        platform_key="discord",
        running_agent=True,
    )
    if not casual or "answer_now" not in casual_reason:
        return _fail("Casual direct question does not take the parallel answer lane.")
    if not status or "one_tool" not in status_reason:
        return _fail("Quick status check does not take the parallel one-tool lane.")
    if workflow or "main work lane" not in workflow_reason:
        return _fail("Workflow implementation work incorrectly leaves the main work lane.")
    return _pass("Busy Discord sessions route casual/simple questions to an independent quick-chat lane.")


def _check_plain_language_progress_updates() -> tuple[str, str]:
    from gateway.session_hygiene import render_plain_language_heartbeat

    heartbeat = render_plain_language_heartbeat(
        elapsed_seconds=245,
        activity={"current_tool": "terminal", "last_activity_desc": "pytest tests/gateway/test_x.py"},
    )
    if "Still working:" not in heartbeat:
        return _fail(f"Heartbeat has unexpected shape: {heartbeat}")
    if "terminal" in heartbeat.lower() or "pytest" in heartbeat.lower():
        return _fail(f"Heartbeat leaked technical tool detail: {heartbeat}")
    if "checking" not in heartbeat.lower():
        return _fail(f"Heartbeat did not explain the work in plain language: {heartbeat}")
    return _pass("Progress heartbeat describes the work in layman's terms without tool names.")


def _check_latency_measurement_pending() -> tuple[str, str]:
    import tempfile
    from pathlib import Path

    from gateway.biff_latency import record_biff_latency, render_biff_latency_report

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "latency.jsonl"
        for idx, response_time in enumerate((1.0, 2.0, 4.0, 8.0, 16.0)):
            record_biff_latency(
                platform="discord",
                chat_id="qa",
                session_id=f"qa-{idx}",
                model="qa-model",
                response_time=response_time,
                wall_metrics={
                    "wall_time": response_time,
                    "agent_loop_time": response_time - 0.2,
                    "gateway_prep_time": 0.1,
                    "gateway_pre_agent_time": 0.1,
                },
                api_calls=idx + 1,
                last_prompt_tokens=10_000 + idx,
                path=path,
                now=1000 + idx,
            )
        report = render_biff_latency_report(path=path, limit=5)
    required = ("p50", "p95", "Slowest recent turns", "mostly model/tool work")
    missing = [needle for needle in required if needle not in report]
    if missing:
        return _fail(f"Latency report is missing expected fields: {', '.join(missing)}")
    return _pass("Recent Discord turns persist p50/p95, worst turns, and the dominant slow phase.")


def _check_fast_router_model_pending() -> tuple[str, str]:
    from agent.biff_intent_router import route_biff_live_intent

    direct = route_biff_live_intent("What folder do I use for Time Machine?")
    slow = route_biff_live_intent("Archive every Linear story and scan the whole Obsidian workspace.")
    status = route_biff_live_intent("Can you check gateway status?")
    workflow = route_biff_live_intent("Implement the speed story and add tests.")
    if direct.action != "answer_now" or direct.allow_bundle_selection:
        return _fail("Direct question does not stay on the answer-now path before bundle load.")
    if slow.action != "ranger_direct" or slow.allow_bundle_selection:
        return _fail("Broad board/archive work does not route to Ranger before bundle load.")
    if status.action != "one_tool" or status.max_live_tool_calls != 1:
        return _fail("Quick status check does not route to the one-tool live path.")
    if workflow.action != "forge_direct" or workflow.allow_bundle_selection:
        return _fail("Implementation workflow does not route to Forge direct before generic bundle selection.")
    return _pass("Cheap deterministic router classifies answer-now, one-tool, background, Forge-direct, and web paths before heavy bundle load.")


def _check_normal_prompt_budget_under_10k() -> tuple[str, str]:
    from gateway.session_hygiene import (
        apply_biff_prompt_budget,
        resolve_biff_prompt_budget_tokens,
        should_apply_biff_prompt_budget,
    )

    config = {"biff": {"platforms": {"discord": {"prompt_budget": True, "prompt_budget_tokens": 10_000}}}}
    if not should_apply_biff_prompt_budget(config, "discord", message="What is the gateway state?"):
        return _fail("Ordinary Discord question did not opt into the prompt budget.")
    bundle_message = 'The user has invoked the "/biff-hermes-runtime-change" skill bundle.'
    if should_apply_biff_prompt_budget(config, "discord", message=bundle_message):
        return _fail("Specialist Forge bundle was not exempted from the normal prompt budget.")
    history = []
    for index in range(80):
        history.append({"role": "user", "content": f"old context {index} " + ("x" * 900)})
        history.append({"role": "assistant", "content": f"old reply {index} " + ("y" * 900)})
    history.extend(
        [
            {"role": "user", "content": "Remember: Forge owns engineering and Ranger owns Kanban administration."},
            {"role": "assistant", "content": "Kanban is the source of truth; Todo stays captured until explicitly promoted to Ready."},
        ]
    )
    budget_tokens = resolve_biff_prompt_budget_tokens(config, "discord")
    budgeted, stats = apply_biff_prompt_budget(
        history,
        budget_tokens=budget_tokens,
        system_context_prompt=(
            "Biff hot context: Forge owns engineering. Ranger owns Kanban. "
            "Kanban is source of truth. K-1306 says Todo must not auto-promote."
        ),
        channel_prompt="discord channel",
        tool_schema_chars=8_000,
    )
    joined = "\n".join(str(message.get("content") or "") for message in budgeted)
    if not stats.applied or stats.kept_chars > 40_000:
        return _fail(f"Prompt budget did not trim to the 10k-token chars equivalent: {stats}")
    if "Forge owns engineering" not in joined or "Todo stays captured" not in joined:
        return _fail("Prompt budget dropped the newest role/Kanban safety facts.")
    return _pass("Ordinary Discord history trims toward 10k tokens while specialist bundles can widen and newest role/Kanban facts remain visible.")


QA_CHECKS: tuple[ResponsivenessQACheck, ...] = (
    ResponsivenessQACheck("K-1291", "Biff responsiveness roadmap", "Operator asks whether the roadmap itself has QA coverage.", _check_roadmap_matrix_exists),
    ResponsivenessQACheck("K-1292", "Make live chat a fast router", "Marco asks for broad archive/migration work from Discord.", _check_fast_router_deflects_slow_work),
    ResponsivenessQACheck("K-1293", "Answer first, verify second", "Marco asks a simple practical question that should not load a heavy bundle.", _check_answer_first_direct_questions_stay_lean),
    ResponsivenessQACheck("K-1294", "Prompt cache discipline", "Two ordinary Discord turns should expose the same narrow base schema.", _check_prompt_cache_discipline_proxy),
    ResponsivenessQACheck("K-1295", "Hot memory/context capsule", "Biff should know current Kanban mode without opening tools.", _check_hot_context_capsule),
    ResponsivenessQACheck("K-1296", "LLM-free memory retrieval", "Biff should retrieve compact memory facts without a model round trip.", _check_llm_free_memory_retrieval_pending),
    ResponsivenessQACheck("K-1297", "Tool schema warm pools", "Specialist work should widen tools only after intent is known.", _check_tool_schema_warm_pool_proxy),
    ResponsivenessQACheck("K-1298", "Store large tool results by pointer", "A huge shell result should not get dragged into later prompts.", _check_large_tool_results_are_pointer_sized),
    ResponsivenessQACheck("K-1299", "Hard live-chat budget", "A Discord turn should not burn minutes on broad shell work.", _check_hard_live_chat_budget),
    ResponsivenessQACheck("K-1300", "Non-blocking handoff and parallel chat lane", "Marco should be able to ask a recipe-style question while work continues.", _check_parallel_chat_lane_pending),
    ResponsivenessQACheck("K-1301", "Plain-language progress updates", "Long work should send useful non-technical heartbeat updates.", _check_plain_language_progress_updates),
    ResponsivenessQACheck("K-1302", "Measure p50/p95 response time", "Marco should be able to see why recent turns were slow.", _check_latency_measurement_pending),
    ResponsivenessQACheck("K-1303", "Fast model/router for intent", "Biff should classify intent before loading heavyweight routes.", _check_fast_router_model_pending),
    ResponsivenessQACheck("K-1308", "Normal Discord prompt budget", "A normal Discord question should stay near 10k prompt tokens without losing current role/Kanban facts.", _check_normal_prompt_budget_under_10k),
)


def run_responsiveness_qa(cards: Iterable[str] | None = None) -> list[ResponsivenessQAResult]:
    wanted = {str(card).strip().upper() for card in cards or [] if str(card).strip()}
    results: list[ResponsivenessQAResult] = []
    for check in QA_CHECKS:
        if wanted and check.card.upper() not in wanted:
            continue
        try:
            status, detail = check.validator()
        except Exception as exc:
            status, detail = FAIL, f"{type(exc).__name__}: {exc}"
        results.append(
            ResponsivenessQAResult(
                card=check.card,
                title=check.title,
                status=status,
                real_life_usage=check.real_life_usage,
                detail=detail,
            )
        )
    return results


def summarize_responsiveness_qa(results: Iterable[ResponsivenessQAResult]) -> dict[str, int]:
    counts = {PASS: 0, FAIL: 0, PENDING: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def results_as_json(results: Iterable[ResponsivenessQAResult]) -> str:
    data = [result.to_dict() for result in results]
    return json.dumps({"summary": summarize_responsiveness_qa(results), "results": data}, indent=2)


def gateway_launchd_state() -> str:
    """Best-effort real gateway smoke state for manual QA reports."""

    try:
        proc = subprocess.run(
            ["launchctl", "print", "system/ai.hermes.gateway"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        return f"unknown: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return "not loaded"
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("state = "):
            return stripped.split("=", 1)[1].strip()
    return "loaded"
