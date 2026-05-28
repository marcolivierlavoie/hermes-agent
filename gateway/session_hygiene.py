"""Gateway session-hygiene helpers.

Small pure functions kept outside gateway.run so hygiene caps are testable
without importing the full gateway runner.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_HYGIENE_MAX_MESSAGES = 240
DEFAULT_HYGIENE_MAX_CONTENT_CHARS = 24_000
DEFAULT_HYGIENE_MAX_TOOL_OUTPUT_CHARS = 4_000
DEFAULT_MODEL_FACING_TOOL_OUTPUT_CHARS = 16_000
DEFAULT_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS = 48_000
ADAPTIVE_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS_MANY = 32_000
ADAPTIVE_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS_HEAVY = 24_000
ADAPTIVE_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS_EXTREME = 16_000
DEFAULT_TOOL_PREVIEW_CHARS = 1_000
DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS = 10_000
MIN_BIFF_DISCORD_PROMPT_BUDGET_TOKENS = 6_000
MAX_BIFF_DISCORD_PROMPT_BUDGET_TOKENS = 40_000
CHARS_PER_TOKEN_ESTIMATE = 4

BIFF_OPERATING_MODES: tuple[str, ...] = ("normal", "economy", "emergency", "evidence-only")


@dataclass(frozen=True)
class BiffOperatingMode:
    """Runtime quota/economy mode for Biff-facing gateway turns.

    Modes are provider/model/account neutral. They preserve persona, memory,
    safety gates, and source-of-truth policy; automatic effects are limited to
    smaller model-facing history/tool-output copies and lower turn iteration
    ceilings. Full transcripts remain intact.
    """

    name: str
    label: str
    description: str
    max_iterations: int | None
    max_tool_output_chars: int
    max_message_content_chars: int | None
    preview_chars: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "max_iterations": self.max_iterations,
            "max_tool_output_chars": self.max_tool_output_chars,
            "max_message_content_chars": self.max_message_content_chars,
            "preview_chars": self.preview_chars,
        }


@dataclass(frozen=True)
class BiffRuntimeInstabilitySignal:
    """Recent runtime instability signal for Discord live-turn degradation."""

    active: bool
    reasons: tuple[str, ...] = ()
    sigterm_count: int = 0
    codex_empty_output_count: int = 0
    repeated_failure_count: int = 0
    severity: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "reasons": list(self.reasons),
            "sigterm_count": self.sigterm_count,
            "codex_empty_output_count": self.codex_empty_output_count,
            "repeated_failure_count": self.repeated_failure_count,
            "severity": self.severity,
        }


_BIFF_MODE_SPECS: dict[str, BiffOperatingMode] = {
    "normal": BiffOperatingMode("normal", "Normal", "Full Biff behavior with default context/tool-output hygiene only.", None, DEFAULT_MODEL_FACING_TOOL_OUTPUT_CHARS, None, DEFAULT_TOOL_PREVIEW_CHARS),
    "economy": BiffOperatingMode("economy", "Economy", "Preserve behavior while reducing accidental context/tool/history bloat for non-critical turns.", 40, 8_000, 16_000, 800),
    "emergency": BiffOperatingMode("emergency", "Emergency", "Keep only essential context while preserving enough iteration budget to complete bounded Discord work.", 60, 4_000, 8_000, 500),
    "evidence-only": BiffOperatingMode("evidence-only", "Evidence-only", "Gather/check evidence and summarize; avoid side-effecting actions unless already explicitly approved.", 8, 2_000, 4_000, 350),
}


def detect_biff_runtime_instability(log_text: Any) -> BiffRuntimeInstabilitySignal:
    """Detect crash/restart/self-loop symptoms from recent gateway log text."""

    text = str(log_text or "")[-200_000:]
    lowered = text.lower()
    sigterm_raw_count = len(re.findall(r"\bsigterm\b|received signal 15|signal\.sigterm", lowered))
    sigterm_event_count = sum(
        1
        for line in lowered.splitlines()
        if re.search(r"received\s+sigterm|received\s+signal\s+15|initiating\s+shutdown", line)
    )
    # A single graceful restart often emits several SIGTERM-shaped lines
    # ("Received SIGTERM", "signal=SIGTERM", "signal.SIGTERM").  The guard is
    # meant to react to restart events, not repeated mentions of the same event.
    sigterm_count = sigterm_event_count or sigterm_raw_count
    codex_empty_output_raw_count = len(re.findall(r"output\s*=\s*none|output none|empty terminal frame", lowered))
    recovered_codex_empty_count = len(
        re.findall(
            r"codex responses stream terminal frame had output\s*=\s*none;\s*recovering",
            lowered,
        )
    )
    # Recovered Codex stream terminal frames are warning-only. They explain why
    # a stream looked odd, but the runtime already recovered usable output.
    codex_empty_output_count = max(0, codex_empty_output_raw_count - recovered_codex_empty_count)
    repeated_failure_count = len(
        re.findall(
            r"long_turn_repeated_failure_fallback|repeated_exact_failure|same_tool_failure|tool-loop|tool loop",
            lowered,
        )
    )
    pending_message_bug = "get_pending_message" in lowered and "attribute" in lowered
    security_lockdown = bool(
        re.search(
            r"security[_\s-]?(?:lockdown|incident|breach)|credential\s+(?:leak|exposure)|secret\s+(?:leak|exposure)",
            lowered,
        )
    )
    reasons: list[str] = []
    if sigterm_count >= 4:
        reasons.append("recent_gateway_restarts")
    if codex_empty_output_count >= 3:
        reasons.append("codex_empty_terminal_frames")
    if repeated_failure_count >= 2:
        reasons.append("repeated_tool_or_long_turn_loop")
    if pending_message_bug:
        reasons.append("discord_pending_message_adapter_error")
    if security_lockdown:
        reasons.append("security_lockdown")

    extreme = (
        security_lockdown
        or sigterm_count >= 8
        or repeated_failure_count >= 6
        or (sigterm_count >= 5 and repeated_failure_count >= 3)
        or (pending_message_bug and repeated_failure_count >= 3)
    )
    severity = "extreme" if extreme else ("soft" if reasons else "none")
    return BiffRuntimeInstabilitySignal(
        active=bool(reasons),
        reasons=tuple(reasons),
        sigterm_count=sigterm_count,
        codex_empty_output_count=codex_empty_output_count,
        repeated_failure_count=repeated_failure_count,
        severity=severity,
    )


_LOG_TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d{3})?\b")


def _filter_recent_instability_log_text(
    text: str,
    *,
    window_seconds: int,
    now: datetime | None = None,
) -> str:
    """Keep only timestamped log lines in the current short instability window."""

    if window_seconds <= 0:
        return text
    parsed: list[tuple[datetime | None, str]] = []
    timestamps: list[datetime] = []
    for line in str(text or "").splitlines():
        match = _LOG_TIMESTAMP_RE.match(line)
        ts = None
        if match:
            try:
                ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                timestamps.append(ts)
            except ValueError:
                ts = None
        parsed.append((ts, line))
    if not timestamps:
        return ""

    current = now or datetime.now()
    latest = max(timestamps)
    if latest < current - timedelta(seconds=window_seconds * 2):
        return ""
    cutoff = current - timedelta(seconds=window_seconds)
    chunks: list[str] = []
    keep_continuation = False
    for ts, line in parsed:
        if ts is not None:
            keep_continuation = ts >= cutoff
        if keep_continuation and (ts is not None or line.startswith((" ", "\t"))):
            chunks.append(line)
    return "\n".join(chunks)


def inspect_biff_runtime_instability_logs(
    log_dir: str | os.PathLike[str] | None = None,
    *,
    max_chars_per_file: int = 80_000,
    window_seconds: int = 600,
) -> BiffRuntimeInstabilitySignal:
    """Inspect recent local gateway/error logs for live-turn instability."""

    base = Path(log_dir or (Path.home() / ".hermes" / "logs"))
    chunks: list[str] = []
    for name in ("gateway.error.log", "errors.log", "tui_gateway_crash.log", "gateway.log"):
        path = base / name
        try:
            if path.is_file():
                text = path.read_text(errors="replace")
                chunks.append(
                    _filter_recent_instability_log_text(
                        text[-max_chars_per_file:],
                        window_seconds=window_seconds,
                    )
                )
        except OSError:
            continue
    return detect_biff_runtime_instability("\n".join(chunks))


_FALSE_CONFIG_VALUES = {"0", "false", "no", "off", "disabled"}
_TRUE_CONFIG_VALUES = {"1", "true", "yes", "on", "enabled"}


def _coerce_config_bool(value: Any, *, default: bool | None = None) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in _TRUE_CONFIG_VALUES:
        return True
    if raw in _FALSE_CONFIG_VALUES:
        return False
    return default


def biff_runtime_instability_guard_disabled(
    config: Mapping[str, Any] | None = None,
    platform_key: str | None = None,
) -> bool:
    """Return whether the Biff runtime instability guard is explicitly disabled.

    This is a controlled kill switch for live recovery. ``BIFF_DISABLE_INSTABILITY_GUARD=1``
    is the preferred emergency override; the older
    ``HERMES_BIFF_RUNTIME_INSTABILITY_GUARD=0`` remains supported for rollback
    compatibility. Config mirrors are intentionally narrow and profile-scoped.
    """

    if _coerce_config_bool(os.getenv("BIFF_DISABLE_INSTABILITY_GUARD"), default=False):
        return True
    legacy_enabled = _coerce_config_bool(os.getenv("HERMES_BIFF_RUNTIME_INSTABILITY_GUARD"), default=None)
    if legacy_enabled is False:
        return True

    cfg: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    maybe_biff_cfg = cfg.get("biff")
    biff_cfg: Mapping[str, Any] = maybe_biff_cfg if isinstance(maybe_biff_cfg, Mapping) else {}
    platform_cfg: Mapping[str, Any] = {}
    maybe_platforms = biff_cfg.get("platforms")
    platforms: Mapping[str, Any] = maybe_platforms if isinstance(maybe_platforms, Mapping) else {}
    maybe_platform = platforms.get(platform_key) if platform_key else None
    if isinstance(maybe_platform, Mapping):
        platform_cfg = maybe_platform

    for scoped_cfg in (platform_cfg, biff_cfg):
        disabled = _coerce_config_bool(scoped_cfg.get("disable_instability_guard"), default=None)
        if disabled is True:
            return True
        enabled = _coerce_config_bool(scoped_cfg.get("runtime_instability_guard_enabled"), default=None)
        if enabled is False:
            return True
    return False


def apply_biff_runtime_instability_guard(
    mode: BiffOperatingMode,
    signal: BiffRuntimeInstabilitySignal | None,
    *,
    disabled: bool = False,
) -> BiffOperatingMode:
    """Fail soft for recoverable runtime weirdness; evidence-only is extreme-only."""

    if disabled or not signal or not signal.active:
        return mode
    if mode.name in {"emergency", "evidence-only"}:
        return mode
    severity = str(getattr(signal, "severity", "") or "").strip().lower()
    if severity == "extreme":
        return _BIFF_MODE_SPECS["evidence-only"]
    if "repeated_tool_or_long_turn_loop" in set(getattr(signal, "reasons", ()) or ()):
        return _BIFF_MODE_SPECS["emergency"]
    return mode


def relax_biff_runtime_instability_guard_for_turn(
    mode: BiffOperatingMode,
    signal: BiffRuntimeInstabilitySignal | None,
    *,
    route_runtime: Any = None,
    route_action: Any = None,
) -> BiffOperatingMode:
    """Restore execution-capable tools for explicit recovery/continuation turns.

    The instability guard intentionally degrades ordinary Discord turns to
    evidence-only after crash-loop symptoms.  Recovery turns are different: if
    Marco asks why Biff lacks tools, asks Biff to restore tools, or an empty
    post-restart continuation is trying to resume interrupted work, evidence-only
    filtering removes the very terminal/file/Kanban tools needed to diagnose and
    recover.  Keep the small emergency budget, but do not strip the base operator
    toolsets for those turn classes.
    """

    if not signal or not signal.active or mode.name != "evidence-only":
        return mode
    runtime = str(route_runtime or "").strip().lower()
    action = str(route_action or "").strip().lower()
    if runtime in {
        "tool_access_recovery",
        "continuation",
        "kanban_read",
        "kanban_admin",
        "biff-hermes-runtime-change",
        "runtime_change",
    } or action in {"resume_context", "kanban_status", "kanban_admin"}:
        return _BIFF_MODE_SPECS["emergency"]
    return mode


def apply_biff_runtime_instability_tool_guardrails(
    settings: Mapping[str, Any] | None,
    signal: BiffRuntimeInstabilitySignal | None,
    *,
    disabled: bool = False,
) -> dict[str, Any]:
    """Narrow tool-loop budget during unstable gateway windows."""

    adjusted = dict(settings or {})
    if disabled:
        adjusted["runtime_instability_guard"] = {"disabled": True, "reason": "disabled_by_config"}
        return adjusted
    if not signal or not signal.active:
        return adjusted
    severity = str(getattr(signal, "severity", "") or "").strip().lower()
    if severity != "extreme":
        if "repeated_tool_or_long_turn_loop" in set(getattr(signal, "reasons", ()) or ()):
            current_max = adjusted.get("max_tool_calls")
            try:
                adjusted["max_tool_calls"] = min(int(current_max), 24) if current_max is not None else 24
            except Exception:
                adjusted["max_tool_calls"] = 24
            current_timeout = adjusted.get("terminal_timeout")
            try:
                adjusted["terminal_timeout"] = min(int(current_timeout), 45) if current_timeout is not None else 45
            except Exception:
                adjusted["terminal_timeout"] = 45
        adjusted["runtime_instability_guard"] = signal.to_dict()
        return adjusted
    current_max = adjusted.get("max_tool_calls")
    try:
        adjusted["max_tool_calls"] = min(int(current_max), 2) if current_max is not None else 2
    except Exception:
        adjusted["max_tool_calls"] = 2
    current_timeout = adjusted.get("terminal_timeout")
    try:
        adjusted["terminal_timeout"] = min(int(current_timeout), 10) if current_timeout is not None else 10
    except Exception:
        adjusted["terminal_timeout"] = 10
    adjusted["runtime_instability_guard"] = signal.to_dict()
    return adjusted


def normalize_biff_operating_mode(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "": "normal",
        "default": "normal",
        "standard": "normal",
        "regular": "normal",
        "econ": "economy",
        "low": "economy",
        "quota": "economy",
        "quota-economy": "economy",
        "urgent": "emergency",
        "panic": "emergency",
        "evidence": "evidence-only",
        "readonly": "evidence-only",
        "read-only": "evidence-only",
        "observer": "evidence-only",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in BIFF_OPERATING_MODES else "normal"


def resolve_biff_operating_mode(config: Mapping[str, Any] | None = None, platform_key: str | None = None) -> BiffOperatingMode:
    """Resolve active Biff operating mode from env/config without side effects.

    Precedence: HERMES_BIFF_MODE env var, biff.platforms.<platform>.operating_mode,
    biff.operating_mode, quota_economy.mode, then normal.
    """

    env_mode = os.getenv("HERMES_BIFF_MODE")
    if env_mode:
        return _BIFF_MODE_SPECS[normalize_biff_operating_mode(env_mode)]

    cfg = config if isinstance(config, Mapping) else {}
    biff_cfg = cfg.get("biff") if isinstance(cfg.get("biff"), Mapping) else {}
    mode_value = None
    if platform_key and isinstance(biff_cfg, Mapping):
        platforms = biff_cfg.get("platforms") if isinstance(biff_cfg.get("platforms"), Mapping) else {}
        platform_cfg = platforms.get(platform_key) if isinstance(platforms.get(platform_key), Mapping) else {}
        mode_value = platform_cfg.get("operating_mode") or platform_cfg.get("mode")
    if mode_value is None and isinstance(biff_cfg, Mapping):
        mode_value = biff_cfg.get("operating_mode") or biff_cfg.get("mode")
    if mode_value is None:
        quota_cfg = cfg.get("quota_economy") if isinstance(cfg.get("quota_economy"), Mapping) else {}
        if isinstance(quota_cfg, Mapping):
            mode_value = quota_cfg.get("mode")
    return _BIFF_MODE_SPECS[normalize_biff_operating_mode(mode_value)]


def biff_discord_quick_check_budget_prompt(settings: Mapping[str, Any] | None = None) -> str:
    """Return the active Discord live-budget routing contract for the system prompt."""

    cfg = settings if isinstance(settings, Mapping) else {}
    max_tools = cfg.get("max_tool_calls")
    timeout = cfg.get("terminal_timeout")
    route = cfg.get("route_action") or "default"
    return (
        "[System note: Discord live tool-budget contract is active. "
        f"Route={route}; max live tool calls={max_tools if max_tools is not None else 'unlimited'}; "
        f"terminal timeout cap={timeout if timeout is not None else 'default'}s. "
        "For ordinary Discord turns, do at most 1-2 narrow quick-check tool calls, prefer direct source-of-truth queries, and shape outputs before they enter context. "
        "If the request needs multi-step verification, broad searching, or several systems, answer concisely with the first decisive status and create/use a Kanban/background/continuation handle rather than exhausting the live turn. "
        "Never claim done/fixed/deployed until visible evidence is checked; label partial results as awaiting verification.]"
    )


def biff_operating_mode_prompt(mode: BiffOperatingMode) -> str:
    if mode.name == "normal":
        return ""
    base = (
        f"[System note: Active Biff operating mode is {mode.label}. "
        "Do not change provider, model, account, persona, memory behavior, safety gates, or source-of-truth policy. "
        "Be more concise and avoid accidental context/tool/history bloat. "
        "For explicit work requests, complete the bounded deliverable in the current turn when safe; do not stop at an acknowledgement, do not pretend unfinished work is done, and do not ask Marco to type continue unless a real external blocker remains. "
        "For direct questions, answer from current context and hot context when possible; if you are unsure, say what you believe and identify the smallest useful verification step. "
        "Prefer rg over grep, always scope shell searches to a specific directory/file set, and avoid whole-repo recursive searches in chat. "
        "Use Kanban/background work only for genuinely broad work or when Marco explicitly asks for background execution; do not use it as a way to avoid finishing normal implementation, verification, or cleanup tasks. "
    )
    if mode.name == "economy":
        return base + "Use tools when they materially improve correctness; prefer bounded reads, short terminal timeouts, focused evidence, and a clear final done/blocked status.]"
    if mode.name == "emergency":
        return base + "Prioritize the smallest safe action that completes or unblocks the ask; defer only nice-to-have exploration.]"
    return base + "Evidence-only: gather/check evidence and summarize. Tool access is restricted to read-only evidence toolsets unless explicit current-turn approval is added by the gateway.]"


BIFF_EVIDENCE_ONLY_SAFE_TOOLSETS: frozenset[str] = frozenset(
    {
        "search",
        "session_search",
        "web",
        "vision",
    }
)

# Opt-in narrowed schema for Biff-facing turns.  This is intentionally a
# conservative subset of normal Discord/Biff work: it preserves persona/memory
# (memory, session_search), BIF implementation capability (terminal/file),
# structured planning and persona instructions (todo/skills), clarification,
# bounded code/delegation, kanban orchestration, and read-only evidence
# surfaces.  It excludes large nice-to-have or higher-risk fixed schemas
# (browser, cronjob, image/tts, messaging) unless the operator explicitly
# selects the full profile.
BIFF_CORE_TOOL_SCHEMA_TOOLSETS: frozenset[str] = frozenset(
    {
        "terminal",
        "file",
        "memory",
        "session_search",
        "skills",
        "todo",
        "clarify",
        "code_execution",
        "delegation",
        "kanban",
        "web",
        "search",
        "vision",
        "discord",
    }
)

# Default Biff Discord schema profile v2: the smallest safe fixed allowlist
# for Biff's common build/ops lane. It keeps the shell/file/code/skills/memory
# surfaces needed to work Kanban-backed Biff OS tasks (including explicit legacy-tracker access via
# terminal + credential helper), named-role delegation, and todo planning, while
# omitting large or nice-to-have schemas. Operators can still select ``full``
# via config or HERMES_BIFF_TOOL_SCHEMA_PROFILE for rollback/escalation.
BIFF_DISCORD_V2_TOOL_SCHEMA_TOOLSETS: frozenset[str] = frozenset(
    {
        "terminal",
        "file",
        "memory",
        "skills",
        "todo",
        "code_execution",
        "delegation",
        "kanban",
    }
)

# Discord profile v3 is the skill-bundle-era default. It keeps direct build/fix
# capability and memory, but drops high-cost always-on schemas that are better
# escalated explicitly for a specific task: delegation, ad hoc code execution,
# and skill editing.
BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS: frozenset[str] = frozenset(
    {
        "web",
        "search",
        "terminal",
        "file",
        "memory",
        "skills-read",
        "todo",
        "kanban",
    }
)

BIFF_TURN_TOOLSET_PROFILES: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "status": frozenset({"terminal", "file", "kanban"}),
    "terminal": frozenset({"terminal", "file"}),
    "memory": frozenset({"memory", "session_search", "terminal"}),
    "kanban": frozenset({"kanban", "terminal"}),
    "resume": frozenset({"session_search", "terminal", "file", "kanban"}),
    "secondbrain": frozenset({"terminal", "file"}),
    "web": frozenset({"web", "search", "browser", "terminal", "file"}),
    "vision": frozenset({"vision", "file", "terminal"}),
    # Mental-health moment and dashboard ritual routing is intentionally
    # zero-tool at the planner layer: the live turn should present a focused
    # opt-in/handoff surface, not broaden into general agent tools or private
    # source retrieval.
    "mental_health": frozenset(),
    "dashboard": frozenset(),
    "base": BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS,
    "specialist": BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS,
    "command": BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS,
}

BIFF_BUNDLE_TOOLSET_ESCALATIONS: dict[str, frozenset[str]] = {
    "biff-hermes-runtime-change": frozenset({"code_execution", "delegation", "skills", "web", "vision"}),
    "biff-issue-execution": frozenset({"code_execution", "delegation", "skills", "web", "vision"}),
    "biff-research-to-decision": frozenset({"browser", "session_search", "web", "vision"}),
    "biff-memory-knowledge-governance": frozenset({"session_search", "skills", "web"}),
    "biff-automation-ownership": frozenset({"code_execution", "cronjob", "delegation", "web"}),
    "biff-personal-logistics": frozenset({"browser", "web", "vision"}),
}

_BUNDLE_INVOCATION_RE = re.compile(r'user has invoked the "([^"]+)" skill bundle', re.IGNORECASE)


def extract_biff_bundle_key(message: Any) -> str | None:
    """Return the selected Biff bundle key embedded in an invocation message."""

    match = _BUNDLE_INVOCATION_RE.search(str(message or ""))
    if not match:
        return None
    key = match.group(1).strip().lstrip("/")
    return key or None


def _biff_platform_cfg(config: Mapping[str, Any] | None, platform_key: str | None) -> Mapping[str, Any]:
    cfg = config if isinstance(config, Mapping) else {}
    biff_cfg = cfg.get("biff") if isinstance(cfg.get("biff"), Mapping) else {}
    platforms = biff_cfg.get("platforms") if isinstance(biff_cfg.get("platforms"), Mapping) else {}
    platform_cfg = platforms.get(platform_key) if isinstance(platforms.get(platform_key), Mapping) else {}
    return platform_cfg


def _first_platform_value(platform_cfg: Mapping[str, Any], keys: Iterable[str], default: Any) -> Any:
    for key in keys:
        if key in platform_cfg:
            return platform_cfg.get(key)
    return default


def resolve_biff_live_tool_guardrail_settings(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
    *,
    message: Any = None,
) -> dict[str, Any]:
    """Resolve live Discord tool guardrails for ordinary chat vs delivery work.

    The regular Discord lane stays intentionally small so casual answers do not
    sprawl.  Selected Biff bundles, especially Forge runtime work, need enough
    room to complete build/restart/verification steps before reporting done.
    """

    bundle_key = extract_biff_bundle_key(message)
    is_forge_direct = bundle_key == "biff-hermes-runtime-change"
    is_issue_execution = bundle_key == "biff-issue-execution"
    platform_cfg = _biff_platform_cfg(config, platform_key)
    route_action = None
    route_runtime = None
    if not bundle_key and str(platform_key or "").strip().lower() == "discord":
        try:
            from agent.biff_intent_router import plan_biff_turn

            route_plan = plan_biff_turn(message, command=False)
            route_action = route_plan.action
            route_runtime = route_plan.runtime
        except Exception:
            route_action = None
            route_runtime = None

    if is_forge_direct:
        timeout_default = 60
    elif is_issue_execution:
        timeout_default = 45
    elif route_action in {"forge_direct", "ranger_direct", "quill_direct", "vex_direct"}:
        timeout_default = 45
    elif route_action in {"kanban_status", "kanban_admin"}:
        timeout_default = 20
    elif route_action == "secondbrain_lookup":
        timeout_default = 10
    elif route_action == "route_bundle":
        timeout_default = 45
    elif route_action in {"quick_web", "vision_analyze", "one_tool"}:
        timeout_default = 20
    else:
        timeout_default = 15
    if is_forge_direct:
        timeout_keys = ("forge_chat_terminal_timeout",)
    elif is_issue_execution:
        timeout_keys = ("issue_execution_chat_terminal_timeout",)
    elif route_action == "forge_direct":
        timeout_keys = ("forge_direct_chat_terminal_timeout",)
    elif route_action == "ranger_direct":
        timeout_keys = ("ranger_direct_chat_terminal_timeout", "kanban_chat_terminal_timeout")
    elif route_action == "quill_direct":
        timeout_keys = ("quill_direct_chat_terminal_timeout", "route_bundle_chat_terminal_timeout", "chat_terminal_timeout")
    elif route_action == "vex_direct":
        timeout_keys = ("vex_direct_chat_terminal_timeout", "route_bundle_chat_terminal_timeout", "chat_terminal_timeout")
    elif route_action in {"kanban_status", "kanban_admin"}:
        timeout_keys = ("kanban_chat_terminal_timeout", "one_tool_chat_terminal_timeout")
    elif route_action == "secondbrain_lookup":
        timeout_keys = ("secondbrain_chat_terminal_timeout", "one_tool_chat_terminal_timeout")
    elif route_action == "route_bundle":
        timeout_keys = ("route_bundle_chat_terminal_timeout", "chat_terminal_timeout")
    elif route_action in {"quick_web", "vision_analyze"}:
        timeout_keys = ("quick_web_chat_terminal_timeout",)
    elif route_action == "one_tool":
        timeout_keys = ("one_tool_chat_terminal_timeout",)
    elif route_action == "answer_now":
        timeout_keys = ("answer_chat_terminal_timeout",)
    else:
        timeout_keys = ("chat_terminal_timeout",)
    timeout_raw = _first_platform_value(platform_cfg, timeout_keys, timeout_default)
    try:
        terminal_timeout = max(5, min(180, int(timeout_raw)))
    except Exception:
        terminal_timeout = timeout_default

    if is_forge_direct:
        tool_keys = ("forge_chat_max_tool_calls",)
    elif is_issue_execution:
        tool_keys = ("issue_execution_chat_max_tool_calls",)
    elif route_action == "forge_direct":
        tool_keys = ("forge_direct_chat_max_tool_calls",)
    elif route_action == "ranger_direct":
        tool_keys = ("ranger_direct_chat_max_tool_calls", "kanban_chat_max_tool_calls")
    elif route_action == "quill_direct":
        tool_keys = ("quill_direct_chat_max_tool_calls", "route_bundle_chat_max_tool_calls")
    elif route_action == "vex_direct":
        tool_keys = ("vex_direct_chat_max_tool_calls", "route_bundle_chat_max_tool_calls")
    elif route_action in {"kanban_status", "kanban_admin"}:
        tool_keys = ("kanban_chat_max_tool_calls", "one_tool_chat_max_tool_calls")
    elif route_action == "secondbrain_lookup":
        tool_keys = ("secondbrain_chat_max_tool_calls", "one_tool_chat_max_tool_calls")
    elif route_action == "route_bundle":
        tool_keys = ("route_bundle_chat_max_tool_calls", "bundle_chat_max_tool_calls")
    elif route_action in {"quick_web", "vision_analyze"}:
        tool_keys = ("quick_web_chat_max_tool_calls",)
    elif route_action == "one_tool":
        tool_keys = ("one_tool_chat_max_tool_calls",)
    elif route_action == "answer_now":
        tool_keys = ("answer_chat_max_tool_calls",)
    else:
        tool_keys = ("bundle_chat_max_tool_calls",) if bundle_key else ("chat_max_tool_calls",)
    if is_forge_direct:
        tool_default = 80
    elif is_issue_execution:
        tool_default = 60
    elif bundle_key:
        tool_default = 20
    elif route_action == "background":
        tool_default = 1
    elif route_action == "forge_direct":
        tool_default = 24
    elif route_action == "ranger_direct":
        tool_default = 24
    elif route_action == "quill_direct":
        tool_default = 24
    elif route_action == "vex_direct":
        tool_default = 24
    elif route_action == "kanban_status":
        tool_default = 3
    elif route_action == "kanban_admin":
        tool_default = 6
    elif route_action == "secondbrain_lookup":
        tool_default = 1
    elif route_action == "route_bundle":
        tool_default = 36
    elif route_action == "quick_web":
        tool_default = 4
    elif route_action == "vision_analyze":
        tool_default = 3
    elif route_action == "one_tool":
        tool_default = 2
    elif route_action == "resume_context":
        tool_default = 3
    elif route_action == "answer_now":
        tool_default = 1
    else:
        tool_default = 4
    if route_action == "route_bundle" and route_runtime == "continuation":
        tool_default = 16
    tool_raw = _first_platform_value(platform_cfg, tool_keys, tool_default)
    if str(tool_raw).strip().lower() in {"0", "none", "off", "false", "unlimited"}:
        max_tool_calls = None
    else:
        try:
            max_tool_calls = max(1, min(120, int(tool_raw)))
        except Exception:
            max_tool_calls = tool_default

    return {
        "bundle_key": bundle_key,
        "route_action": route_action,
        "terminal_timeout": terminal_timeout,
        "max_tool_calls": max_tool_calls,
    }


def resolve_biff_live_max_iterations(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
    *,
    message: Any = None,
    base_max_iterations: int = 90,
) -> int:
    """Resolve the live Discord iteration cap without starving delivery work."""

    if str(platform_key or "").strip().lower() != "discord":
        return int(base_max_iterations)

    bundle_key = extract_biff_bundle_key(message)
    is_forge_direct = bundle_key == "biff-hermes-runtime-change"
    is_issue_execution = bundle_key == "biff-issue-execution"
    platform_cfg = _biff_platform_cfg(config, platform_key)
    route_action = None
    if not bundle_key:
        try:
            from agent.biff_intent_router import route_biff_live_intent

            route_action = route_biff_live_intent(message, command=False).action
        except Exception:
            route_action = None
    if is_forge_direct:
        iterations_key = "forge_chat_max_iterations"
    elif is_issue_execution:
        iterations_key = "issue_execution_chat_max_iterations"
    elif route_action == "forge_direct":
        iterations_key = "forge_direct_chat_max_iterations"
    elif route_action == "ranger_direct":
        iterations_key = "ranger_direct_chat_max_iterations"
    elif route_action == "quill_direct":
        iterations_key = "quill_direct_chat_max_iterations"
    elif route_action == "vex_direct":
        iterations_key = "vex_direct_chat_max_iterations"
    elif route_action in {"kanban_status", "kanban_admin"}:
        iterations_key = "kanban_chat_max_iterations"
    elif route_action == "secondbrain_lookup":
        iterations_key = "secondbrain_chat_max_iterations"
    elif route_action == "route_bundle":
        iterations_key = "route_bundle_chat_max_iterations"
    elif route_action in {"quick_web", "vision_analyze"}:
        iterations_key = "quick_web_chat_max_iterations"
    elif route_action == "one_tool":
        iterations_key = "one_tool_chat_max_iterations"
    elif route_action == "answer_now":
        iterations_key = "answer_chat_max_iterations"
    else:
        iterations_key = "bundle_chat_max_iterations" if bundle_key else "chat_max_iterations"
    if is_forge_direct:
        iterations_default = 72
    elif is_issue_execution:
        iterations_default = 60
    elif bundle_key:
        iterations_default = 36
    elif route_action == "forge_direct":
        iterations_default = 24
    elif route_action == "ranger_direct":
        iterations_default = 24
    elif route_action == "quill_direct":
        iterations_default = 24
    elif route_action == "vex_direct":
        iterations_default = 24
    elif route_action == "kanban_status":
        iterations_default = 5
    elif route_action == "kanban_admin":
        iterations_default = 8
    elif route_action == "secondbrain_lookup":
        iterations_default = 2
    elif route_action == "route_bundle":
        iterations_default = 48
    elif route_action == "quick_web":
        iterations_default = 6
    elif route_action == "vision_analyze":
        iterations_default = 4
    elif route_action == "one_tool":
        iterations_default = 3
    elif route_action == "answer_now":
        iterations_default = 2
    else:
        iterations_default = 4
    iterations_raw = platform_cfg.get(iterations_key, iterations_default)
    try:
        live_cap = max(2, min(120, int(iterations_raw)))
    except Exception:
        live_cap = iterations_default
    return min(int(base_max_iterations), live_cap)


def biff_bundle_tool_widening_enabled(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
) -> bool:
    if str(platform_key or "").strip().lower() != "discord":
        return False
    env = os.getenv("HERMES_BIFF_BUNDLE_TOOL_WIDENING")
    if env is not None:
        return env.strip().lower() not in {"0", "false", "no", "off"}
    platform_cfg = _biff_platform_cfg(config, platform_key)
    return str(platform_cfg.get("bundle_tool_widening", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def biff_prompt_budget_enabled(config: Mapping[str, Any] | None, platform_key: str | None) -> bool:
    """Return whether ordinary Discord turns should use a live prompt budget."""

    if str(platform_key or "").strip().lower() != "discord":
        return False
    env = os.getenv("HERMES_BIFF_PROMPT_BUDGET")
    if env is not None:
        return env.strip().lower() not in {"0", "false", "no", "off"}
    platform_cfg = _biff_platform_cfg(config, platform_key)
    return str(platform_cfg.get("prompt_budget", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def resolve_biff_prompt_budget_tokens(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
) -> int:
    """Resolve the target prompt budget for ordinary Discord live turns."""

    raw: Any = os.getenv("HERMES_BIFF_PROMPT_BUDGET_TOKENS")
    if raw is None:
        platform_cfg = _biff_platform_cfg(config, platform_key)
        raw = platform_cfg.get("prompt_budget_tokens")
    try:
        value = int(raw or DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS)
    except Exception:
        value = DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS
    return max(
        MIN_BIFF_DISCORD_PROMPT_BUDGET_TOKENS,
        min(MAX_BIFF_DISCORD_PROMPT_BUDGET_TOKENS, value),
    )


def should_apply_biff_prompt_budget(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
    *,
    message: Any = None,
) -> bool:
    """Apply the compact budget only to ordinary live turns.

    Explicit or auto-selected skill bundles intentionally widen context/tools
    for specialist work, so they keep the existing model-facing history after
    tool-output caps.
    """

    if not biff_prompt_budget_enabled(config, platform_key):
        return False
    return extract_biff_bundle_key(message) is None


def widen_biff_toolsets_for_bundle(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
    enabled_toolsets: Iterable[str] | None,
    configured_toolsets: Iterable[str] | None,
    *,
    message: Any = None,
    bundle_key: str | None = None,
) -> list[str]:
    """Add narrowly-scoped specialist toolsets for the selected Biff bundle.

    The v3 Discord profile keeps live chat fast by default.  When bundle
    auto-selection already knows the task is implementation, research, memory,
    or automation work, this restores only the matching specialist surfaces.
    It never grants a toolset that is not configured for the platform.
    """

    current = {str(t) for t in (enabled_toolsets or []) if str(t).strip()}
    configured = {str(t) for t in (configured_toolsets or []) if str(t).strip()}
    if not biff_bundle_tool_widening_enabled(config, platform_key):
        return sorted(current)

    key = (bundle_key or extract_biff_bundle_key(message) or "").strip().lstrip("/")
    additions = BIFF_BUNDLE_TOOLSET_ESCALATIONS.get(key)
    if not additions:
        return sorted(current)

    widened = set(current)
    for toolset in additions:
        if toolset in configured:
            widened.add(toolset)
    if "skills" in widened:
        widened.discard("skills-read")
    return sorted(widened)


SLOW_WORK_KANBAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(archive|migrate|import|export|backfill|sync)\b.*\b(stories|issues|legacy_tracker|obsidian|docs?|history|references?)\b", re.IGNORECASE),
    re.compile(r"\b(search|scan|check|inspect|audit)\b.*\b(all|every|entire|whole)\b.*\b(repo|repository|codebase|workspace|obsidian|mnemosyne|legacy_tracker|stories|references?)\b", re.IGNORECASE),
    re.compile(r"\b(run|fix|execute|work on|implement)\b.*\b(all|entire|whole|backlog|queue|board)\b", re.IGNORECASE),
    re.compile(r"\b(long|large|big|multi[-\s]?step|background)\b.*\b(task|work|migration|audit|cleanup|refactor)\b", re.IGNORECASE),
)

FAST_CHAT_ALLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(status|state|why|what|when|where|should|can|could|do i|is it)\b", re.IGNORECASE),
    re.compile(r"^\s*/", re.IGNORECASE),
)


@dataclass(frozen=True)
class SlowWorkDeflection:
    title: str
    body: str
    assignee: str
    priority: int = 1


def maybe_build_slow_work_deflection(
    text: Any,
    *,
    platform_key: str | None,
    command: bool = False,
) -> SlowWorkDeflection | None:
    """Return a Kanban task spec when a Discord prompt should not block chat."""

    if command or str(platform_key or "").strip().lower() != "discord":
        return None
    prompt = str(text or "").strip()
    if len(prompt) < 40:
        return None
    if any(pattern.search(prompt) for pattern in FAST_CHAT_ALLOW_PATTERNS):
        return None
    if not any(pattern.search(prompt) for pattern in SLOW_WORK_KANBAN_PATTERNS):
        return None

    compact = re.sub(r"\s+", " ", prompt)
    title = compact[:96].rstrip(" .,;:")
    if len(compact) > len(title):
        title = title[:92].rstrip(" .,;:") + "..."
    body = (
        "Created automatically from a live Discord request because it looks like "
        "slow background work. Keep Discord responsive: gather context, perform "
        "the work in Kanban, update the task with plain-language progress, and "
        "report the result back when done.\n\n"
        f"Original request:\n{prompt}"
    )
    return SlowWorkDeflection(title=title, body=body, assignee="ranger", priority=1)


def plain_language_activity_summary(activity: Mapping[str, Any] | None) -> str:
    """Turn low-level agent activity into a user-facing progress phrase."""

    activity = activity if isinstance(activity, Mapping) else {}
    tool = str(activity.get("current_tool") or "").strip().lower()
    desc = str(activity.get("last_activity_desc") or "").strip().lower()
    source = f"{tool} {desc}"
    if any(needle in source for needle in ("npm run build", "vite build", "tsc", "webpack", "build")):
        return "rebuilding the app"
    if any(needle in source for needle in ("pytest", "npm test", "pnpm test", "unit test", "regression")):
        return "checking that the change works"
    if any(needle in source for needle in ("curl", "localhost", "127.0.0.1", "dashboard", "browser")):
        return "checking the dashboard"
    if any(needle in source for needle in ("launchctl", "service", "gateway", "restart", "process")):
        return "checking the running services"
    if any(needle in source for needle in ("kanban", "story", "card", "board")):
        return "updating the work board"
    if any(needle in source for needle in ("patch", "write_file", "edit", "save")):
        return "editing the relevant files"
    if any(needle in source for needle in ("read_file", "search_files", "rg ", "grep", "find")):
        return "finding the relevant files"
    checks = (
        (("terminal", "shell", "command"), "checking the system"),
        (("read", "file", "search"), "looking up the right context"),
        (("write", "patch", "edit"), "applying changes"),
        (("test", "verify"), "checking that the change works"),
        (("web", "http"), "checking external information"),
        (("memory", "mnemosyne", "obsidian"), "checking memory and notes"),
        (("delegate", "subagent", "ranger", "forge", "vex", "quill"), "coordinating a specialist"),
    )
    for needles, phrase in checks:
        if any(needle in source for needle in needles):
            return phrase
    return "working through the request"


def render_plain_language_heartbeat(
    *,
    elapsed_seconds: float,
    activity: Mapping[str, Any] | None = None,
) -> str:
    elapsed_mins = max(1, int(float(elapsed_seconds or 0) // 60))
    summary = plain_language_activity_summary(activity)
    return f"Still working: {summary}. ({elapsed_mins} min elapsed)"


def _normalize_biff_tool_schema_profile(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "": "full",
        "default": "full",
        "normal": "full",
        "all": "full",
        "wide": "full",
        "core": "core",
        "narrow": "v3",
        "lean": "v3",
        "reduced": "v3",
        "biff-core": "core",
        "v2": "v2",
        "profile-v2": "v2",
        "discord-v2": "v2",
        "biff-discord-v2": "v2",
        "v3": "v3",
        "profile-v3": "v3",
        "discord-v3": "v3",
        "biff-discord-v3": "v3",
        "minimal": "v3",
        "essentials": "v3",
    }
    return aliases.get(raw, raw) if aliases.get(raw, raw) in {"full", "core", "v2", "v3"} else "full"


def resolve_biff_tool_schema_profile(config: Mapping[str, Any] | None = None, platform_key: str | None = None) -> str:
    """Resolve Biff's tool-schema profile.

    Default is ``v3`` for Discord Biff turns and ``full`` elsewhere for
    compatibility.  ``core``/``v2``/``v3`` are selectable via
    HERMES_BIFF_TOOL_SCHEMA_PROFILE, biff.platforms.<platform>.tool_schema_profile,
    or biff.tool_schema_profile.
    """

    env_profile = os.getenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE")
    if env_profile:
        return _normalize_biff_tool_schema_profile(env_profile)

    cfg = config if isinstance(config, Mapping) else {}
    biff_cfg = cfg.get("biff") if isinstance(cfg.get("biff"), Mapping) else {}
    profile_value = None
    if platform_key and isinstance(biff_cfg, Mapping):
        platforms = biff_cfg.get("platforms") if isinstance(biff_cfg.get("platforms"), Mapping) else {}
        platform_cfg = platforms.get(platform_key) if isinstance(platforms.get(platform_key), Mapping) else {}
        profile_value = platform_cfg.get("tool_schema_profile") or platform_cfg.get("tools_profile")
    if profile_value is None and isinstance(biff_cfg, Mapping):
        profile_value = biff_cfg.get("tool_schema_profile") or biff_cfg.get("tools_profile")
    if profile_value is None and str(platform_key or "").strip().lower() == "discord":
        return "v3"
    return _normalize_biff_tool_schema_profile(profile_value)


def apply_biff_tool_schema_profile(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
    enabled_toolsets: Iterable[str] | None,
) -> list[str]:
    """Apply Biff's fixed tool-schema narrowing.

    The core/v2 profiles only remove toolsets from the already-configured
    platform selection; they never grant new toolsets. Full-tool escalation is
    preserved by the full profile and the HERMES_BIFF_TOOL_SCHEMA_PROFILE=full
    override.
    """

    original = [str(toolset) for toolset in (enabled_toolsets or []) if str(toolset).strip()]
    profile = resolve_biff_tool_schema_profile(config, platform_key)
    if profile == "v3":
        narrowed = {toolset for toolset in original if toolset in BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS}
        if "skills" in original:
            narrowed.add("skills-read")
        return sorted(narrowed)
    if profile == "v2":
        return sorted({toolset for toolset in original if toolset in BIFF_DISCORD_V2_TOOL_SCHEMA_TOOLSETS})
    if profile != "core":
        return sorted(dict.fromkeys(original))
    return sorted({toolset for toolset in original if toolset in BIFF_CORE_TOOL_SCHEMA_TOOLSETS})


def apply_biff_turn_toolset_plan(
    config: Mapping[str, Any] | None,
    platform_key: str | None,
    enabled_toolsets: Iterable[str] | None,
    *,
    message: Any = None,
    configured_toolsets: Iterable[str] | None = None,
) -> list[str]:
    """Apply the canonical Biff turn planner to toolset exposure.

    This is the second stage after the platform's fixed tool-schema profile:
    first classify the turn, then expose only the runtime surfaces that the
    classification actually needs.  Specialist bundle widening still happens
    after this, so Forge/Quill/Ranger/Vex keep their required tools without
    making casual Discord turns carry those schemas.
    """

    original = [str(toolset) for toolset in (enabled_toolsets or []) if str(toolset).strip()]
    configured = {str(toolset) for toolset in (configured_toolsets or original) if str(toolset).strip()}
    if extract_biff_bundle_key(message):
        return sorted(dict.fromkeys(original))
    try:
        from agent.biff_intent_router import plan_biff_turn

        plan = plan_biff_turn(message, command=False)
    except Exception:
        plan = None
    if plan is None:
        return sorted(dict.fromkeys(original))
    if str(platform_key or "").strip().lower() == "discord":
        try:
            from gateway.biff_toolset_router import (
                biff_toolset_router_enabled,
                select_biff_toolsets_with_router,
            )

            if biff_toolset_router_enabled(config, platform_key):
                decision = select_biff_toolsets_with_router(
                    plan=plan,
                    enabled_toolsets=original,
                    configured_toolsets=configured,
                    profile_toolsets=BIFF_TURN_TOOLSET_PROFILES,
                )
                return list(decision.selected_toolsets)
        except Exception:
            # Router failures must fail open to the already-profiled surface.
            return sorted(dict.fromkeys(original))
    allowed = BIFF_TURN_TOOLSET_PROFILES.get(plan.toolset_profile)
    if allowed is None:
        return sorted(dict.fromkeys(original))
    if str(platform_key or "").strip().lower() != "discord" and allowed:
        return sorted(dict.fromkeys(original))
    selected = {toolset for toolset in original if toolset in allowed}
    # Web/status/board plans may need a narrow toolset that the base v3 profile
    # intentionally removed. Grant only the planner-approved toolsets and only
    # when the platform actually configured them.
    for toolset in allowed:
        if toolset in configured:
            selected.add(toolset)
    return sorted(selected)


def filter_biff_mode_enabled_toolsets(mode: BiffOperatingMode, enabled_toolsets: Iterable[str] | None) -> list[str]:
    """Return toolsets allowed for a Biff operating mode.

    Evidence-only is enforced below the prompt layer by allowing only read-only
    evidence toolsets. This intentionally excludes broad/mutating toolsets such
    as terminal, file, browser, homeassistant, spotify, send/message platform
    controls, and other integration toolsets. Other modes preserve the platform
    tool configuration unchanged.
    """

    original = [str(toolset) for toolset in (enabled_toolsets or []) if str(toolset).strip()]
    if mode.name != "evidence-only":
        return sorted(dict.fromkeys(original))
    return sorted({toolset for toolset in original if toolset in BIFF_EVIDENCE_ONLY_SAFE_TOOLSETS})


_SECRET_LIKE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]{8,}"),
)


@dataclass(frozen=True)
class HygieneCapStats:
    original_messages: int
    capped_messages: int
    content_truncated: int
    tool_outputs_truncated: int

    @property
    def capped(self) -> bool:
        return (
            self.original_messages != self.capped_messages
            or self.content_truncated > 0
            or self.tool_outputs_truncated > 0
        )


@dataclass(frozen=True)
class ToolOutputCapStats:
    tool_outputs_capped_count: int = 0
    tool_output_chars_before: int = 0
    tool_output_chars_after: int = 0
    tool_output_chars_omitted: int = 0
    message_contents_capped_count: int = 0

    @property
    def capped(self) -> bool:
        return self.tool_outputs_capped_count > 0 or self.message_contents_capped_count > 0


@dataclass(frozen=True)
class BiffPromptBudgetStats:
    applied: bool = False
    budget_tokens: int = DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS
    budget_chars: int = DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS * CHARS_PER_TOKEN_ESTIMATE
    original_messages: int = 0
    kept_messages: int = 0
    omitted_messages: int = 0
    original_chars: int = 0
    kept_chars: int = 0
    overhead_chars: int = 0
    reason: str = "within_budget"


SESSION_QUOTA_THRESHOLDS: tuple[int, ...] = (40_000, 70_000, 100_000, 130_000)
DISCORD_SLOWDOWN_GUARD_MIN_MODE = "emergency"
DISCORD_SLOWDOWN_GUARD_USER_CHARS = 50_000
DISCORD_SLOWDOWN_GUARD_ASSISTANT_CHARS = 50_000
DISCORD_SLOWDOWN_GUARD_TOOL_CHARS = 250_000
DISCORD_SLOWDOWN_GUARD_HISTORY_MESSAGES = 160


@dataclass(frozen=True)
class SessionQuotaRecommendation:
    """Low-noise recommendation surfaced when a session grows large.

    Advisory only: callers must not change providers/models, discard context,
    auto-reset, or mutate Cockpit state from this object.
    """

    threshold: int
    level: str
    prompt_tokens: int
    text: str
    dedupe_key: str
    context_length: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "threshold": self.threshold,
            "level": self.level,
            "prompt_tokens": self.prompt_tokens,
            "text": self.text,
            "dedupe_key": self.dedupe_key,
        }
        if self.context_length:
            payload["context_length"] = self.context_length
        return payload


def session_quota_threshold_for_tokens(prompt_tokens: int | None) -> int | None:
    """Return the highest crossed quota/session threshold, if any."""

    try:
        tokens = int(prompt_tokens or 0)
    except (TypeError, ValueError):
        return None
    crossed = [threshold for threshold in SESSION_QUOTA_THRESHOLDS if tokens >= threshold]
    return crossed[-1] if crossed else None


def build_session_quota_recommendation(
    *,
    session_id: str,
    prompt_tokens: int | None,
    context_length: int | None = None,
    warned_thresholds: Iterable[int] | None = None,
) -> SessionQuotaRecommendation | None:
    """Build an appendable session-size recommendation.

    Uses API/provider-reported prompt tokens (``last_prompt_tokens``) where
    available.  ``warned_thresholds`` lets persistent callers warn at most once
    per threshold per session; callers without persistence can use the returned
    ``dedupe_key`` for deterministic de-duplication.
    """

    threshold = session_quota_threshold_for_tokens(prompt_tokens)
    if threshold is None:
        return None
    warned = {int(value) for value in (warned_thresholds or []) if value is not None}
    if threshold in warned:
        return None

    tokens = int(prompt_tokens or 0)
    session_part = session_id or "unknown"
    dedupe_key = f"session-quota:{session_part}:{threshold}"
    token_label = f"{tokens:,}"
    if threshold == 40_000:
        level = "heads_up"
        guidance = "Heads-up: this session is getting large; safe to keep going, but a fresh session after this task may be cleaner."
    elif threshold == 70_000:
        level = "recommend"
        guidance = "Recommendation: finish the current thread, then reset/start a fresh session to reduce drift and overflow risk."
    elif threshold == 100_000:
        level = "strong_economy"
        guidance = "Strong recommendation: use economy mode for non-critical follow-ups and reset/start fresh soon."
    else:
        level = "urgent"
        guidance = "Urgent: start a fresh session soon after active work lands; do not auto-reset or discard context mid-task."

    return SessionQuotaRecommendation(
        threshold=threshold,
        level=level,
        prompt_tokens=tokens,
        context_length=int(context_length) if context_length else None,
        dedupe_key=dedupe_key,
        text=f"Session quota {level.replace('_', ' ')} ({token_label} prompt tokens): {guidance}",
    )


def apply_discord_slowdown_guard(
    mode: BiffOperatingMode,
    history: Iterable[Mapping[str, Any]] | None,
    *,
    platform_key: str | None,
    message: Any = None,
    enabled: bool = True,
) -> tuple[BiffOperatingMode, dict[str, Any] | None]:
    """Tighten the current Discord turn when active transcript context is bloated.

    This is intentionally model-facing only. It does not mutate transcripts,
    session files, or Mnemosyne memory. The goal is to prevent a long-running
    Discord work session from dragging a huge assistant/tool transcript into
    every future turn.
    """

    if (
        not enabled
        or str(platform_key or "").strip().lower() != "discord"
        or mode.name in {"emergency", "evidence-only"}
    ):
        return mode, None

    bundle_key = extract_biff_bundle_key(message)
    if bundle_key in {"biff-issue-execution", "biff-hermes-runtime-change", "forge-direct-engineering"}:
        return mode, None

    messages = list(history or [])
    user_chars = 0
    assistant_chars = 0
    tool_chars = 0
    for msg in messages:
        if not isinstance(msg, Mapping):
            continue
        role = str(msg.get("role") or "").lower()
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        if role == "user":
            user_chars += len(content)
        elif role == "assistant":
            assistant_chars += len(content)
        elif role in {"tool", "function"}:
            tool_chars += len(content)

    reasons: list[str] = []
    if len(messages) >= DISCORD_SLOWDOWN_GUARD_HISTORY_MESSAGES:
        reasons.append("history_messages")
    if user_chars >= DISCORD_SLOWDOWN_GUARD_USER_CHARS:
        reasons.append("user_chars")
    if assistant_chars >= DISCORD_SLOWDOWN_GUARD_ASSISTANT_CHARS:
        reasons.append("assistant_chars")
    if tool_chars >= DISCORD_SLOWDOWN_GUARD_TOOL_CHARS:
        reasons.append("tool_chars")
    if not reasons:
        return mode, None

    guarded = _BIFF_MODE_SPECS[DISCORD_SLOWDOWN_GUARD_MIN_MODE]
    return guarded, {
        "from_mode": mode.name,
        "to_mode": guarded.name,
        "reasons": reasons,
        "history_messages": len(messages),
        "user_chars": user_chars,
        "assistant_chars": assistant_chars,
        "tool_chars": tool_chars,
    }


def _truncate_text(value: Any, max_chars: int) -> tuple[Any, bool]:
    if not isinstance(value, str) or max_chars <= 0 or len(value) <= max_chars:
        return value, False
    suffix = f"\n\n[...truncated by gateway session hygiene to {max_chars} chars...]"
    keep = max(0, max_chars - len(suffix))
    return value[:keep] + suffix, True


def _truncate_model_facing_message_text(value: Any, max_chars: int, role: str) -> tuple[Any, bool]:
    if not isinstance(value, str) or max_chars <= 0 or len(value) <= max_chars:
        return value, False
    suffix = f"\n\n[...{role or 'message'} content capped by Biff operating mode to {max_chars} chars; full transcript is preserved...]"
    keep = max(0, max_chars - len(suffix))
    return value[:keep] + suffix, True


def cap_hygiene_history(
    history: Iterable[dict[str, Any]],
    *,
    max_messages: int = DEFAULT_HYGIENE_MAX_MESSAGES,
    max_content_chars: int = DEFAULT_HYGIENE_MAX_CONTENT_CHARS,
    max_tool_output_chars: int = DEFAULT_HYGIENE_MAX_TOOL_OUTPUT_CHARS,
) -> tuple[list[dict[str, Any]], HygieneCapStats]:
    """Return a bounded history copy for pre-agent hygiene decisions.

    Keeps the most recent messages, then caps large content fields. Tool outputs
    get a smaller cap because large command/file dumps dominate token estimates
    and slow compression prompts while adding little summary value.
    """

    items = [dict(m) for m in history]
    original_count = len(items)
    if max_messages > 0 and len(items) > max_messages:
        items = items[-max_messages:]

    content_truncated = 0
    tool_outputs_truncated = 0
    for msg in items:
        role = msg.get("role")
        cap = max_tool_output_chars if role in ("tool", "function") else max_content_chars
        new_content, truncated = _truncate_text(msg.get("content"), cap)
        if truncated:
            msg["content"] = new_content
            if role in ("tool", "function"):
                tool_outputs_truncated += 1
            else:
                content_truncated += 1

    return items, HygieneCapStats(
        original_messages=original_count,
        capped_messages=len(items),
        content_truncated=content_truncated,
        tool_outputs_truncated=tool_outputs_truncated,
    )


def _redact_secret_like_values(text: str) -> str:
    redacted = text
    for pattern in _SECRET_LIKE_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET_LIKE_VALUE]", redacted)
    return redacted


def _content_char_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    except Exception:
        return len(str(value))


def _tool_output_marker(
    raw: str,
    *,
    session_id: str,
    message_index: int,
    transcript_ref: str,
    preview_chars: int,
) -> tuple[str, int]:
    preview_chars = max(0, int(preview_chars))
    head = _redact_secret_like_values(raw[:preview_chars])
    tail = _redact_secret_like_values(raw[-preview_chars:] if preview_chars else "")
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    retained_preview_chars = min(len(raw), preview_chars) + min(
        max(0, len(raw) - preview_chars),
        preview_chars,
    )
    omitted_chars = max(0, len(raw) - retained_preview_chars)
    marker = (
        "[Gateway model-facing tool output capped]\n"
        f"original_chars={len(raw)} omitted_chars={omitted_chars} sha256={digest}\n"
        f"session_id={session_id} message_index={message_index} transcript_ref={transcript_ref}\n"
        "The full raw tool output remains stored in the session transcript/history; "
        "retrieve the exact raw tool output with session/file transcript tools using the "
        "session_id, message_index, transcript_ref, and sha256 above before relying on omitted evidence.\n"
        "--- head preview (secret-like values redacted) ---\n"
        f"{head}\n"
        "--- tail preview (secret-like values redacted) ---\n"
        f"{tail}"
    )
    return marker, omitted_chars


_EVIDENCE_PATH_RE = re.compile(r"(?:(?:~|/)[^\s\"'`<>|,;)]+)")


def _summarize_historical_tool_output_marker(
    raw: str,
    *,
    session_id: str,
    message_index: int,
    transcript_ref: str,
) -> tuple[str, int]:
    """Return a compact marker for older tool output beyond aggregate budget."""

    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    summary = _redact_secret_like_values(first_line[:240])
    paths = []
    for match in _EVIDENCE_PATH_RE.finditer(raw):
        path = match.group(0).rstrip(".:")
        if path not in paths:
            paths.append(path)
        if len(paths) >= 8:
            break
    paths_line = ", ".join(paths) if paths else "none detected"
    marker = (
        "[Gateway model-facing historical tool output summarized]\n"
        f"original_chars={len(raw)} omitted_chars={len(raw)} sha256={digest}\n"
        f"session_id={session_id} message_index={message_index} transcript_ref={transcript_ref}\n"
        f"summary={summary or '[no text summary available]'}\n"
        f"evidence_paths={paths_line}\n"
        "The full raw tool output remains stored in the session transcript/history; "
        "retrieve it with session_id, message_index, transcript_ref, and sha256 before relying on omitted evidence."
    )
    return marker, len(raw)


def _fit_tool_output_marker_to_budget(marker: str, max_chars: int) -> str:
    """Return a marker no longer than max_chars, preserving leading handles.

    Normal Biff budgets are large enough for every compact marker. This guard
    keeps the aggregate cap strict even under unusually tiny configured budgets.
    """

    max_chars = int(max_chars)
    if max_chars <= 0:
        return ""
    if len(marker) <= max_chars:
        return marker
    suffix = "\n[marker truncated to fit aggregate tool-output budget]"
    if max_chars <= len(suffix):
        return marker[:max_chars]
    return marker[: max_chars - len(suffix)] + suffix


def _adaptive_total_tool_output_budget(capped_candidate_count: int) -> int:
    """Return the default aggregate model-facing tool-output budget.

    Keep the historical 48k floor for ordinary turns. When a Discord/Biff
    session has many historical tool outputs that require model-facing capping,
    fixed 48k markers become the prompt floor; step the default down while the
    marker algorithm still preserves newest retrieval handles first.
    """

    count = int(capped_candidate_count or 0)
    if count >= 64:
        return ADAPTIVE_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS_EXTREME
    if count >= 32:
        return ADAPTIVE_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS_HEAVY
    if count >= 16:
        return ADAPTIVE_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS_MANY
    return DEFAULT_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS


def cap_model_facing_tool_outputs(
    history: Iterable[Mapping[str, Any]],
    *,
    session_id: str,
    transcript_ref: str,
    max_tool_output_chars: int = DEFAULT_MODEL_FACING_TOOL_OUTPUT_CHARS,
    max_total_tool_output_chars: int | None = None,
    preview_chars: int = DEFAULT_TOOL_PREVIEW_CHARS,
    max_message_content_chars: int | None = None,
) -> tuple[list[dict[str, Any]], ToolOutputCapStats]:
    """Cap large historical tool/function contents for the model-facing copy only.

    The returned messages preserve roles, tool_call_id, tool_calls, and all other
    fields except capped tool/function ``content``.  The input transcript/history
    object is never mutated, so raw evidence remains retrievable out of context.
    """

    capped_history = [copy.deepcopy(dict(msg)) for msg in history]
    capped_count = 0
    message_capped_count = 0
    chars_before = 0
    chars_after = 0
    omitted_total = 0
    aggregate_budget_enabled = max_total_tool_output_chars is None or int(max_total_tool_output_chars or 0) > 0
    total_budget = int(max_total_tool_output_chars or 0)
    tool_entries: list[dict[str, Any]] = []

    for index, msg in enumerate(capped_history):
        transcript_message_index = msg.pop("_transcript_message_index", index)
        role = msg.get("role")
        if role not in ("tool", "function"):
            if max_message_content_chars and role in ("user", "assistant"):
                new_content, truncated = _truncate_model_facing_message_text(
                    msg.get("content"),
                    int(max_message_content_chars),
                    str(role or "message"),
                )
                if truncated:
                    msg["content"] = new_content
                    message_capped_count += 1
            continue

        content = msg.get("content")
        if not isinstance(content, str):
            chars_after += _content_char_len(content)
            continue

        if not aggregate_budget_enabled:
            if len(content) <= max_tool_output_chars:
                chars_after += len(content)
                continue
            marker, omitted_chars = _tool_output_marker(
                content,
                session_id=session_id,
                message_index=transcript_message_index,
                transcript_ref=transcript_ref,
                preview_chars=preview_chars,
            )
            msg["content"] = marker
            capped_count += 1
            chars_before += len(content)
            chars_after += len(marker)
            omitted_total += omitted_chars
            continue

        summary_marker, summary_omitted = _summarize_historical_tool_output_marker(
            content,
            session_id=session_id,
            message_index=transcript_message_index,
            transcript_ref=transcript_ref,
        )
        if len(content) <= max_tool_output_chars:
            preferred_marker = content
            preferred_omitted = 0
        else:
            preferred_marker, preferred_omitted = _tool_output_marker(
                content,
                session_id=session_id,
                message_index=transcript_message_index,
                transcript_ref=transcript_ref,
                preview_chars=preview_chars,
            )
        tool_entries.append(
            {
                "msg": msg,
                "raw": content,
                "summary": summary_marker,
                "summary_omitted": summary_omitted,
                "preferred": preferred_marker,
                "preferred_omitted": preferred_omitted,
            }
        )

    if aggregate_budget_enabled and tool_entries:
        if max_total_tool_output_chars is None:
            large_candidate_count = sum(
                1 for entry in tool_entries if len(entry["raw"]) > int(max_tool_output_chars or 0)
            )
            raw_tool_output_total = sum(len(entry["raw"]) for entry in tool_entries)
            capped_candidate_count = large_candidate_count
            if raw_tool_output_total > DEFAULT_MODEL_FACING_TOTAL_TOOL_OUTPUT_CHARS and len(tool_entries) >= 32:
                capped_candidate_count = max(capped_candidate_count, len(tool_entries))
            total_budget = _adaptive_total_tool_output_budget(capped_candidate_count)
        # Reserve a compact retrieval marker for every string tool/function
        # message first. Then spend any surplus from newest to oldest to keep
        # recent raw small outputs or richer head/tail previews. This makes the
        # aggregate cap strict for both raw outputs and replacement markers.
        base_total = sum(len(entry["summary"]) for entry in tool_entries)
        final_by_entry: dict[int, tuple[str, int]] = {}
        if base_total > total_budget:
            remaining = total_budget
            for entry_pos in range(len(tool_entries) - 1, -1, -1):
                entry = tool_entries[entry_pos]
                marker = _fit_tool_output_marker_to_budget(entry["summary"], remaining)
                final_by_entry[entry_pos] = (marker, entry["summary_omitted"])
                remaining -= len(marker)
            for entry_pos in range(len(tool_entries)):
                final_by_entry.setdefault(entry_pos, ("", tool_entries[entry_pos]["summary_omitted"]))
        else:
            surplus = total_budget - base_total
            for entry_pos, entry in enumerate(tool_entries):
                final_by_entry[entry_pos] = (entry["summary"], entry["summary_omitted"])
            for entry_pos in range(len(tool_entries) - 1, -1, -1):
                entry = tool_entries[entry_pos]
                extra = len(entry["preferred"]) - len(entry["summary"])
                if extra <= surplus:
                    final_by_entry[entry_pos] = (entry["preferred"], entry["preferred_omitted"])
                    surplus -= max(0, extra)

        for entry_pos, entry in enumerate(tool_entries):
            final_content, final_omitted = final_by_entry[entry_pos]
            entry["msg"]["content"] = final_content
            raw_content = entry["raw"]
            chars_after += len(final_content)
            if final_content != raw_content:
                capped_count += 1
                chars_before += len(raw_content)
                omitted_total += final_omitted

    return capped_history, ToolOutputCapStats(
        tool_outputs_capped_count=capped_count,
        tool_output_chars_before=chars_before,
        tool_output_chars_after=chars_after,
        tool_output_chars_omitted=omitted_total,
        message_contents_capped_count=message_capped_count,
    )


def _message_model_chars(message: Mapping[str, Any]) -> int:
    try:
        return len(json.dumps(message, ensure_ascii=False, default=str))
    except Exception:
        return len(str(dict(message)))


def _history_model_chars(history: Iterable[Mapping[str, Any]]) -> int:
    return sum(_message_model_chars(msg) for msg in history if isinstance(msg, Mapping))


def _copy_budget_message(message: Mapping[str, Any]) -> dict[str, Any]:
    clean = copy.deepcopy(dict(message))
    clean.pop("_transcript_message_index", None)
    return clean


def _append_budget_group(
    groups: list[list[dict[str, Any]]],
    group: list[dict[str, Any]],
) -> None:
    if group:
        groups.append(group)


def _group_agent_history_for_budget(history: Iterable[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group assistant tool calls with their tool results to preserve API shape."""

    items = [_copy_budget_message(msg) for msg in history if isinstance(msg, Mapping)]
    groups: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(items):
        msg = items[index]
        role = str(msg.get("role") or "").lower()
        if role == "assistant" and msg.get("tool_calls"):
            group = [msg]
            index += 1
            while index < len(items) and str(items[index].get("role") or "").lower() in {"tool", "function"}:
                group.append(items[index])
                index += 1
            _append_budget_group(groups, group)
            continue
        if role in {"tool", "function"}:
            # Orphan tool messages are invalid without their assistant call.
            index += 1
            continue
        _append_budget_group(groups, [msg])
        index += 1
    return groups


def _truncate_budget_text_message(message: Mapping[str, Any], max_chars: int) -> dict[str, Any] | None:
    if max_chars <= 120:
        return None
    if str(message.get("role") or "").lower() not in {"user", "assistant"}:
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content:
        return None
    suffix = "\n\n[...older message shortened for the live Discord prompt budget; full transcript is preserved...]"
    keep = max(0, max_chars - len(suffix) - 80)
    if keep <= 0:
        return None
    trimmed = _copy_budget_message(message)
    trimmed["content"] = content[-keep:] + suffix
    return trimmed


def apply_biff_prompt_budget(
    history: Iterable[Mapping[str, Any]],
    *,
    budget_tokens: int = DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS,
    system_context_prompt: Any = "",
    channel_prompt: Any = "",
    tool_schema_chars: int = 0,
) -> tuple[list[dict[str, Any]], BiffPromptBudgetStats]:
    """Return a smaller model-facing history for ordinary Discord live turns.

    This function never mutates the saved transcript. It keeps newest context,
    preserves assistant/tool-call grouping, and adds a short note when older
    model-facing history is omitted. Durable continuity still comes from the
    hot context capsule, Mnemosyne, Obsidian, Kanban, and the raw transcript.
    """

    try:
        resolved_budget_tokens = int(budget_tokens or DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS)
    except Exception:
        resolved_budget_tokens = DEFAULT_BIFF_DISCORD_PROMPT_BUDGET_TOKENS
    budget_tokens = max(
        MIN_BIFF_DISCORD_PROMPT_BUDGET_TOKENS,
        min(MAX_BIFF_DISCORD_PROMPT_BUDGET_TOKENS, resolved_budget_tokens),
    )
    budget_chars = budget_tokens * CHARS_PER_TOKEN_ESTIMATE
    items = [_copy_budget_message(msg) for msg in history if isinstance(msg, Mapping)]
    overhead_chars = (
        _content_char_len(system_context_prompt)
        + _content_char_len(channel_prompt)
        + int(tool_schema_chars or 0)
    )
    original_chars = _history_model_chars(items) + overhead_chars
    if original_chars <= budget_chars:
        return items, BiffPromptBudgetStats(
            budget_tokens=budget_tokens,
            budget_chars=budget_chars,
            original_messages=len(items),
            kept_messages=len(items),
            original_chars=original_chars,
            kept_chars=original_chars,
            overhead_chars=overhead_chars,
        )

    notice = {
        "role": "user",
        "content": (
            "[System note: Older Discord history was trimmed from this live prompt "
            "for speed. Full transcript, Kanban, Obsidian, and Mnemosyne memory "
            "remain available when needed.]"
        ),
    }
    notice_chars = _message_model_chars(notice)
    history_budget = max(0, budget_chars - overhead_chars - notice_chars)
    kept_reversed: list[list[dict[str, Any]]] = []
    kept_chars = 0
    omitted_messages = 0
    groups = _group_agent_history_for_budget(items)
    for group in reversed(groups):
        group_chars = _history_model_chars(group)
        if kept_chars + group_chars <= history_budget:
            kept_reversed.append(group)
            kept_chars += group_chars
            continue
        remaining = history_budget - kept_chars
        if remaining > 120 and len(group) == 1:
            trimmed = _truncate_budget_text_message(group[0], remaining)
            if trimmed is not None:
                kept_reversed.append([trimmed])
                kept_chars += _message_model_chars(trimmed)
                continue
        omitted_messages += len(group)

    kept: list[dict[str, Any]] = []
    for group in reversed(kept_reversed):
        kept.extend(group)
    if omitted_messages or len(kept) < len(items):
        kept.insert(0, notice)

    final_chars = _history_model_chars(kept) + overhead_chars
    omitted = max(0, len(items) - len(kept) + 1) if kept and kept[0] is notice else max(0, len(items) - len(kept))
    return kept, BiffPromptBudgetStats(
        applied=True,
        budget_tokens=budget_tokens,
        budget_chars=budget_chars,
        original_messages=len(items),
        kept_messages=len(kept),
        omitted_messages=omitted,
        original_chars=original_chars,
        kept_chars=final_chars,
        overhead_chars=overhead_chars,
        reason="over_budget",
    )


def collect_token_source_metrics(
    original_history: Iterable[Mapping[str, Any]],
    model_facing_history: Iterable[Mapping[str, Any]],
    cap_stats: ToolOutputCapStats,
    *,
    system_context_prompt: Any = "",
    channel_prompt: Any = "",
    tool_schema_chars: int = 0,
) -> dict[str, int]:
    """Summarize token-source character counts without message contents."""

    metrics = {
        "history_user_chars": 0,
        "history_assistant_chars": 0,
        "history_tool_output_chars_before_cap": 0,
        "history_tool_output_chars_after_cap": 0,
        "tool_output_chars_omitted": int(cap_stats.tool_output_chars_omitted),
        "tool_outputs_capped_count": int(cap_stats.tool_outputs_capped_count),
        "history_message_contents_capped_count": int(cap_stats.message_contents_capped_count),
        "tool_schema_chars": int(tool_schema_chars or 0),
        "system_context_prompt_chars": _content_char_len(system_context_prompt),
        "channel_prompt_chars": _content_char_len(channel_prompt),
    }

    for msg in original_history:
        role = msg.get("role")
        chars = _content_char_len(msg.get("content"))
        if role == "user":
            metrics["history_user_chars"] += chars
        elif role == "assistant":
            metrics["history_assistant_chars"] += chars
        elif role in ("tool", "function"):
            metrics["history_tool_output_chars_before_cap"] += chars

    for msg in model_facing_history:
        if msg.get("role") in ("tool", "function"):
            metrics["history_tool_output_chars_after_cap"] += _content_char_len(msg.get("content"))

    return metrics
