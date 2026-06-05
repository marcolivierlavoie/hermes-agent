import { test } from "node:test";
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(resolve(process.cwd(), "src/pages/RitualPage.tsx"), "utf8");

test("ritual page presents the daily practice as the focused dashboard surface", () => {
  assert.match(source, /Daily Practice/);
  assert.match(source, /60-180 sec/);
  assert.match(source, /One prompt only/);
  assert.match(source, /no Discord capture/);
});

test("ritual page represents the daily mechanism loop and Atomic Habits basics", () => {
  for (const expected of [
    "Cue / habit stack",
    "Control / Influence / No-control",
    "distortion or body cue",
    "balanced truthful thought",
    "two-minute agency action",
    "friction",
    "reward",
    "skip",
    "private evidence",
  ]) {
    assert.match(source, new RegExp(expected, "i"));
  }
});

test("ritual page avoids clinical claims and private-source leakage", () => {
  for (const forbidden of ["diagnosis", "treatment", "crisis", "therapist says", "based on your therapy notes"]) {
    assert.equal(source.toLowerCase().includes(forbidden), false);
  }
});
