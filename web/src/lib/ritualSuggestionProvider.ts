export const THINKING_PATTERNS = [
  "mind-reading",
  "catastrophizing",
  "all-or-nothing thinking",
  "should-ing",
  "discounting positives",
] as const;

export type ThinkingPattern = (typeof THINKING_PATTERNS)[number];
export type SuggestionPattern = ThinkingPattern | "default";

export type RitualSuggestionAnswer = {
  questionIndex: number;
  answer?: string | null;
  skipped?: boolean;
};

export type SanitizedRecentAnswerSignal = {
  questionIndex: number;
  skipped: boolean;
  empty: boolean;
  hasAnswer?: true;
  knownPattern?: SuggestionPattern;
};

export type RitualSuggestionReason =
  | "habit-start-chips"
  | "agency-sort-chips"
  | "known-pattern-list"
  | "known-pattern-cues"
  | "known-pattern-reset"
  | "known-pattern-reset-with-cue-signal"
  | "cold-start-default-cues"
  | "cold-start-default-reset"
  | "deterministic-default-cues"
  | "deterministic-default-reset"
  | "balanced-thought-chips"
  | "evidence-capture-chips"
  | "no-suggestions-for-question";

export type RitualSuggestionResult = {
  suggestions: string[];
  pattern: SuggestionPattern;
  reason: RitualSuggestionReason;
  sanitizedRecentAnswerSignals: SanitizedRecentAnswerSignal[];
};

const HABIT_START_SUGGESTIONS = [
  "After I open this page, I will take one slow breath",
  "When the daily cue appears, I will answer just this prompt",
  "If this feels like too much, I will do one breath and stop",
] as const;

const AGENCY_SORT_SUGGESTIONS = [
  "Control: one next choice I can make",
  "Influence: one clean ask or preparation step",
  "No-control: one outcome I can release for now",
] as const;

const CUE_SUGGESTIONS: Record<SuggestionPattern, string[]> = {
  "mind-reading": ["I am assuming what someone thinks before asking", "I start treating silence as evidence", "I replay a message looking for hidden meaning"],
  catastrophizing: ["My mind jumps straight to the worst outcome", "I feel urgency before I have facts", "I use always/never language about what will happen"],
  "all-or-nothing thinking": ["I call the day a win or a failure", "One imperfect moment starts feeling like the whole story", "I notice only two options"],
  "should-ing": ["My sentence starts with I should or I have to", "I turn a preference into a rule", "I feel guilty before checking what matters"],
  "discounting positives": ["I explain away something that went well", "A compliment or win feels like it does not count", "I skip over evidence that I am trying"],
  default: ["My body gets tight before I have the facts", "I hear a familiar harsh phrase", "The loop repeats more than twice"],
};

const BALANCED_THOUGHT_SUGGESTIONS = [
  "A balanced thought: this is hard, and I can choose one next rep",
  "A balanced thought: I do not need the whole answer to take a kind next step",
  "A balanced thought: uncertainty is present, and one useful action is still available",
] as const;

const RESET_SUGGESTIONS: Record<SuggestionPattern, string[]> = {
  "mind-reading": ["Pause; name mind-reading; ask for one real fact or one clean question", "Hand on chest; say maybe, not proven; wait for evidence"],
  catastrophizing: ["Pause; name catastrophizing; ask what is the next safe step", "Exhale longer than inhale; write the most likely outcome"],
  "all-or-nothing thinking": ["Pause; name all-or-nothing; find the 10% middle", "Say both/and once, then choose one small next move"],
  "should-ing": ["Pause; name should-ing; swap should for I choose or I prefer", "Ask whose rule this is, then pick the kind next step"],
  "discounting positives": ["Pause; name discounting positives; count one thing that still counts", "Put one real receipt beside the worry before moving on"],
  default: ["Pause; name the pattern; take one slow breath; choose one next-right action", "Feet on floor; label the loop; return to one controllable step"],
};

const EVIDENCE_CAPTURE_SUGGESTIONS = [
  "Capture one private evidence point only if useful",
  "Save only a mechanism label and one public-safe receipt",
  "No capture today; closing the loop still counts",
] as const;

const PATTERN_ALIASES: Record<ThinkingPattern, string[]> = {
  "mind-reading": ["mind-reading", "mind reading", "assuming what", "assuming someone thinks"],
  catastrophizing: ["catastrophizing", "catastrophising", "catastrophe", "worst outcome", "worst case"],
  "all-or-nothing thinking": ["all-or-nothing", "all or nothing", "black and white", "win or failure"],
  "should-ing": ["should-ing", "shoulding", "i should", "have to", "supposed to"],
  "discounting positives": ["discounting positives", "discount positives", "does not count", "doesn't count", "ignore positives"],
};

const PATTERN_QUESTION_INDEX = 2;
const CUE_QUESTION_INDEX = 3;

function normalizePattern(value: string | null | undefined): SuggestionPattern {
  const lower = value?.toLowerCase() ?? "";
  if (!lower.trim()) return "default";

  for (const pattern of THINKING_PATTERNS) {
    if (PATTERN_ALIASES[pattern].some((alias) => lower.includes(alias))) return pattern;
  }

  return "default";
}

function answerFor(answers: RitualSuggestionAnswer[], questionIndex: number): RitualSuggestionAnswer | undefined {
  for (let index = answers.length - 1; index >= 0; index -= 1) {
    if (answers[index]?.questionIndex === questionIndex) return answers[index];
  }
  return undefined;
}

function sanitizeAnswer(answer: RitualSuggestionAnswer): SanitizedRecentAnswerSignal {
  const text = typeof answer.answer === "string" ? answer.answer.trim() : "";
  const signal: SanitizedRecentAnswerSignal = {
    questionIndex: answer.questionIndex,
    skipped: answer.skipped === true,
    empty: text.length === 0,
  };

  if (text.length > 0 && !signal.skipped) signal.hasAnswer = true;
  if (answer.questionIndex === PATTERN_QUESTION_INDEX) signal.knownPattern = normalizePattern(text);

  return signal;
}

function recentSignals(answers: RitualSuggestionAnswer[]): SanitizedRecentAnswerSignal[] {
  return answers.slice(-3).map(sanitizeAnswer);
}

function result(
  suggestions: readonly string[],
  pattern: SuggestionPattern,
  reason: RitualSuggestionReason,
  sanitizedRecentAnswerSignals: SanitizedRecentAnswerSignal[],
): RitualSuggestionResult {
  return {
    suggestions: [...suggestions],
    pattern,
    reason,
    sanitizedRecentAnswerSignals,
  };
}

export function selectRitualSuggestions(input: {
  questionIndex: number;
  answers: readonly RitualSuggestionAnswer[];
}): RitualSuggestionResult {
  const answers = [...input.answers];
  const sanitizedRecentAnswerSignals = recentSignals(answers);

  if (input.questionIndex === 0) {
    return result(HABIT_START_SUGGESTIONS, "default", "habit-start-chips", sanitizedRecentAnswerSignals);
  }

  if (input.questionIndex === 1) {
    return result(AGENCY_SORT_SUGGESTIONS, "default", "agency-sort-chips", sanitizedRecentAnswerSignals);
  }

  if (input.questionIndex === PATTERN_QUESTION_INDEX) {
    return result(THINKING_PATTERNS, "default", "known-pattern-list", sanitizedRecentAnswerSignals);
  }

  if (input.questionIndex === CUE_QUESTION_INDEX) {
    const pattern = normalizePattern(answerFor(answers, PATTERN_QUESTION_INDEX)?.answer);
    if (pattern !== "default") return result(CUE_SUGGESTIONS[pattern], pattern, "known-pattern-cues", sanitizedRecentAnswerSignals);
    const hasPatternAttempt = Boolean(answerFor(answers, PATTERN_QUESTION_INDEX));
    return result(CUE_SUGGESTIONS.default, pattern, hasPatternAttempt ? "deterministic-default-cues" : "cold-start-default-cues", sanitizedRecentAnswerSignals);
  }

  if (input.questionIndex === 4) {
    return result(BALANCED_THOUGHT_SUGGESTIONS, "default", "balanced-thought-chips", sanitizedRecentAnswerSignals);
  }

  if (input.questionIndex === 5) {
    const pattern = normalizePattern(answerFor(answers, PATTERN_QUESTION_INDEX)?.answer);
    const cueAnswer = answerFor(answers, CUE_QUESTION_INDEX);
    const hasCueSignal = Boolean(cueAnswer && cueAnswer.skipped !== true && typeof cueAnswer.answer === "string" && cueAnswer.answer.trim());
    const base = RESET_SUGGESTIONS[pattern];
    const suggestions = hasCueSignal ? [`Pause; name ${pattern === "default" ? "the pattern" : pattern}; reset when I notice the selected cue`, ...base] : base;

    if (pattern !== "default") return result(suggestions, pattern, hasCueSignal ? "known-pattern-reset-with-cue-signal" : "known-pattern-reset", sanitizedRecentAnswerSignals);
    const hasPatternAttempt = Boolean(answerFor(answers, PATTERN_QUESTION_INDEX));
    return result(suggestions, pattern, hasPatternAttempt ? "deterministic-default-reset" : "cold-start-default-reset", sanitizedRecentAnswerSignals);
  }

  if (input.questionIndex === 6) {
    return result(EVIDENCE_CAPTURE_SUGGESTIONS, "default", "evidence-capture-chips", sanitizedRecentAnswerSignals);
  }

  return result([], "default", "no-suggestions-for-question", []);
}
