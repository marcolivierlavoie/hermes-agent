import { test } from "node:test";
import { strict as assert } from "node:assert";

import {
  selectRitualSuggestions,
  THINKING_PATTERNS,
  type RitualSuggestionAnswer,
} from "../src/lib/ritualSuggestionProvider.ts";

const answer = (questionIndex: number, value: string | null, skipped = false): RitualSuggestionAnswer => ({
  questionIndex,
  answer: value,
  skipped,
});

test("offers sanitized habit-start chips for the arrival question", () => {
  const result = selectRitualSuggestions({ questionIndex: 0, answers: [answer(0, "SENSITIVE FREE TEXT SITUATION")] });

  assert.equal(result.reason, "habit-start-chips");
  assert.ok(result.suggestions.includes("After I open this page, I will take one slow breath"));
  assert.ok(result.suggestions.includes("If this feels like too much, I will do one breath and stop"));
  assert.equal(JSON.stringify(result).includes("SENSITIVE FREE TEXT"), false);
});

test("offers known thinking patterns for the pattern question", () => {
  const result = selectRitualSuggestions({ questionIndex: 2, answers: [] });

  assert.deepEqual(result.suggestions, THINKING_PATTERNS);
  assert.equal(result.reason, "known-pattern-list");
  assert.deepEqual(result.sanitizedRecentAnswerSignals, []);
});

test("selects known cue suggestions from a sanitized prior pattern answer", () => {
  const result = selectRitualSuggestions({
    questionIndex: 3,
    answers: [answer(2, "I am definitely catastrophizing about a private work thing")],
  });

  assert.equal(result.pattern, "catastrophizing");
  assert.equal(result.reason, "known-pattern-cues");
  assert.ok(result.suggestions.includes("My mind jumps straight to the worst outcome"));
  assert.deepEqual(result.sanitizedRecentAnswerSignals, [
    { questionIndex: 2, skipped: false, empty: false, hasAnswer: true, knownPattern: "catastrophizing" },
  ]);
  assert.equal(JSON.stringify(result).includes("private work thing"), false);
});

test("offers balanced thought chips without echoing the raw prior concern", () => {
  const result = selectRitualSuggestions({
    questionIndex: 4,
    answers: [answer(3, "SENSITIVE FREE TEXT CONCERN ABOUT A NAMED PERSON")],
  });

  assert.equal(result.reason, "balanced-thought-chips");
  assert.ok(result.suggestions.includes("A balanced thought: this is hard, and I can choose one next rep"));
  assert.equal(JSON.stringify(result).includes("SENSITIVE FREE TEXT CONCERN"), false);
});

test("uses cold-start defaults when cue/reset questions have no known prior pattern", () => {
  const cueResult = selectRitualSuggestions({ questionIndex: 3, answers: [] });
  const resetResult = selectRitualSuggestions({ questionIndex: 5, answers: [answer(2, "custom private pattern")] });

  assert.equal(cueResult.reason, "cold-start-default-cues");
  assert.equal(cueResult.pattern, "default");
  assert.ok(cueResult.suggestions.includes("My body gets tight before I have the facts"));
  assert.equal(resetResult.reason, "deterministic-default-reset");
  assert.ok(resetResult.suggestions.includes("Pause; name the pattern; take one slow breath; choose one next-right action"));
  assert.equal(JSON.stringify(resetResult).includes("custom private pattern"), false);
});

test("builds reset suggestions without echoing raw cue text", () => {
  const result = selectRitualSuggestions({
    questionIndex: 5,
    answers: [
      answer(2, "mind-reading"),
      answer(3, "SENSITIVE FREE TEXT CUE ABOUT A NAMED PERSON"),
    ],
  });

  assert.equal(result.pattern, "mind-reading");
  assert.equal(result.reason, "known-pattern-reset-with-cue-signal");
  assert.equal(result.sanitizedRecentAnswerSignals[result.sanitizedRecentAnswerSignals.length - 1]?.hasAnswer, true);
  assert.equal(JSON.stringify(result).includes("SENSITIVE FREE TEXT CUE"), false);
  assert.ok(result.suggestions[0]?.includes("the selected cue"));
});

test("offers evidence capture CTA chips that stay local and consent-based", () => {
  const result = selectRitualSuggestions({ questionIndex: 6, answers: [answer(4, "SENSITIVE FREE TEXT BALANCED THOUGHT")] });

  assert.equal(result.reason, "evidence-capture-chips");
  assert.ok(result.suggestions.includes("Capture one private evidence point only if useful"));
  assert.ok(result.suggestions.includes("No capture today; closing the loop still counts"));
  assert.equal(JSON.stringify(result).includes("SENSITIVE FREE TEXT BALANCED"), false);
});

test("returns deterministic empty fallback for unsupported questions", () => {
  const first = selectRitualSuggestions({ questionIndex: 99, answers: [answer(0, "private text")] });
  const second = selectRitualSuggestions({ questionIndex: 99, answers: [answer(0, "different private text")] });

  assert.deepEqual(first.suggestions, []);
  assert.equal(first.reason, "no-suggestions-for-question");
  assert.deepEqual(first, second);
});
