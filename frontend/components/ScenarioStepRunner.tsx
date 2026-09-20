"use client";

import { useEffect, useRef, useState } from "react";
import { useScenario } from "@/lib/scenarioStore";

/**
 * The Scenario Lab decision engine: presents the current step, takes a committed
 * decision, and reveals consequence + reflection. Reuses ExamRunner's option-button
 * language verbatim (`ExamRunner.tsx:160-184`) and its confirm-dialog pattern
 * (`ExamRunner.tsx:279-325`) for the select -> review -> commit sequence, so the two
 * experiences share one accessible interaction vocabulary rather than inventing a
 * second one.
 *
 * Threat boundary, restated in code: this component never renders `misconception_tag`
 * -- only the authored `rationale` prose the backend already translates it into. See
 * `SelectedOptionFeedback` in lib/types.ts.
 */
export function ScenarioStepRunner({
  title,
  domainCode,
  setupText,
}: {
  title: string;
  domainCode: string;
  setupText: string;
}) {
  const {
    currentStep,
    selectedOptionIds,
    hintsRevealed,
    lastAnswer,
    status,
    error,
    errorKind,
    selectOption,
    revealHint,
    commit,
    continueToNext,
    reset,
  } = useScenario();

  const [reviewing, setReviewing] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  // Focus moves to the step/consequence heading on every major transition, so a
  // keyboard or screen-reader user is never left on a stale control (Slice 3 Section
  // 17; the same deliberate-focus-move ExamRunner never needed, since it has no
  // per-item feedback state of its own).
  useEffect(() => {
    headingRef.current?.focus();
  }, [currentStep?.position, status]);

  if (!currentStep && status !== "consequence") return null;

  const step = currentStep;
  const selected = lastAnswer?.selected_option_ids ?? selectedOptionIds;

  function handleCommitClick() {
    if (selectedOptionIds.length === 0) return;
    setReviewing(true);
  }

  function confirmCommit() {
    setReviewing(false);
    void commit();
  }

  // --- Consequence + reflection (post-commit) ---------------------------------------
  if (status === "consequence" && lastAnswer) {
    const outcomeLabel = lastAnswer.is_correct ? "On track" : "Needs correction";
    const correctLabels = step
      ? step.options
          .filter((o) => lastAnswer.correct_option_ids.includes(o.id))
          .map((o) => o.label)
          .join(", ")
      : "";

    return (
      <div>
        <ScenarioContextBar title={title} domainCode={domainCode} step={step} />

        <h2
          ref={headingRef}
          tabIndex={-1}
          className="mt-6 text-lg font-semibold tracking-tight outline-none"
        >
          What happened
        </h2>

        {/* Text-labeled status, never color-only (mirrors ExamRunner's Flagged
            button precedent, ExamRunner.tsx:216). */}
        <div
          className="mt-3 inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium"
          style={{
            borderColor: lastAnswer.is_correct ? "var(--color-pass)" : "var(--color-warn)",
            color: lastAnswer.is_correct ? "var(--color-pass)" : "var(--color-warn)",
          }}
        >
          <span aria-hidden="true">{lastAnswer.is_correct ? "✓" : "⚠"}</span>
          Outcome: {outcomeLabel}
        </div>

        <div className="mt-4 space-y-3 rounded-xl border border-[var(--color-edge)] bg-[var(--color-surface)] p-5">
          {lastAnswer.feedback.map((f) => (
            <div key={f.option_id}>
              <p className="text-sm">
                <span className="text-[var(--color-muted)]">You decided to: </span>
                {step?.options.find((o) => o.id === f.option_id)?.text ?? f.label}
              </p>
              <div className="mt-2">
                <div className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                  Reflection
                </div>
                {/* Only the authored rationale prose is ever rendered here --
                    f.misconception_tag exists on this object but is deliberately
                    never read below. */}
                <p className="mt-1 text-sm leading-relaxed">{f.rationale}</p>
              </div>
            </div>
          ))}

          {!lastAnswer.is_correct && correctLabels && (
            <p className="border-t border-[var(--color-edge)] pt-3 text-xs text-[var(--color-muted)]">
              The stronger move here was option {correctLabels}.
            </p>
          )}
        </div>

        {error && <ErrorPanel message={error} kind={errorKind} onRestart={reset} />}

        <div className="mt-6">
          <button
            type="button"
            onClick={continueToNext}
            className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)]"
          >
            {lastAnswer.attempt_status === "submitted" ? "See summary" : "Continue"}
          </button>
        </div>
      </div>
    );
  }

  if (!step) return null;

  // --- Situation / decision (pre-commit) ---------------------------------------------
  return (
    <div className="pb-24">
      <ScenarioContextBar title={title} domainCode={domainCode} step={step} />

      {/* The Situation renders in full only on the first decision -- repeating it on
          every step would turn a short scenario into a wall of text on mobile
          (Section 16). Later steps get a one-line recap instead. */}
      {step.position === 1 ? (
        <section className="mt-6 rounded-xl border border-[var(--color-edge)] bg-[var(--color-surface)] p-5">
          <h2 className="mb-2 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            Situation
          </h2>
          <p className="text-sm leading-relaxed">{setupText}</p>
        </section>
      ) : (
        <p className="mt-4 text-xs text-[var(--color-muted)]" title={setupText}>
          Continuing: {title}
        </p>
      )}

      <article className="mt-6">
        <h2
          ref={headingRef}
          tabIndex={-1}
          className="text-xs font-medium uppercase tracking-widest text-[var(--color-muted)] outline-none"
        >
          What&apos;s happening now
        </h2>
        <p className="mt-2 text-base leading-relaxed">{step.prompt_text}</p>
      </article>

      {step.hints_available > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => void revealHint()}
            disabled={hintsRevealed.length >= step.hints_available}
            className="rounded-md border border-[var(--color-edge)] px-3 py-1.5 text-xs text-[var(--color-muted)] hover:border-[var(--color-accent)] disabled:opacity-40"
          >
            {hintsRevealed.length >= step.hints_available
              ? "No more hints for this decision"
              : "Get a hint"}
          </button>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            Hints cost some of this decision&apos;s credit -- the exact cost is shown
            when you reveal one.
          </p>
          {hintsRevealed.map((h) => (
            <p
              key={h.hint_position}
              className="mt-2 rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-3 text-sm leading-relaxed"
            >
              <span className="mr-2 rounded bg-[var(--color-edge)] px-1.5 py-0.5 font-mono text-xs">
                &minus;{(h.penalty_bps / 100).toFixed(0)}%
              </span>
              {h.text}
            </p>
          ))}
        </div>
      )}

      <h2 className="mt-6 mb-3 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
        Your decision
      </h2>
      <div className="space-y-2" role="group" aria-label="Decision options">
        {step.options.map((option) => {
          const isSelected = selected.includes(option.id);
          return (
            <button
              key={option.id}
              type="button"
              aria-pressed={isSelected}
              onClick={() => selectOption(option.id)}
              className={`flex w-full items-start gap-3 rounded-lg border p-4 text-left text-sm transition-colors ${
                isSelected
                  ? "border-[var(--color-accent)] bg-[var(--color-accent)]/10"
                  : "border-[var(--color-edge)] bg-[var(--color-surface)] hover:border-[var(--color-muted)]"
              }`}
            >
              <span
                className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center font-mono text-xs ${
                  step.step_type === "mr" ? "rounded" : "rounded-full"
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

      {error && <ErrorPanel message={error} kind={errorKind} onRestart={reset} />}

      <div className="fixed inset-x-0 bottom-0 border-t border-[var(--color-edge)] bg-[var(--color-ink)]/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-3">
          <span className="text-xs text-[var(--color-muted)]">
            {selectedOptionIds.length === 0
              ? "Choose an option to continue."
              : "Ready to commit."}
          </span>
          <button
            type="button"
            onClick={handleCommitClick}
            disabled={selectedOptionIds.length === 0 || status === "committing"}
            className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)] disabled:opacity-50"
          >
            {status === "committing" ? "Committing..." : "Commit decision"}
          </button>
        </div>
      </div>

      {reviewing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/60 p-6">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="commit-title"
            className="w-full max-w-md rounded-xl border border-[var(--color-edge)] bg-[var(--color-surface)] p-6"
          >
            <h2 id="commit-title" className="text-base font-semibold">
              Commit this decision?
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-[var(--color-muted)]">
              You are about to commit:{" "}
              <span className="text-[#e8eaf0]">
                {step.options
                  .filter((o) => selectedOptionIds.includes(o.id))
                  .map((o) => o.text)
                  .join("; ")}
              </span>
            </p>
            <p className="mt-2 text-xs text-[var(--color-muted)]">
              This decision will be recorded and cannot be changed afterward.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setReviewing(false)}
                className="rounded-md border border-[var(--color-edge)] px-3 py-1.5 text-sm"
              >
                Change selection
              </button>
              <button
                type="button"
                onClick={confirmCommit}
                className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-[var(--color-ink)]"
              >
                Commit decision
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ScenarioContextBar({
  title,
  domainCode,
  step,
}: {
  title: string;
  domainCode: string;
  step: { position: number; total_steps: number } | null;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
          {domainCode}
        </span>
        <span className="text-sm font-medium">{title}</span>
      </div>
      {step && (
        <span className="text-xs text-[var(--color-muted)]">
          Decision {step.position} of {step.total_steps}
        </span>
      )}
    </div>
  );
}

function ErrorPanel({
  message,
  kind,
  onRestart,
}: {
  message: string;
  kind: "conflict" | "not_found" | "transient" | null;
  onRestart: () => void;
}) {
  const needsRestart = kind === "conflict" || kind === "not_found";
  return (
    <div
      role="alert"
      className="mt-4 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-3 text-sm text-[var(--color-warn)]"
    >
      <p>{message}</p>
      {needsRestart && (
        <button
          type="button"
          onClick={onRestart}
          className="mt-2 rounded-md border border-[var(--color-warn)]/50 px-3 py-1 text-xs"
        >
          Start a new attempt
        </button>
      )}
    </div>
  );
}
