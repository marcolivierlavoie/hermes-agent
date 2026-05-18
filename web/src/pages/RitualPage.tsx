import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, CheckCircle2, HelpCircle, Moon, RotateCcw, ShieldCheck, SkipForward, Square } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "biff.daily_distortion_ritual.v1";

const INTRO = "Daily Distortion Ritual (60-180 sec)\n\nPause gently. Pick one current worry and keep it broad enough for a public-safe note.";
const QUESTIONS = [
  "1. Control: What is one part I directly control?",
  "2. Influence: What is one part I can nudge, ask for, or prepare?",
  "3. No Control: What part can I release for now?",
  "4. Distortion check: Am I mind-reading, catastrophizing, all-or-nothing thinking, should-ing, or discounting positives?",
  "5. Balanced thought: What is a kinder, more accurate sentence I can believe at least 10%?",
  "6. One small action: What is the next 2-minute step?",
  "7. Evidence point: What is one fact that I have handled something like this before?",
] as const;
const CLOSE = "Close: choose the small action, then stop the loop.";

const EXPLANATIONS = [
  "Name the part that is genuinely inside your next choices: a boundary, a message, a tiny action, a breath, or how you frame the problem.",
  "Name a gentle lever: something you can ask, prepare, clarify, schedule, or make easier, without pretending you control the outcome.",
  "Name the piece that is not yours to solve right now. Releasing is not approval; it is refusing to keep carrying what cannot be moved by rumination.",
  "Look for common brain shortcuts: mind-reading, catastrophizing, all-or-nothing thinking, should-ing, or ignoring evidence that things are not all bad.",
  "Write one sentence that is kinder and more accurate than the worry story. It only needs to feel 10% believable.",
  "Choose the smallest visible next move, ideally something that takes two minutes or less.",
  "Find one factual receipt from your life that says: I have survived, learned, repaired, asked for help, or handled something adjacent before.",
] as const;

type RitualAnswer = {
  questionIndex: number;
  question: string;
  answer: string | null;
  skipped: boolean;
  answeredAt: string;
};

type RitualState = {
  active: boolean;
  questionIndex: number;
  answers: RitualAnswer[];
  startedAt: string;
  updatedAt: string;
  completedAt?: string;
  stoppedAt?: string;
  exitReason?: "completed" | "stopped";
};

function nowIso(): string {
  return new Date().toISOString();
}

function freshState(): RitualState {
  const now = nowIso();
  return {
    active: true,
    questionIndex: 0,
    answers: [],
    startedAt: now,
    updatedAt: now,
  };
}

function safeStoredState(raw: string | null): RitualState | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<RitualState>;
    if (!parsed || !Array.isArray(parsed.answers)) return null;
    const index = Number(parsed.questionIndex ?? 0);
    return {
      active: parsed.active !== false,
      questionIndex: Number.isFinite(index) ? Math.min(Math.max(index, 0), QUESTIONS.length) : 0,
      answers: parsed.answers
        .filter((answer) => typeof answer === "object" && answer !== null)
        .map((answer) => ({
          questionIndex: Number(answer.questionIndex ?? 0),
          question: String(answer.question ?? ""),
          answer: answer.answer == null ? null : String(answer.answer),
          skipped: Boolean(answer.skipped),
          answeredAt: String(answer.answeredAt ?? nowIso()),
        })),
      startedAt: String(parsed.startedAt ?? nowIso()),
      updatedAt: String(parsed.updatedAt ?? nowIso()),
      completedAt: parsed.completedAt ? String(parsed.completedAt) : undefined,
      stoppedAt: parsed.stoppedAt ? String(parsed.stoppedAt) : undefined,
      exitReason: parsed.exitReason === "completed" || parsed.exitReason === "stopped" ? parsed.exitReason : undefined,
    };
  } catch {
    return null;
  }
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "today";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
}

export default function RitualPage() {
  const [state, setState] = useState<RitualState>(() => {
    if (typeof window === "undefined") return freshState();
    return safeStoredState(window.localStorage.getItem(STORAGE_KEY)) ?? freshState();
  });
  const [draft, setDraft] = useState("");
  const [showExplain, setShowExplain] = useState(false);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [state]);

  const currentQuestion = state.questionIndex < QUESTIONS.length ? QUESTIONS[state.questionIndex] : null;
  const complete = !state.active || state.questionIndex >= QUESTIONS.length;
  const progress = Math.min(state.questionIndex + (complete ? 0 : 1), QUESTIONS.length);
  const answeredCount = state.answers.length;

  const statusLabel = useMemo(() => {
    if (state.exitReason === "completed") return "Complete";
    if (state.exitReason === "stopped") return "Stopped";
    return "In progress";
  }, [state.exitReason]);

  const advance = useCallback((answer: string | null, skipped: boolean) => {
    setState((previous) => {
      if (!previous.active || previous.questionIndex >= QUESTIONS.length) return previous;
      const index = previous.questionIndex;
      const answers = [
        ...previous.answers,
        {
          questionIndex: index,
          question: QUESTIONS[index],
          answer,
          skipped,
          answeredAt: nowIso(),
        },
      ];
      const nextIndex = index + 1;
      const now = nowIso();
      if (nextIndex >= QUESTIONS.length) {
        return {
          ...previous,
          active: false,
          questionIndex: nextIndex,
          answers,
          updatedAt: now,
          completedAt: now,
          exitReason: "completed",
        };
      }
      return {
        ...previous,
        questionIndex: nextIndex,
        answers,
        updatedAt: now,
      };
    });
    setDraft("");
    setShowExplain(false);
  }, []);

  const handleAnswer = useCallback(() => {
    const text = draft.trim();
    if (!text) return;
    advance(text, false);
  }, [advance, draft]);

  const handleSkip = useCallback(() => advance(null, true), [advance]);

  const handleStop = useCallback(() => {
    setState((previous) => {
      if (!previous.active) return previous;
      const now = nowIso();
      return {
        ...previous,
        active: false,
        stoppedAt: now,
        updatedAt: now,
        exitReason: "stopped",
      };
    });
    setShowExplain(false);
  }, []);

  const handleRestart = useCallback(() => {
    setDraft("");
    setShowExplain(false);
    setState(freshState());
  }, []);

  return (
    <main className="min-h-dvh bg-[var(--cockpit-shell)] px-4 py-5 text-[var(--cockpit-text)] sm:px-6 lg:px-8">
      <section className="mx-auto flex min-h-[calc(100dvh-2.5rem)] w-full max-w-3xl flex-col justify-center">
        <div className="mb-5 flex items-center justify-between gap-3">
          <a href="/cockpit" className="inline-flex items-center gap-2 rounded-full border border-[var(--cockpit-border)] px-3 py-2 text-xs text-[var(--cockpit-muted)] transition hover:text-[var(--cockpit-text)]">
            <ArrowLeft className="h-3.5 w-3.5" />
            Cockpit
          </a>
          <Badge tone={complete ? "success" : "secondary"} className="text-[10px] uppercase tracking-[0.18em]">
            {statusLabel}
          </Badge>
        </div>

        <Card className="overflow-hidden border-[var(--cockpit-border)] bg-[var(--cockpit-card)] shadow-2xl shadow-black/30">
          <CardContent className="p-0">
            <div className="border-b border-[var(--cockpit-border)] bg-[var(--cockpit-card-raised)] p-5 sm:p-7">
              <div className="flex items-start gap-4">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-[color-mix(in_srgb,var(--cockpit-active)_36%,transparent)] bg-[color-mix(in_srgb,var(--cockpit-active)_10%,transparent)] text-[var(--cockpit-active)]">
                  <Moon className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-xs uppercase tracking-[0.22em] text-[var(--cockpit-muted)]">Biff ritual surface</p>
                  <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-[var(--cockpit-text)] sm:text-3xl">Daily Distortion Ritual</h1>
                  <p className="mt-3 whitespace-pre-line text-sm leading-6 text-[color-mix(in_srgb,var(--cockpit-text)_74%,transparent)]">{INTRO}</p>
                </div>
              </div>

              <div className="mt-6" aria-label={`Question ${progress} of ${QUESTIONS.length}`}>
                <div className="mb-2 flex items-center justify-between text-xs text-[var(--cockpit-muted)]">
                  <span>{complete ? `${answeredCount} responses captured locally` : `Question ${progress} of ${QUESTIONS.length}`}</span>
                  <span>Started {formatTime(state.startedAt)}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-black/30">
                  <div className="h-full rounded-full bg-[var(--cockpit-active)] transition-all" style={{ width: `${(Math.min(state.questionIndex, QUESTIONS.length) / QUESTIONS.length) * 100}%` }} />
                </div>
              </div>
            </div>

            <div className="p-5 sm:p-7">
              {!complete && currentQuestion ? (
                <div className="space-y-5">
                  <div className="rounded-3xl border border-[var(--cockpit-border)] bg-[var(--cockpit-panel)] p-5">
                    <p className="text-xs uppercase tracking-[0.18em] text-[var(--cockpit-muted)]">One prompt only</p>
                    <h2 className="mt-3 text-xl font-semibold leading-8 text-[var(--cockpit-text)]">{currentQuestion}</h2>
                  </div>

                  {showExplain && (
                    <div className="rounded-3xl border border-[color-mix(in_srgb,var(--cockpit-secondary)_40%,transparent)] bg-[color-mix(in_srgb,var(--cockpit-secondary)_10%,transparent)] p-4 text-sm leading-6 text-[color-mix(in_srgb,var(--cockpit-text)_82%,transparent)]">
                      {EXPLANATIONS[state.questionIndex]}
                    </div>
                  )}

                  <label className="block">
                    <span className="sr-only">Answer</span>
                    <textarea
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      rows={5}
                      placeholder="Short phrase is enough. This stays in this browser's localStorage."
                      className="w-full resize-none rounded-3xl border border-[var(--cockpit-border)] bg-[var(--cockpit-shell)] p-4 text-base leading-7 text-[var(--cockpit-text)] outline-none transition placeholder:text-[color-mix(in_srgb,var(--cockpit-muted)_72%,transparent)] focus:border-[color-mix(in_srgb,var(--cockpit-active)_65%,transparent)]"
                    />
                  </label>

                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Button onClick={handleAnswer} disabled={!draft.trim()} className="min-h-12 rounded-2xl bg-[var(--cockpit-active)] text-black hover:bg-[color-mix(in_srgb,var(--cockpit-active)_88%,white)]">
                      <CheckCircle2 className="mr-2 h-4 w-4" />
                      Answer
                    </Button>
                    <Button ghost onClick={handleSkip} className="min-h-12 rounded-2xl border border-[var(--cockpit-border)] text-[var(--cockpit-text)]">
                      <SkipForward className="mr-2 h-4 w-4" />
                      Skip
                    </Button>
                    <Button ghost onClick={handleStop} className="min-h-12 rounded-2xl border border-[var(--cockpit-border)] text-[var(--cockpit-text)]">
                      <Square className="mr-2 h-4 w-4" />
                      Stop
                    </Button>
                    <Button ghost onClick={() => setShowExplain((value) => !value)} className="min-h-12 rounded-2xl border border-[var(--cockpit-border)] text-[var(--cockpit-text)]">
                      <HelpCircle className="mr-2 h-4 w-4" />
                      Explain
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="space-y-5">
                  <div className="rounded-3xl border border-[color-mix(in_srgb,var(--cockpit-healthy)_42%,transparent)] bg-[color-mix(in_srgb,var(--cockpit-healthy)_10%,transparent)] p-5">
                    <div className="flex items-center gap-3 text-[var(--cockpit-active)]">
                      <ShieldCheck className="h-5 w-5" />
                      <p className="text-xs uppercase tracking-[0.2em]">{state.exitReason === "completed" ? "Ritual complete" : "Ritual stopped"}</p>
                    </div>
                    <h2 className="mt-4 text-xl font-semibold text-[var(--cockpit-text)]">{state.exitReason === "completed" ? CLOSE : "Stopped. Nothing else is required."}</h2>
                    <p className="mt-3 text-sm leading-6 text-[color-mix(in_srgb,var(--cockpit-text)_74%,transparent)]">Answers remain only in this browser unless Marco chooses to copy them elsewhere. n8n can stay the scheduler/notification owner; Discord should only point here or act as fallback.</p>
                  </div>

                  <div className="space-y-3">
                    {state.answers.map((answer) => (
                      <article key={`${answer.questionIndex}-${answer.answeredAt}`} className="rounded-2xl border border-[var(--cockpit-border)] bg-[var(--cockpit-panel)] p-4">
                        <p className="text-xs text-[var(--cockpit-muted)]">{answer.question}</p>
                        <p className={cn("mt-2 text-sm leading-6", answer.skipped ? "text-[var(--cockpit-muted)]" : "text-[var(--cockpit-text)]")}>{answer.skipped ? "Skipped" : answer.answer}</p>
                      </article>
                    ))}
                  </div>

                  <Button onClick={handleRestart} className="min-h-12 w-full rounded-2xl bg-[var(--cockpit-active)] text-black hover:bg-[color-mix(in_srgb,var(--cockpit-active)_88%,white)]">
                    <RotateCcw className="mr-2 h-4 w-4" />
                    Start again
                  </Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <p className="mx-auto mt-5 max-w-xl text-center text-xs leading-5 text-[var(--cockpit-muted)]">
          Local-only PWA surface: no Discord capture, no API writes, no tokens or secrets persisted.
        </p>
      </section>
    </main>
  );
}
