"""Canonical cheap planner for Biff live-chat turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent.biff_bundle_selector import is_direct_question_without_action


_SLOW_WORK_RE = re.compile(
    r"\b(archive|migrate|import|export|backfill|sync|scan|audit|inspect|search|implement|fix|work on)\b"
    r".*\b(all|every|entire|whole|backlog|board|repo|repository|workspace|legacy_tracker|obsidian|stories|references?)\b",
    re.IGNORECASE,
)
_BROAD_VERIFICATION_RE = re.compile(
    r"\b(?:verify|validate|check|qa|audit|test)\b"
    r".*\b(?:all|every|entire|whole|full|broad|multi[-\s]?system|workspace|repo|repository|board|backlog|cards?|stories|issues|services?|systems?)\b"
    r"|\b(?:all|every|entire|whole|full|broad|multi[-\s]?system|workspace|repo|repository|board|backlog|cards?|stories|issues|services?|systems?)\b"
    r".*\b(?:verify|validate|check|qa|audit|test)\b",
    re.IGNORECASE,
)
_BOARD_ADMIN_RE = re.compile(
    r"\b(?:create|add|write|make|open|reopen|close|keep|leave|mark|move|update|comment|archive|triage|sort|groom|administer|manage)\b"
    r".*\b(?:K-\d+|story|stories|card|cards|kanban|board|ticket|tickets|issue|issues|task|tasks|backlog)\b"
    r"|\b(?:K-\d+|story|stories|card|cards|kanban|board|ticket|tickets|issue|issues|task|tasks|backlog)\b"
    r".*\b(?:create|write|make|open|reopen|close|keep|leave|mark|move|update|comment|archive|triage|sort|groom|administer|manage)\b",
    re.IGNORECASE,
)
_ONE_TOOL_RE = re.compile(
    r"\b(check|status|state|verify|is .* running|gateway|service|launchd|logs?)\b",
    re.IGNORECASE,
)
_TOOL_ACCESS_RECOVERY_RE = re.compile(
    r"\b(?:why\s+(?:do|don'?t|does|doesn'?t)\s+(?:you|biff)\s+(?:not\s+)?have|you\s+(?:do\s+not|don'?t|cannot|can'?t)\s+have|missing|lost|restore|recover|give\s+(?:yourself|you)|enable|use)\b"
    r".{0,120}\b(?:tools?|tool\s+access|terminal|shell|file\s+tools?|kanban|memory[-\s]?only|repo\s+tools?)\b"
    r"|\b(?:tools?|tool\s+access|terminal|shell|file\s+tools?|kanban|memory[-\s]?only|repo\s+tools?)\b"
    r".{0,120}\b(?:missing|lost|unavailable|blocked|restore|recover|enable|give\s+(?:yourself|you)|why\s+(?:do|don'?t|does|doesn'?t)\s+(?:you|biff))\b",
    re.IGNORECASE,
)
_VEX_QA_RE = re.compile(
    r"\b(?:vex|qa|quality\s+assurance|test|tests|testing|validate|validation|verify|verification|smoke\s+test|regression|reproduce|repro|audit|adversarial|break\s+it|check\s+whether\s+it\s+works)\b",
    re.IGNORECASE,
)
_VEX_STRONG_QA_RE = re.compile(
    r"\b(?:vex|qa|quality\s+assurance|validation|smoke\s+test|regression|reproduce|repro|adversarial|break\s+it|check\s+whether\s+it\s+works)\b",
    re.IGNORECASE,
)
_QUILL_DOC_RE = re.compile(
    r"\b(?:quill|document|documentation|docs?|write\s+up|runbook|obsidian|mnemosyne|memory|research|investigate|summari[sz]e|summary|notes?|decision\s+record|adr)\b",
    re.IGNORECASE,
)
_IMPLEMENTATION_ACTION_RE = re.compile(
    r"\b(?:implement|fix|change|patch|configure|install|restart|delete|remove|make|build|deploy|ship|debug)\b",
    re.IGNORECASE,
)
_QUICK_WEB_RE = re.compile(
    r"\b("
    r"look\s*(?:it|this|that)?\s*up|search\s+(?:the\s+)?web|google|online|"
    r"(?:can\s+you\s+)?(?:see|open|read|inspect|check)\s+(?:this|that|the)?\s*(?:link|url|thread|post|page|site)|"
    r"current|latest|today|recent|near\s+me|open\s+now|"
    r"deals?|sale|coupon|price|prices|availability|"
    r"score|scores|live\s+score|game|match|fixture|standings|schedule"
    r")\b",
    re.IGNORECASE,
)
_RIGHT_NOW_WEB_RE = re.compile(
    r"\b(?:"
    r"(?:what'?s|what\s+is|who'?s|who\s+is|where'?s|where\s+is)\b.{0,80}\bright\s+now\b|"
    r"\b(?:happening|trending|available|open|on\s+sale|price|prices|score|weather|traffic)\b.{0,80}\bright\s+now\b|"
    r"\bright\s+now\b.{0,80}\b(?:near\s+me|open|available|score|weather|traffic|price|prices)\b"
    r")",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_VISION_REQUEST_RE = re.compile(
    r"\b(?:vision[_\s-]?analy[sz]e|analy[sz]e\s+(?:this\s+)?(?:image|photo|picture|screenshot|attachment)|"
    r"look\s+at\s+(?:this\s+)?(?:image|photo|picture|screenshot|attachment)|"
    r"read\s+(?:this\s+)?(?:image|photo|picture|screenshot|attachment)|"
    r"what(?:'s|\s+is)\s+(?:in|on)\s+(?:this\s+)?(?:image|photo|picture|screenshot|attachment)|"
    r"grant\s+(?:yourself|you)\s+vision|give\s+(?:yourself|you)\s+vision|"
    r"(?:missing|lost|restore|recover|enable|use)\s+vision)\b",
    re.IGNORECASE,
)
_MEMORY_TOOL_RE = re.compile(
    r"\b(?:remember\s+this|save\s+this\s+(?:to|in)\s+(?:memory|mnemosyne)|what\s+do\s+you\s+remember|"
    r"recall\s+(?:memory|mnemosyne)|memory\s+(?:check|lookup|recall)|mnemosyne\s+(?:recall|memory|candidate))\b",
    re.IGNORECASE,
)
_TERMINAL_TOOL_RE = re.compile(
    r"\b(?:what\s+(?:time|date)\s+is\s+it|current\s+(?:time|date)|today'?s\s+date|"
    r"calculate|compute|arithmetic|checksum|sha256|hash|base64|git\s+(?:status|diff|log|branch)|"
    r"run\s+(?:the\s+)?(?:tests?|pytest|lint|typecheck)|pytest|system\s+state|disk\s+space|ports?)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(implement|fix|change|patch|create|write|save|remember|forget|add|update|archive|migrate|sync|run|continue|finish|complete|close|debug|deploy|configure|install|delete|remove|make|build|execute|proceed|ship|work on|get it done|let me know|document|specify|triage|do)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_ACTION_RE = re.compile(
    r"^\s*(?:"
    r"do\s+(?:it(?:\s+properly(?:\s+now)?)?|this|that|properly(?:\s+now)?|\d+|option\s+\d+)|"
    r"do\s+what\s+you\s+have\s+to\s+do|"
    r"continue|keep\s+going|go\s+ahead|proceed|execute|ship\s+it|get\s+it\s+done|"
    r"(?:finish|complete|close)\s+(?:BIF-)?\d{3,6}|"
    r"work\s+on\s+something\s+else|let\s+me\s+know\s+when\s+(?:it'?s\s+)?done|"
    r"(?:now\s+)?continue\s+and\s+don'?t\s+stop\s+until\s+done"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_FOLLOW_UP_ACTION_LEAD_RE = re.compile(
    r"^\s*(?:"
    r"do\s+(?:it|this|that|what\s+you\s+have\s+to\s+do|properly|option\s+\d+|\d+)|"
    r"continue|keep\s+going|go\s+ahead|proceed|execute|ship\s+it|get\s+it\s+done|"
    r"finish|complete|close|fix\s+it|make\s+it\s+happen"
    r")\b",
    re.IGNORECASE,
)
_REFRESH_RESUME_RE = re.compile(
    r"("
    r"\b(?:chat|tab|window|dashboard|browser|page|ui)\b.*\b(?:refresh(?:ed)?|reload(?:ed)?|closed|lost|disappear(?:ed)?)\b"
    r"|\b(?:refresh(?:ed)?|reload(?:ed)?|closed)\b.*\b(?:chat|tab|window|dashboard|browser|page|ui)\b"
    r"|\b(?:can'?t|cannot|don'?t)\s+(?:see|find)\s+(?:your\s+)?(?:progress|context|status|work)\b"
    r"|\b(?:where\s+did\s+we\s+leave\s+off|what\s+were\s+we\s+(?:doing|discussing|working\s+on))\b"
    r"|\b(?:resume|continue|pick\s+up)\s+(?:this\s+)?(?:conversation|thread|context|non[-\s]?kanban\s+thing)\b"
    r"|\bpick\s+up\s+the\s+non[-\s]?kanban\s+thing\b"
    r"|^\s*resume\s*[.!?]*\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_RESTART_DONE_FOLLOWUP_RE = re.compile(
    r"^\s*(?:"
    r"(?:i(?:'ll|\s+will)\s+(?:just\s+)?say\s+)?restart\s+(?:is\s+)?done|"
    r"(?:gateway\s+)?restart(?:ed)?\s+(?:is\s+)?done|"
    r"(?:i(?:'ve|\s+have)?\s+)?restart(?:ed)?\s+(?:the\s+)?gateway|"
    r"(?:gateway\s+)?restarted"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_REPLY_FIX_FOLLOWUP_RE = re.compile(
    r"^\s*\[Replying to:.*\b(?:what\s+do\s+you\s+suggest\s+we\s+do\s+to\s+fix\s+this|fix\s+this)\b",
    re.IGNORECASE | re.DOTALL,
)
_EXPLICIT_SPECIALIST_RE = re.compile(
    # Named-role handoff must include an explicit dispatch/assignment verb.
    # Bare role mentions, "for Ranger", "Vex should verify", and preference/
    # correction language are conversation, not authorization to dispatch.
    r"\b(?:use|ask|have)\s+(?P<role>forge|ranger|quill|vex)\b"
    r"|\b(?:send|route|dispatch|delegate)\s+(?:this|it|that|work|task|story|issue|card|prompt|message)?(?:\s+\w+){0,4}\s+(?:to|through)\s+(?P<role2>forge|ranger|quill|vex)\b"
    r"|\bhand(?:\s+this|\s+it|\s+that)?\s+off\s+to\s+(?P<role3>forge|ranger|quill|vex)\b"
    r"|\brun\s+(?P<role4>forge|ranger|quill|vex)(?:\s*(?:->|→|then)\s*(?:forge|ranger|quill|vex))*\b",
    re.IGNORECASE,
)
_SPECIALIST_CONTROL_RE = re.compile(
    r"\b(?:stop|cancel|kill|interrupt|pause|halt|shut\s+down)\b"
    r".*\b(?:forge|ranger|quill|vex|specialist|subagent|worker|background\s+task)\b"
    r"|\b(?:forge|ranger|quill|vex|specialist|subagent|worker|background\s+task)\b"
    r".*\b(?:stop|cancel|kill|interrupt|pause|halt|shut\s+down)\b",
    re.IGNORECASE,
)
_SPECIALIST_REVIEW_BEFORE_SEND_RE = re.compile(
    r"\b(?:message|prompt|note|instruction)s?\s+for\s+(?:forge|ranger|quill|vex)\b"
    r".*\b(?:read|review|check|look\s+at)\b.*\b(?:before|instead\s+of)\b.*\b(?:send|sending|route|routing|hand(?:ing)?\s+off)\b"
    r"|\b(?:read|review|check|look\s+at)\b.*\b(?:before|instead\s+of)\b.*\b(?:send|sending|route|routing|hand(?:ing)?\s+off)\b"
    r".*\b(?:forge|ranger|quill|vex)\b",
    re.IGNORECASE,
)
_SPECIALIST_MISROUTE_FEEDBACK_RE = re.compile(
    r"\b(?:you|biff)\b.*\b(?:sent|routed|handed|dispatched|delegated)\b"
    r".*\b(?:question|prompt|message|this|that|it)?\b.*\b(?:to|through)\s+(?:forge|ranger|quill|vex)\b"
    r"|\b(?:why\s+did\s+you|did\s+you)\b.*\b(?:send|route|hand\s+off|dispatch|delegate)\b"
    r".*\b(?:to|through)\s+(?:forge|ranger|quill|vex)\b"
    r"|\b(?:don'?t|do\s+not|stop)\b.*\b(?:send|route|hand\s+off|dispatch|delegate)\b"
    r".*\b(?:to|through)\s+(?:forge|ranger|quill|vex)\b",
    re.IGNORECASE,
)
_FORGE_DIRECT_RE = re.compile(
    r"\b("
    r"implement|fix|patch|debug|configure|install|test|verify|restart|delete|remove|"
    r"gateway|hermes|biff|forge|runtime|repo|repository|code|bug|error|"
    r"stacktrace|traceback|launchd|config(?:\.yaml)?|skill(?:[-\s]?bundle)?|"
    r"dashboard|frontend|backend|sidebar|navigation|nav|page|route|react|tsx|vite|web/src|source|files?|"
    r"api|model|openai|node[-\s]?red|workflow|flow|classifier"
    r")\b",
    re.IGNORECASE,
)
_RANGER_TASK_CORRECTION_RE = re.compile(
    r"\b(?:task|story|card|issue|ticket)\b.*\b(?:for\s+ranger|ranger\b.*\bnot\s+forge\b|not\s+forge\b.*\branger)"
    r"|\b(?:for\s+ranger|ranger\b.*\bnot\s+forge\b|not\s+forge\b.*\branger)\b.*\b(?:task|story|card|issue|ticket)\b",
    re.IGNORECASE,
)
_NOT_FORGE_RE = re.compile(r"\bnot\s+forge\b", re.IGNORECASE)
_CONCEPTUAL_ENGINEERING_COMMENTARY_RE = re.compile(
    r"\b(?:gateway|runtime|repo|repository|code|bug|dashboard|api|workflow|flow|kanban|dispatcher)\b"
    r".*\b(?:seems?|feels?|looks?|sounds?|might|may|probably|risky|risk|important|stable|overkill|needed)\b"
    r"|\b(?:seems?|feels?|looks?|sounds?|might|may|probably|risky|risk|important|stable|overkill|needed)\b"
    r".*\b(?:gateway|runtime|repo|repository|code|bug|dashboard|api|workflow|flow|kanban|dispatcher)\b",
    re.IGNORECASE,
)
_MENTAL_HEALTH_DECLINE_RE = re.compile(
    r"\b(?:don'?t|do\s+not|no|not)\s+(?:coach|coaching|mental\s+health|therapy)\b"
    r"|\b(?:stay|keep\s+it)\s+(?:tactical|practical)\b"
    r"|\b(?:just|only)\s+(?:help\s+me\s+)?(?:draft|write|tell|give)\b",
    re.IGNORECASE,
)
_RITUAL_ENTRY_RE = re.compile(
    r"\b(?:start|open|launch|show|take\s+me\s+to|handoff\s+to|hand\s+off\s+to)\b"
    r".*\b(?:daily\s+)?(?:mental\s+health\s+)?ritual(?:\s+(?:page|entry|surface|practice))?\b"
    r"|\b(?:daily\s+practice|daily\s+ritual|ritual\s+page|ritual\s+entry)\b",
    re.IGNORECASE,
)
_MENTAL_HEALTH_EXPLICIT_RE = re.compile(
    r"\b(?:coach\s+me\s+through\s+this|distortion\s+check|help\s+me\s+step\s+back|"
    r"i\s+am\s+spiraling|i'm\s+spiraling|i\s+need\s+perspective|mental\s+health\s+check)\b",
    re.IGNORECASE,
)
_AMBIENT_ACTIVATION_RE = re.compile(
    r"(?=.*\b(?:looping|activated|spiral(?:ing)?|ruminating|stuck\s+in\s+a\s+loop)\b)"
    r"(?=.*\b(?:urgent|urgency|hard|everything|overwhelmed|tight|flooded)\b)",
    re.IGNORECASE,
)

_MENTAL_HEALTH_MECHANISM_MAP = (
    "activation-noticing",
    "cognitive-distortion-check",
    "locus-of-control-sort",
    "balanced-thought-reframe",
    "two-minute-agency-step",
    "decline-safe-close",
)


@dataclass(frozen=True)
class BiffIntentRoute:
    action: str
    reason: str
    max_live_tool_calls: int
    allow_bundle_selection: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "max_live_tool_calls": self.max_live_tool_calls,
            "allow_bundle_selection": self.allow_bundle_selection,
        }


@dataclass(frozen=True)
class BiffTurnPlan:
    """Small pre-tool contract for a Discord/Biff turn.

    The gateway should resolve this before loading heavyweight bundles or
    exposing broad tool schemas.  ``action`` is kept compatible with the older
    route object, while the additional fields are the authoritative runtime
    selection contract used by tests and synthetic checks.
    """

    action: str
    reason: str
    runtime: str
    max_live_tool_calls: int
    allow_bundle_selection: bool
    toolset_profile: str
    background: bool = False
    specialist: str | None = None
    requires_current_info: bool = False
    moment_semantics: str | None = None
    mechanism_map: tuple[str, ...] = ()
    dashboard_handoff: str | None = None

    def to_route(self) -> BiffIntentRoute:
        return BiffIntentRoute(
            self.action,
            self.reason,
            self.max_live_tool_calls,
            self.allow_bundle_selection,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "runtime": self.runtime,
            "max_live_tool_calls": self.max_live_tool_calls,
            "allow_bundle_selection": self.allow_bundle_selection,
            "toolset_profile": self.toolset_profile,
            "background": self.background,
            "specialist": self.specialist,
            "requires_current_info": self.requires_current_info,
            "moment_semantics": self.moment_semantics,
            "mechanism_map": list(self.mechanism_map),
            "dashboard_handoff": self.dashboard_handoff,
        }


_KANBAN_STATUS_RE = re.compile(
    r"\b(?:status|state|say|show|read|check|current(?:ly)?|open|closed|done)\b.*\b(?:K-\d+|BIF-\d+|story|card|kanban|board|ticket|issue)\b"
    r"|\b(?:K-\d+|BIF-\d+|story|card|kanban|board|ticket|issue)\b.*\b(?:status|state|say|show|read|check|current(?:ly)?|open|closed|done)\b",
    re.IGNORECASE,
)
_KANBAN_ADMIN_RE = re.compile(
    r"\b(?:create|add|move|update|edit|admin|assign|reassign|open|close|reopen|block|unblock|link|unlink|comment|archive|delete)\b.*\b(?:K-\d+|BIF-\d+|card|cards|kanban|board|ticket|issue|backlog)\b"
    r"|\b(?:K-\d+|BIF-\d+|card|cards|kanban|board|ticket|issue|backlog)\b.*\b(?:create|add|move|update|edit|admin|assign|reassign|open|close|reopen|block|unblock|link|unlink|comment|archive|delete)\b"
    r"|\b(?:create|move|update|assign|reassign|open|close|reopen|block|unblock|link|unlink|comment|archive|delete)\b.*\bstor(?:y|ies)\b"
    r"|\bstor(?:y|ies)\b.*\b(?:move|update|assign|reassign|open|close|reopen|block|unblock|link|unlink|comment|archive|delete|to\s+(?:todo|ready|doing|done|blocked))\b",
    re.IGNORECASE,
)
_KANBAN_BARE_STATUS_RE = re.compile(
    r"^\s*(?:check|show|read|status|state|open|closed|done|where(?:'s|\s+is)|what(?:'s|\s+is)(?:\s+up\s+with)?|tell\s+me\s+about)\s+"
    r"(?:the\s+)?(?:story|card|ticket|issue)?\s*(?:#\s*)?(?:K-|BIF-)?\d{3,5}\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_KANBAN_BARE_ADMIN_RE = re.compile(
    r"^\s*(?:move|update|edit|assign|reassign|open|close|reopen|block|unblock|link|unlink|comment|archive|delete|keep)\s+"
    r"(?:the\s+)?(?:story|card|ticket|issue)?\s*(?:#\s*)?(?:K-|BIF-)?\d{3,5}\b",
    re.IGNORECASE,
)

def plan_biff_turn(text: Any, *, command: bool = False) -> BiffTurnPlan:
    """Classify a turn before context/tool/skill selection."""

    body = " ".join(str(text or "").strip().split())
    if command:
        return BiffTurnPlan("command", "slash command already has explicit dispatch", "command", 0, True, "command")
    if not body:
        # Discord can generate an empty live turn after a gateway restart,
        # seamless rollover, image-only message, or interrupted-run recovery.
        # Treat that as a continuation surface, not as a no-tool casual reply;
        # otherwise the fresh session may falsely conclude it has only memory
        # tools and stop instead of resuming the active work.
        return BiffTurnPlan(
            "route_bundle",
            "empty/system continuation should preserve execution-capable tools",
            "continuation",
            2,
            False,
            "base",
        )
    if re.fullmatch(r"(?i)\s*(?:hi|hello|hey|yo|sup|thanks|thank you|ok|okay|gm|gn)[.!?\s]*", body):
        return BiffTurnPlan("answer_now", "casual greeting or acknowledgement", "direct_answer", 0, False, "none")
    if _MENTAL_HEALTH_DECLINE_RE.search(body) and (
        _MENTAL_HEALTH_EXPLICIT_RE.search(body)
        or _AMBIENT_ACTIVATION_RE.search(body)
        or re.search(r"\b(?:coach|coaching|mental\s+health|therapy)\b", body, re.IGNORECASE)
    ):
        return BiffTurnPlan(
            "answer_now",
            "moment-coach declined or tactical-only request; keep #hermes in direct practical support, not coaching",
            "direct_answer",
            0,
            False,
            "none",
            moment_semantics="declined_or_tactical",
            mechanism_map=_MENTAL_HEALTH_MECHANISM_MAP,
        )
    if _RITUAL_ENTRY_RE.search(body):
        return BiffTurnPlan(
            "ritual_entry",
            "dashboard ritual-entry handoff to BIF-1425 ritual page",
            "dashboard_ritual_entry",
            0,
            False,
            "dashboard",
            moment_semantics="dashboard_handoff_only",
            mechanism_map=_MENTAL_HEALTH_MECHANISM_MAP,
            dashboard_handoff="BIF-1425 ritual page",
        )
    if _MENTAL_HEALTH_EXPLICIT_RE.search(body):
        return BiffTurnPlan(
            "mental_health_coach",
            "explicit moment-coach trigger routes to decline-safe mental-health moment runtime",
            "mental_health_moment",
            0,
            False,
            "mental_health",
            moment_semantics="decline_safe_opt_in",
            mechanism_map=_MENTAL_HEALTH_MECHANISM_MAP,
        )
    if _AMBIENT_ACTIVATION_RE.search(body):
        return BiffTurnPlan(
            "mental_health_opt_in",
            "ambient high-confidence activation noticing returns opt-in/routing prompt only, not forced coaching",
            "mental_health_routing_prompt",
            0,
            False,
            "mental_health",
            moment_semantics="opt_in_routing_only",
            mechanism_map=_MENTAL_HEALTH_MECHANISM_MAP,
        )
    if _SPECIALIST_CONTROL_RE.search(body):
        return BiffTurnPlan("one_tool", "specialist control request must stay in Biff/controller, not route to the specialist being controlled", "status_read", 2, False, "status")
    if _SPECIALIST_REVIEW_BEFORE_SEND_RE.search(body):
        return BiffTurnPlan("answer_now", "review-before-send request must stay with Biff instead of dispatching to a specialist", "direct_answer", 0, False, "none")
    if _SPECIALIST_MISROUTE_FEEDBACK_RE.search(body):
        return BiffTurnPlan("answer_now", "specialist routing feedback must stay with Biff/controller instead of dispatching to that specialist", "direct_answer", 0, False, "none")
    try:
        from agent.biff_role_consent import detect_explicit_role_handoff

        role_consent = detect_explicit_role_handoff(body)
    except Exception:
        role_consent = None
    if role_consent is not None and role_consent.approved and role_consent.role in {"forge", "ranger", "quill", "vex"}:
        role = role_consent.role
        return BiffTurnPlan(
            f"{role}_direct",
            f"explicit {role.title()} specialist request",
            "specialist_work",
            2,
            False,
            "specialist",
            background=True,
            specialist=role,
        )
    if _TOOL_ACCESS_RECOVERY_RE.search(body):
        return BiffTurnPlan(
            "route_bundle",
            "tool-access recovery request should restore the execution-capable base operator profile before answering",
            "tool_access_recovery",
            4,
            True,
            "base",
        )
    if _REFRESH_RESUME_RE.search(body):
        return BiffTurnPlan(
            "resume_context",
            "refresh/lost-context request should recover recent session and scratch checkpoint state without assuming Kanban",
            "context_resume",
            3,
            False,
            "resume",
        )
    if _VISION_REQUEST_RE.search(body):
        return BiffTurnPlan(
            "vision_analyze",
            "image/attachment vision request needs the narrow vision tool lane",
            "vision_lookup",
            3,
            False,
            "vision",
        )
    if _RESTART_DONE_FOLLOWUP_RE.search(body):
        return BiffTurnPlan(
            "route_bundle",
            "restart-complete follow-up should stay with Biff/controller and continue prior work context",
            "continuation",
            2,
            True,
            "base",
        )
    is_early_kanban_status_read = (
        _KANBAN_STATUS_RE.search(body) or _KANBAN_BARE_STATUS_RE.search(body)
    ) and not _BOARD_ADMIN_RE.search(body)
    is_early_kanban_admin = _KANBAN_ADMIN_RE.search(body) or (
        _BOARD_ADMIN_RE.search(body)
        and re.search(r"\b(?:K-\d+|BIF-\d+|story|stories|card|cards|kanban|board|ticket|issue|backlog)\b", body, re.IGNORECASE)
    ) or _KANBAN_BARE_ADMIN_RE.search(body)
    has_early_broad_quantifier = re.search(r"\b(?:all|every|entire|whole|full|broad|multi[-\s]?system)\b", body, re.IGNORECASE)
    if is_early_kanban_status_read and not has_early_broad_quantifier:
        return BiffTurnPlan("kanban_status", "read-only Kanban/status request", "kanban_read", 2, False, "kanban")
    if is_early_kanban_admin and not has_early_broad_quantifier:
        return BiffTurnPlan("kanban_admin", "bounded Kanban administration request", "kanban_admin", 4, False, "kanban")
    if _MEMORY_TOOL_RE.search(body):
        return BiffTurnPlan("memory_lookup", "memory/Mnemosyne request needs the narrow memory tool lane", "memory_lookup", 2, False, "memory")
    if _TERMINAL_TOOL_RE.search(body):
        return BiffTurnPlan("terminal_lookup", "system/git/test/math/date request needs the narrow terminal tool lane", "terminal_lookup", 2, False, "terminal")
    if _FOLLOW_UP_ACTION_RE.search(body):
        return BiffTurnPlan("route_bundle", "short follow-up should continue prior work context", "continuation", 2, True, "base")
    if _FOLLOW_UP_ACTION_LEAD_RE.search(body):
        if _FORGE_DIRECT_RE.search(body):
            return BiffTurnPlan("route_bundle", "action follow-up should stay in the live Biff turn unless Forge is explicit", "continuation", 4, True, "base")
        return BiffTurnPlan("route_bundle", "action follow-up should continue prior work context", "continuation", 2, True, "base")
    if _REPLY_FIX_FOLLOWUP_RE.search(body) and _FORGE_DIRECT_RE.search(body):
        return BiffTurnPlan("route_bundle", "engineering reply-fix follow-up should stay with Biff unless a specialist handoff is explicit", "continuation", 4, True, "base")
    is_kanban_status_read = (
        _KANBAN_STATUS_RE.search(body) or _KANBAN_BARE_STATUS_RE.search(body)
    ) and not _BOARD_ADMIN_RE.search(body)
    is_kanban_admin = _KANBAN_ADMIN_RE.search(body) or (
        _BOARD_ADMIN_RE.search(body)
        and re.search(r"\b(?:K-\d+|BIF-\d+|story|stories|card|cards|kanban|board|ticket|issue|backlog)\b", body, re.IGNORECASE)
    ) or _KANBAN_BARE_ADMIN_RE.search(body)
    has_broad_quantifier = re.search(r"\b(?:all|every|entire|whole|full|broad|multi[-\s]?system)\b", body, re.IGNORECASE)
    if is_kanban_status_read and not has_broad_quantifier:
        return BiffTurnPlan("kanban_status", "read-only Kanban/status request", "kanban_read", 2, False, "kanban")
    if is_kanban_admin and not has_broad_quantifier:
        return BiffTurnPlan("kanban_admin", "bounded Kanban administration request", "kanban_admin", 4, False, "kanban")
    if _BROAD_VERIFICATION_RE.search(body):
        return BiffTurnPlan("route_bundle", "broad verification should stay with Biff unless Vex handoff is explicit", "workflow", 2, True, "base")
    # Preserve board/archive hygiene routing before SecondBrain RAG broad-match.
    # Prompts like "archive legacy tracker stories and scan Obsidian" belong to Ranger's
    # continuation lane, not Quill's SecondBrain retrieval lane.
    if _SLOW_WORK_RE.search(body) and re.search(
        r"\b(?:backlog|board|kanban|legacy_tracker|stories|story|cards|card|tickets|issues)\b",
        body,
        re.IGNORECASE,
    ):
        return BiffTurnPlan("route_bundle", "broad board/backlog work should stay with Biff unless Ranger handoff is explicit", "workflow", 2, True, "base")
    try:
        from agent.biff_rag_router import classify_biff_rag_request

        rag_decision = classify_biff_rag_request(body)
    except Exception:
        rag_decision = None
    if rag_decision is not None and rag_decision.action == "background":
        return BiffTurnPlan("route_bundle", "broad retrieval/doc work should stay with Biff unless Quill handoff is explicit", "workflow", 2, True, "base")
    if rag_decision is not None and rag_decision.action == "sqlite_fts":
        return BiffTurnPlan(
            "secondbrain_lookup",
            rag_decision.reason,
            "secondbrain_lookup",
            rag_decision.max_live_tool_calls,
            False,
            "secondbrain",
            requires_current_info=False,
        )
    if _RANGER_TASK_CORRECTION_RE.search(body):
        return BiffTurnPlan("route_bundle", "Ranger/task correction mention is not a dispatch request without explicit handoff wording", "workflow", 2, True, "base")
    if _BOARD_ADMIN_RE.search(body):
        return BiffTurnPlan("route_bundle", "Kanban/backlog administration should stay with Biff unless Ranger handoff is explicit", "workflow", 2, True, "base")
    if _URL_RE.search(body) or _QUICK_WEB_RE.search(body) or _RIGHT_NOW_WEB_RE.search(body):
        return BiffTurnPlan("quick_web", "casual web lookup can use a bounded side-lane search", "web_lookup", 3, False, "web", requires_current_info=True)
    if _QUILL_DOC_RE.search(body) and not _IMPLEMENTATION_ACTION_RE.search(body):
        return BiffTurnPlan("route_bundle", "documentation/research/memory work should stay in the live Biff turn unless Quill is explicit", "workflow", 4, True, "base")
    if _VEX_STRONG_QA_RE.search(body):
        return BiffTurnPlan("route_bundle", "QA/validation work should stay in the live Biff turn unless Vex is explicit", "workflow", 4, True, "base")
    if _VEX_QA_RE.search(body) and not _IMPLEMENTATION_ACTION_RE.search(body):
        return BiffTurnPlan("route_bundle", "QA/validation work should stay in the live Biff turn unless Vex is explicit", "workflow", 4, True, "base")
    if _CONCEPTUAL_ENGINEERING_COMMENTARY_RE.search(body):
        return BiffTurnPlan("route_bundle", "engineering/runtime concept mention without explicit assignment should stay with Biff", "workflow", 2, True, "base")
    if _FORGE_DIRECT_RE.search(body) and _ACTION_RE.search(body):
        return BiffTurnPlan("route_bundle", "engineering action should stay with Biff unless Forge handoff is explicit", "workflow", 4, True, "base")
    if _SLOW_WORK_RE.search(body):
        if re.search(r"\b(?:backlog|board|kanban|legacy_tracker|stories|story|cards|card|tickets|issues)\b", body, re.IGNORECASE):
            return BiffTurnPlan("route_bundle", "broad board/backlog work should stay with Biff unless Ranger handoff is explicit", "workflow", 2, True, "base")
        return BiffTurnPlan("route_bundle", "broad slow work should stay with Biff unless a background handoff is explicit", "workflow", 2, True, "base")
    if _ONE_TOOL_RE.search(body) and not _ACTION_RE.search(body):
        return BiffTurnPlan("one_tool", "quick status/check request", "status_read", 1, False, "status")
    if _NOT_FORGE_RE.search(body):
        return BiffTurnPlan("route_bundle", "explicit specialist correction should not force Forge", "workflow", 2, True, "base")
    if is_direct_question_without_action(body):
        return BiffTurnPlan("answer_now", "direct question without action request", "direct_answer", 0, False, "none")
    return BiffTurnPlan("route_bundle", "workflow request may benefit from bundle context", "workflow", 2, True, "base")


def route_biff_live_intent(text: Any, *, command: bool = False) -> BiffIntentRoute:
    """Classify a Discord turn before loading heavyweight bundle context."""

    return plan_biff_turn(text, command=command).to_route()
