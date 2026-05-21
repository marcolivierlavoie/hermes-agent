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
from dataclasses import dataclass
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


_BIFF_MODE_SPECS: dict[str, BiffOperatingMode] = {
    "normal": BiffOperatingMode("normal", "Normal", "Full Biff behavior with default context/tool-output hygiene only.", None, DEFAULT_MODEL_FACING_TOOL_OUTPUT_CHARS, None, DEFAULT_TOOL_PREVIEW_CHARS),
    "economy": BiffOperatingMode("economy", "Economy", "Preserve behavior while reducing accidental context/tool/history bloat for non-critical turns.", 40, 8_000, 16_000, 800),
    "emergency": BiffOperatingMode("emergency", "Emergency", "Keep only essential context and use a short tool loop for urgent quota pressure.", 16, 4_000, 8_000, 500),
    "evidence-only": BiffOperatingMode("evidence-only", "Evidence-only", "Gather/check evidence and summarize; avoid side-effecting actions unless already explicitly approved.", 8, 2_000, 4_000, 350),
}


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


def biff_operating_mode_prompt(mode: BiffOperatingMode) -> str:
    if mode.name == "normal":
        return ""
    base = (
        f"[System note: Active Biff operating mode is {mode.label}. "
        "Do not change provider, model, account, persona, memory behavior, safety gates, or source-of-truth policy. "
        "Be more concise and avoid accidental context/tool/history bloat. "
    )
    if mode.name == "economy":
        return base + "Use tools only when they materially improve correctness; prefer bounded reads and focused evidence.]"
    if mode.name == "emergency":
        return base + "Prioritize the smallest safe action that answers the ask; defer nice-to-have exploration.]"
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
# bounded code/delegation, and read-only evidence surfaces.  It excludes large
# nice-to-have or higher-risk fixed schemas (browser, cronjob, image/tts,
# messaging, kanban) unless the operator explicitly selects the full profile.
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
        "web",
        "search",
        "vision",
        "discord",
    }
)

# Default Biff Discord schema profile v2: the smallest safe fixed allowlist
# for Biff's common build/ops lane. It keeps the shell/file/code/skills/memory
# surfaces needed to work Linear-backed BIFs (including Linear access via
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
    }
)


def _normalize_biff_tool_schema_profile(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "": "full",
        "default": "full",
        "normal": "full",
        "all": "full",
        "wide": "full",
        "core": "core",
        "narrow": "core",
        "lean": "core",
        "reduced": "core",
        "biff-core": "core",
        "v2": "v2",
        "profile-v2": "v2",
        "discord-v2": "v2",
        "biff-discord-v2": "v2",
        "minimal": "v2",
        "essentials": "v2",
    }
    return aliases.get(raw, raw) if aliases.get(raw, raw) in {"full", "core", "v2"} else "full"


def resolve_biff_tool_schema_profile(config: Mapping[str, Any] | None = None, platform_key: str | None = None) -> str:
    """Resolve Biff's tool-schema profile.

    Default is ``v2`` for Discord Biff turns and ``full`` elsewhere for
    compatibility.  ``core``/``v2`` are selectable via
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
        return "v2"
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
    if profile == "v2":
        return sorted({toolset for toolset in original if toolset in BIFF_DISCORD_V2_TOOL_SCHEMA_TOOLSETS})
    if profile != "core":
        return sorted(dict.fromkeys(original))
    return sorted({toolset for toolset in original if toolset in BIFF_CORE_TOOL_SCHEMA_TOOLSETS})


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


SESSION_QUOTA_THRESHOLDS: tuple[int, ...] = (40_000, 70_000, 100_000, 130_000)


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
