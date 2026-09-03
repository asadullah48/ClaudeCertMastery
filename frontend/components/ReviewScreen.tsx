"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useExam } from "@/lib/store";
import type { ExamQuestion, ExamResult, Explanation, ItemResult } from "@/lib/types";

const BAND_COLOR: Record<string, string> = {
  strong: "var(--color-pass)",
  developing: "var(--color-warn)",
  weak: "var(--color-accent)",
};

function ScoreHeadline({ result, expired }: { result: ExamResult; expired: boolean }) {
  const { scaled_score, pass_scaled_score, passed, raw_correct, raw_total } = result;
  // The scale runs 100-1000, so the bar must start at 100. Anchoring at 0 would
  // overstate every score by making the floor look like an achievement.
  const pct = ((scaled_score - 100) / 900) * 100;
  const passPct = ((pass_scaled_score - 100) / 900) * 100;

  return (
    <section className="rounded-xl border border-[var(--color-edge)] bg-[var(--color-surface)] p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-widest text-[var(--color-muted)]">
            Scaled score
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-4xl font-semibold tabular-nums">{scaled_score}</span>
            <span className="text-sm text-[var(--color-muted)]">/ 1000</span>
          </div>
        </div>
        <span
          className="rounded-md px-3 py-1 text-sm font-medium"
          style={{
            background: passed ? "var(--color-pass)" : "var(--color-accent)",
            color: "var(--color-ink)",
          }}
        >
          {passed ? "Pass" : "Did not pass"}
        </span>
      </div>

      <div className="relative mt-5 h-2 w-full rounded bg-[var(--color-edge)]">
        <div
          className="h-full rounded transition-all"
          style={{
            width: `${Math.max(0, Math.min(100, pct))}%`,
            background: passed ? "var(--color-pass)" : "var(--color-accent)",
          }}
        />
        {/* The pass line drawn where it actually falls, so a near miss reads as a near
            miss rather than as a number without context. */}
        <div
          className="absolute top-[-4px] h-4 w-px bg-[#e8eaf0]"
          style={{ left: `${passPct}%` }}
          aria-hidden="true"
        />
      </div>

      <p className="mt-3 text-sm text-[var(--color-muted)]">
        {raw_correct} of {raw_total} correct ({result.raw_percentage.toFixed(1)}%). The
        pass line is {pass_scaled_score}.
      </p>

      {expired && (
        <p className="mt-3 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-3 text-xs text-[var(--color-warn)]">
          Time expired. Unanswered questions were graded as incorrect.
        </p>
      )}

      {result.composition_warning && (
        <p className="mt-3 text-xs text-[var(--color-warn)]">
          {result.composition_warning}
        </p>
      )}
    </section>
  );
}

function DomainTable({ scores }: { scores: ExamResult["domain_scores"] }) {
  return (
    <section className="mt-8">
      <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
        Domain mastery
      </h2>
      <div className="overflow-x-auto rounded-lg border border-[var(--color-edge)]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--color-surface)] text-xs text-[var(--color-muted)]">
            <tr>
              <th className="px-4 py-3 text-left font-medium">Domain</th>
              <th className="px-4 py-3 text-right font-medium">Score</th>
              <th className="px-4 py-3 text-right font-medium">%</th>
              <th className="px-4 py-3 text-right font-medium">Band</th>
            </tr>
          </thead>
          <tbody>
            {scores.map((d) => (
              <tr key={d.domain_code} className="border-t border-[var(--color-edge)]">
                <td className="px-4 py-3">
                  <span className="font-mono text-xs text-[var(--color-muted)]">
                    {d.domain_code}
                  </span>
                  <span className="ml-2">{d.domain_name}</span>
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {d.correct}/{d.total}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {d.percentage.toFixed(0)}%
                </td>
                <td
                  className="px-4 py-3 text-right text-xs font-medium capitalize"
                  style={{ color: BAND_COLOR[d.mastery_band] ?? "var(--color-muted)" }}
                >
                  {d.mastery_band}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-[var(--color-muted)]">
        Domain percentages are diagnostic. Blueprint weights decide how many items each
        domain contributes, not what each item is worth &mdash; every item counts equally
        toward the scaled score.
      </p>
    </section>
  );
}

function ExplanationBody({ explanation }: { explanation: Explanation }) {
  if (explanation.source === "static") {
    return (
      <div className="mt-3 space-y-2">
        <p className="text-sm leading-relaxed">{explanation.static_explanation}</p>
        <p className="text-xs text-[var(--color-muted)]">
          Authored explanation. AI remediation is unavailable
          {explanation.detail ? ` (${explanation.detail})` : ""}.
        </p>
      </div>
    );
  }

  const sections: [string, string][] = [
    ["Why the correct answer is correct", explanation.why_correct],
    ["Why your answer was wrong", explanation.why_your_answer_wrong],
    ["Key concept", explanation.key_concept],
    ["Blueprint link", explanation.blueprint_link],
    ["Study tip", explanation.study_tip],
  ];

  return (
    <div className="mt-3 space-y-3">
      {sections
        .filter(([, body]) => body)
        .map(([heading, body]) => (
          <div key={heading}>
            <div className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
              {heading}
            </div>
            <p className="mt-1 text-sm leading-relaxed">{body}</p>
          </div>
        ))}
      <p className="text-xs text-[var(--color-muted)]">
        Generated by Claude{explanation.reused ? " (reused for this same answer)" : ""}.
      </p>
    </div>
  );
}

function ItemRow({
  item,
  question,
  attemptId,
}: {
  item: ItemResult;
  question: ExamQuestion | undefined;
  attemptId: number;
}) {
  const [open, setOpen] = useState(false);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [loading, setLoading] = useState(false);

  // Fetched on expand, not on page load. A 60-item exam with 20 wrong answers would
  // otherwise fan out 20 generations for panels the candidate may never open.
  async function expand() {
    const next = !open;
    setOpen(next);
    if (!next || explanation || item.is_correct) return;

    setLoading(true);
    try {
      const res = await api.explanations(attemptId, {
        question_ids: [item.question_id],
      });
      setExplanation(res.explanations[0] ?? null);
    } catch {
      setExplanation(null); // falls through to the authored explanation below
    } finally {
      setLoading(false);
    }
  }

  const labelOf = (ids: number[]) =>
    question
      ? question.options
          .filter((o) => ids.includes(o.id))
          .map((o) => o.label)
          .join(", ") || "none"
      : ids.join(", ");

  return (
    <div className="border-t border-[var(--color-edge)] first:border-t-0">
      <button
        type="button"
        onClick={expand}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-[var(--color-surface)]"
      >
        <span
          className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
          style={{
            background: item.is_correct ? "var(--color-pass)" : "var(--color-accent)",
          }}
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1">
          <span className="font-mono text-xs text-[var(--color-muted)]">
            {item.external_id}
          </span>
          <span className="ml-2 text-xs text-[var(--color-muted)]">
            {item.domain_code}
          </span>
          {question && (
            <span className="mt-1 block truncate text-sm">{question.stem}</span>
          )}
        </span>
        <span className="shrink-0 text-xs text-[var(--color-muted)]">
          {item.is_correct
            ? "Correct"
            : `You: ${labelOf(item.selected_option_ids)} · Correct: ${labelOf(
                item.correct_option_ids,
              )}`}
        </span>
      </button>

      {open && (
        <div className="px-4 pb-4 pl-9">
          {item.is_correct ? (
            <p className="text-sm leading-relaxed text-[var(--color-muted)]">
              {item.explanation}
            </p>
          ) : loading ? (
            <p className="text-sm text-[var(--color-muted)]">
              Generating remediation&hellip;
            </p>
          ) : explanation ? (
            <ExplanationBody explanation={explanation} />
          ) : (
            <p className="text-sm leading-relaxed">{item.explanation}</p>
          )}
        </div>
      )}
    </div>
  );
}

export function ReviewScreen() {
  const { result, questions, endReason, trackCode, reset } = useExam();
  const [filter, setFilter] = useState<"all" | "wrong">("wrong");

  if (!result) return null;

  const byId = new Map(questions.map((q) => [q.id, q]));
  const items =
    filter === "wrong" ? result.items.filter((i) => !i.is_correct) : result.items;
  const wrongCount = result.items.filter((i) => !i.is_correct).length;

  return (
    <div>
      <ScoreHeadline result={result} expired={endReason === "expired"} />
      <DomainTable scores={result.domain_scores} />

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            Item review
          </h2>
          <div className="flex gap-1">
            {(["wrong", "all"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`rounded px-2.5 py-1 text-xs ${
                  filter === f
                    ? "bg-[var(--color-edge)]"
                    : "text-[var(--color-muted)] hover:text-[#e8eaf0]"
                }`}
              >
                {f === "wrong"
                  ? `Missed (${wrongCount})`
                  : `All (${result.items.length})`}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-[var(--color-edge)]">
          {items.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-[var(--color-muted)]">
              Nothing missed. Every item was answered correctly.
            </p>
          ) : (
            items.map((item) => (
              <ItemRow
                key={item.question_id}
                item={item}
                question={byId.get(item.question_id)}
                attemptId={result.attempt_id}
              />
            ))
          )}
        </div>
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          Expand a missed item for remediation targeted at the answer you actually chose.
        </p>
      </section>

      <div className="mt-8 flex gap-2">
        <button
          type="button"
          onClick={reset}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)]"
        >
          Take another exam
        </button>
        <Link
          href={trackCode ? `/tracks/${trackCode}` : "/"}
          className="rounded-md border border-[var(--color-edge)] px-4 py-2 text-sm"
        >
          Back to track
        </Link>
      </div>
    </div>
  );
}
