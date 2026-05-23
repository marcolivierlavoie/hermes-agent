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

test("offers known thinking patterns for the pattern question", () => {
  const result = selectRitualSuggestions({ questionIndex: 4, answers: [] });

  assert.deepEqual(result.suggestions, THINKING_PATTERNS);
  assert.equal(result.reason, "known-pattern-list");
  assert.deepEqual(result.sanitizedRecentAnswerSignals, []);
});

test("selects known cue suggestions from a sanitized prior pattern answer", () => {
  const result = selectRitualSuggestions({
    questionIndex: 5,
    answers: [answer(4, "I am definitely catastrophizing about a private work thing")],
  });

  assert.equal(result.pattern, "catastrophizing");
  assert.equal(result.reason, "known-pattern-cues");
  assert.ok(result.suggestions.includes("My mind jumps straight to the worst outcome"));
  assert.deepEqual(result.sanitizedRecentAnswerSignals, [
    { questionIndex: 4, skipped: false, empty: false, hasAnswer: true, knownPattern: "catastrophizing" },
  ]);
  assert.equal(JSON.stringify(result).includes("private work thing"), false);
});

test("uses cold-start defaults when cue/reset questions have no known prior pattern", () => {
  const cueResult = selectRitualSuggestions({ questionIndex: 5, answers: [] });
  const resetResult = selectRitualSuggestions({ questionIndex: 6, answers: [answer(4, "custom private pattern")] });

  assert.equal(cueResult.reason, "cold-start-default-cues");
  assert.equal(cueResult.pattern, "default");
  assert.ok(cueResult.suggestions.includes("My body gets tight before I have the facts"));
  assert.equal(resetResult.reason, "deterministic-default-reset");
  assert.ok(resetResult.suggestions.includes("Pause; name the pattern; take one slow breath; choose one next-right action"));
  assert.equal(JSON.stringify(resetResult).includes("custom private pattern"), false);
});

test("builds reset suggestions without echoing raw cue text", () => {
  const result = selectRitualSuggestions({
    questionIndex: 6,
    answers: [
      answer(4, "mind-reading"),
      answer(5, "RAW PRIVATE CUE ABOUT A NAMED PERSON"),
    ],
  });

  assert.equal(result.pattern, "mind-reading");
  assert.equal(result.reason, "known-pattern-reset-with-cue-signal");
  assert.equal(result.sanitizedRecentAnswerSignals[result.sanitizedRecentAnswerSignals.length - 1]?.hasAnswer, true);
  assert.equal(JSON.stringify(result).includes("RAW PRIVATE CUE"), false);
  assert.ok(result.suggestions[0]?.includes("the selected cue"));
});

test("returns deterministic empty fallback for unsupported questions", () => {
  const first = selectRitualSuggestions({ questionIndex: 2, answers: [answer(0, "private text")] });
  const second = selectRitualSuggestions({ questionIndex: 2, answers: [answer(0, "different private text")] });

  assert.deepEqual(first.suggestions, []);
  assert.equal(first.reason, "no-suggestions-for-question");
  assert.deepEqual(first, second);
});
