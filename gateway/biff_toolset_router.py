"""Feature-flagged Biff toolset-router seam.

This module is intentionally pure and default-off. It provides the architecture
seam for adopting an upstream Tool Router without changing live Discord behavior
until the flag is enabled and synthetic parity checks pass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


_DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
_ENABLED_VALUES = {"1", "true", "yes", "on", "enabled"}
_CONSERVATIVE_ACTIONS = {"route_bundle", "command"}
_CONSERVATIVE_RUNTIMES = {"workflow", "continuation", "tool_access_recovery", "command", "specialist_work"}


@dataclass(frozen=True)
class BiffToolsetRouteDecision:
    """Toolset selection decision produced by the feature-flagged router."""

    route_class: str
    selected_toolsets: tuple[str, ...]
    fallback: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_class": self.route_class,
            "selected_toolsets": list(self.selected_toolsets),
            "fallback": self.fallback,
            "reason": self.reason,
        }


def _platform_cfg(config: Mapping[str, Any] | None, platform_key: str | None) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    raw_biff = cfg.get("biff")
    biff_cfg: Mapping[str, Any] = raw_biff if isinstance(raw_biff, Mapping) else {}
    raw_platforms = biff_cfg.get("platforms")
    platforms: Mapping[str, Any] = raw_platforms if isinstance(raw_platforms, Mapping) else {}
    raw_platform_cfg = platforms.get(str(platform_key or ""))
    return raw_platform_cfg if isinstance(raw_platform_cfg, Mapping) else {}


def biff_toolset_router_enabled(config: Mapping[str, Any] | None, platform_key: str | None) -> bool:
    """Return whether the new router seam should enforce toolset decisions.

    Default is disabled. The flag can be enabled with either:
    - HERMES_BIFF_TOOLSET_ROUTER=1
    - biff.platforms.<platform>.toolset_router: true

    A platform-scoped false value wins over absent config only when no env var is
    set. Non-Discord platforms stay disabled for this Biff-specific adoption
    slice.
    """

    if str(platform_key or "").strip().lower() != "discord":
        return False
    env = os.getenv("HERMES_BIFF_TOOLSET_ROUTER")
    if env is not None:
        return env.strip().lower() in _ENABLED_VALUES
    platform_cfg = _platform_cfg(config, platform_key)
    raw = platform_cfg.get("toolset_router", platform_cfg.get("use_toolset_router", False))
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _ENABLED_VALUES


def _sorted_unique(values: Iterable[str] | None) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(str(value) for value in (values or []) if str(value).strip())))


def route_class_for_plan(plan: Any) -> str:
    """Map a BiffTurnPlan-like object to the router's route class."""

    action = str(getattr(plan, "action", "") or "").strip()
    runtime = str(getattr(plan, "runtime", "") or "").strip()
    profile = str(getattr(plan, "toolset_profile", "") or "").strip()
    if action in _CONSERVATIVE_ACTIONS or runtime in _CONSERVATIVE_RUNTIMES:
        return "conservative_full"
    if profile:
        return profile
    return "conservative_full"


def select_biff_toolsets_with_router(
    *,
    plan: Any,
    enabled_toolsets: Iterable[str] | None,
    configured_toolsets: Iterable[str] | None,
    profile_toolsets: Mapping[str, Iterable[str]],
) -> BiffToolsetRouteDecision:
    """Select toolsets from a plan using conservative upstream-router semantics.

    The router never grants a toolset outside the configured platform ceiling.
    Uncertain, long, complex, workflow, continuation, command, and specialist
    lanes deliberately fall back to the incoming surface instead of narrowing.
    That preserves DeepSeek/provider fallback and role-consent behavior while
    the router is trace/enforcement-tested behind a flag.
    """

    original = _sorted_unique(enabled_toolsets)
    configured = set(_sorted_unique(configured_toolsets or original))
    route_class = route_class_for_plan(plan)

    if route_class == "conservative_full":
        return BiffToolsetRouteDecision(
            route_class=route_class,
            selected_toolsets=original,
            fallback=True,
            reason="conservative fallback for workflow/continuation/command/specialist uncertainty",
        )

    allowed = profile_toolsets.get(route_class)
    if allowed is None:
        return BiffToolsetRouteDecision(
            route_class="conservative_full",
            selected_toolsets=original,
            fallback=True,
            reason=f"unknown route class {route_class!r}; preserving incoming tool surface",
        )

    allowed_set = {str(toolset) for toolset in allowed if str(toolset).strip()}
    selected = {toolset for toolset in original if toolset in allowed_set}
    for toolset in allowed_set:
        if toolset in configured:
            selected.add(toolset)
    return BiffToolsetRouteDecision(
        route_class=route_class,
        selected_toolsets=_sorted_unique(selected),
        fallback=False,
        reason=f"selected toolsets for route class {route_class}",
    )
