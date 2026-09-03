"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useExam } from "@/lib/store";

/** mm:ss, or h:mm:ss once an hour or more remains. */
function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/**
 * The countdown.
 *
 * Reads an absolute deadline off the store rather than decrementing a counter, so a
 * throttled background tab cannot buy the candidate extra time. The interval only
 * decides how often the display refreshes; it never decides how much time is left.
 */
function Timer({ deadline, onExpire }: { deadline: number; onExpire: () => void }) {
  const [remaining, setRemaining] = useState(() => deadline - Date.now());
  // Expiry must fire exactly once. Without this guard the 1s interval would re-enter
  // submission on every tick after the deadline passed.
  const fired = useRef(false);

  useEffect(() => {
    const tick = () => {
      const left = deadline - Date.now();
      setRemaining(left);
      if (left <= 0 && !fired.current) {
        fired.current = true;
        onExpire();
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [deadline, onExpire]);

  const urgent = remaining <= 5 * 60_000;

  return (
    <div
      className={`rounded-md border px-3 py-1.5 font-mono text-sm tabular-nums ${
        urgent
          ? "border-[var(--color-warn)]/50 bg-[var(--color-warn)]/10 text-[var(--color-warn)]"
          : "border-[var(--color-edge)] bg-[var(--color-surface)]"
      }`}
      // The clock changes every second; announcing each tick would make a screen reader
      // unusable. Only the urgent threshold is worth interrupting for.
      aria-live={urgent ? "polite" : "off"}
      aria-label={`Time remaining: ${formatClock(remaining)}`}
    >
      {formatClock(remaining)}
    </div>
  );
}

export function ExamRunner() {
  const {
    questions,
    answers,
    flagged,
    index,
    deadline,
    status,
    error,
    compositionWarning,
    select,
    toggleFlag,
    goTo,
    tickTime,
    submit,
    onTimeExpiry,
  } = useExam();

  const [confirming, setConfirming] = useState(false);
  const question = questions[index];

  // Accumulate time against the question actually on screen. The cleanup runs on every
  // navigation and on unmount, so the elapsed seconds land on the item the candidate was
  // looking at rather than on whichever one happens to be current at submit time.
  useEffect(() => {
    if (!question) return;
    const enteredAt = Date.now();
    const questionId = question.id;
    return () => {
      tickTime(questionId, (Date.now() - enteredAt) / 1000);
    };
  }, [question, tickTime]);

  const handleExpire = useCallback(() => onTimeExpiry(), [onTimeExpiry]);

  // Keyboard navigation. A 60-item exam is a lot of clicking, and arrow keys are what a
  // candidate reaches for. Disabled while the confirm dialog is open so the two do not
  // fight over the same keys.
  useEffect(() => {
    if (confirming) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") goTo(index + 1);
      if (e.key === "ArrowLeft") goTo(index - 1);
      if (e.key.toLowerCase() === "f" && question) toggleFlag(question.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, goTo, toggleFlag, question, confirming]);

  if (!question) return null;

  const selected = answers[question.id] ?? [];
  const answeredCount = questions.filter((q) => (answers[q.id] ?? []).length > 0).length;
  const unanswered = questions.length - answeredCount;
  const flaggedCount = Object.keys(flagged).length;

  return (
    <div className="pb-24">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
            {question.domain_code}
          </span>
          <span className="text-sm text-[var(--color-muted)]">
            Question {index + 1} of {questions.length}
          </span>
        </div>
        {deadline !== null && <Timer deadline={deadline} onExpire={handleExpire} />}
      </div>

      {compositionWarning && (
        <p className="mb-4 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-3 text-xs text-[var(--color-warn)]">
          {compositionWarning}
        </p>
      )}

      <div className="h-1 w-full overflow-hidden rounded bg-[var(--color-edge)]">
        <div
          className="h-full bg-[var(--color-accent)] transition-all"
          style={{ width: `${(answeredCount / questions.length) * 100}%` }}
        />
      </div>

      <article className="mt-6">
        <p className="text-base leading-relaxed">{question.stem}</p>
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          {question.question_type === "mr"
            ? "Select all that apply."
            : "Select one answer."}
        </p>

        {/* A group of toggle buttons rather than native radios/checkboxes: MR items are
            checkbox-shaped and MCQ items radio-shaped, and one consistent control keeps
            keyboard behaviour identical across both. aria-pressed carries the state. */}
        <div className="mt-5 space-y-2" role="group" aria-label="Answer options">
          {question.options.map((option) => {
            const isSelected = selected.includes(option.id);
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={isSelected}
                onClick={() => select(question.id, option.id)}
                className={`flex w-full items-start gap-3 rounded-lg border p-4 text-left text-sm transition-colors ${
                  isSelected
                    ? "border-[var(--color-accent)] bg-[var(--color-accent)]/10"
                    : "border-[var(--color-edge)] bg-[var(--color-surface)] hover:border-[var(--color-muted)]"
                }`}
              >
                <span
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center font-mono text-xs ${
                    question.question_type === "mr" ? "rounded" : "rounded-full"
                  } ${
                    isSelected
                      ? "bg-[var(--color-accent)] text-[var(--color-ink)]"
                      : "border border-[var(--color-edge)] text-[var(--color-muted)]"
                  }`}
                >
                  {option.label}
                </span>
                <span className="leading-relaxed">{option.text}</span>
              </button>
            );
          })}
        </div>
      </article>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => goTo(index - 1)}
          disabled={index === 0}
          className="rounded-md border border-[var(--color-edge)] px-3 py-1.5 text-sm disabled:opacity-30"
        >
          &larr; Previous
        </button>
        <button
          type="button"
          onClick={() => goTo(index + 1)}
          disabled={index === questions.length - 1}
          className="rounded-md border border-[var(--color-edge)] px-3 py-1.5 text-sm disabled:opacity-30"
        >
          Next &rarr;
        </button>
        <button
          type="button"
          onClick={() => toggleFlag(question.id)}
          aria-pressed={Boolean(flagged[question.id])}
          className={`rounded-md border px-3 py-1.5 text-sm ${
            flagged[question.id]
              ? "border-[var(--color-warn)] text-[var(--color-warn)]"
              : "border-[var(--color-edge)]"
          }`}
        >
          {flagged[question.id] ? "Flagged" : "Flag for review"}
        </button>
        <span className="ml-auto text-xs text-[var(--color-muted)]">
          &larr; &rarr; to navigate &middot; F to flag
        </span>
      </div>

      <section className="mt-8">
        <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
          All questions &middot; {answeredCount} answered &middot; {flaggedCount} flagged
        </h2>
        <div className="flex flex-wrap gap-1.5">
          {questions.map((q, i) => {
            const isAnswered = (answers[q.id] ?? []).length > 0;
            const isFlagged = Boolean(flagged[q.id]);
            return (
              <button
                key={q.id}
                type="button"
                onClick={() => goTo(i)}
                aria-current={i === index ? "true" : undefined}
                aria-label={`Question ${i + 1}${isAnswered ? ", answered" : ", unanswered"}${
                  isFlagged ? ", flagged" : ""
                }`}
                className={`h-8 w-8 rounded font-mono text-xs transition-colors ${
                  i === index
                    ? "bg-[var(--color-accent)] text-[var(--color-ink)]"
                    : isFlagged
                      ? "border border-[var(--color-warn)] text-[var(--color-warn)]"
                      : isAnswered
                        ? "bg-[var(--color-edge)]"
                        : "border border-[var(--color-edge)] text-[var(--color-muted)]"
                }`}
              >
                {i + 1}
              </button>
            );
          })}
        </div>
      </section>

      {error && (
        <p className="mt-4 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-3 text-sm text-[var(--color-warn)]">
          {error}
        </p>
      )}

      <div className="fixed inset-x-0 bottom-0 border-t border-[var(--color-edge)] bg-[var(--color-ink)]/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-3">
          <span className="text-xs text-[var(--color-muted)]">
            {unanswered === 0 ? "All questions answered." : `${unanswered} unanswered.`}
          </span>
          <button
            type="button"
            onClick={() => setConfirming(true)}
            disabled={status === "submitting"}
            className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)] disabled:opacity-50"
          >
            {status === "submitting" ? "Submitting..." : "Submit exam"}
          </button>
        </div>
      </div>

      {confirming && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/60 p-6">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            className="w-full max-w-md rounded-xl border border-[var(--color-edge)] bg-[var(--color-surface)] p-6"
          >
            <h2 id="confirm-title" className="text-base font-semibold">
              Submit this exam?
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-[var(--color-muted)]">
              {unanswered > 0 ? (
                <>
                  <strong className="text-[var(--color-warn)]">
                    {unanswered} question{unanswered === 1 ? "" : "s"}
                  </strong>{" "}
                  {unanswered === 1 ? "is" : "are"} still unanswered and will be marked
                  incorrect.
                </>
              ) : (
                "Every question has an answer."
              )}{" "}
              Submitting is final for this attempt.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="rounded-md border border-[var(--color-edge)] px-3 py-1.5 text-sm"
              >
                Keep working
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirming(false);
                  void submit();
                }}
                className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-[var(--color-ink)]"
              >
                Submit
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
