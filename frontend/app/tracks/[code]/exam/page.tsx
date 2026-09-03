"use client";

import { use, useEffect } from "react";
import Link from "next/link";
import { ExamRunner } from "@/components/ExamRunner";
import { ReviewScreen } from "@/components/ReviewScreen";
import { useExam } from "@/lib/store";

/** Practice lengths. Full length mirrors the published item count for the track. */
const LENGTHS: { label: string; items?: number; note: string }[] = [
  { label: "Full exam", note: "Published length and duration." },
  { label: "30 items", items: 30, note: "Half length, same domain weighting." },
  { label: "10 items", items: 10, note: "Quick drill." },
];

export default function ExamPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const { status, error, start, reset, trackCode } = useExam();

  // A stale sitting from another track must not bleed into this page. Reset on mount
  // when the store is holding an exam for a different track.
  useEffect(() => {
    if (trackCode && trackCode !== code) reset();
  }, [code, trackCode, reset]);

  if (status === "done") {
    return (
      <main>
        <header className="mb-8">
          <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
            {code}
          </span>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">Your result</h1>
        </header>
        <ReviewScreen />
      </main>
    );
  }

  if (status === "running" || status === "submitting") {
    return (
      <main>
        <ExamRunner />
      </main>
    );
  }

  return (
    <main>
      <Link
        href={`/tracks/${code}`}
        className="text-xs text-[var(--color-muted)] hover:text-[var(--color-accent)]"
      >
        &larr; Back to {code}
      </Link>

      <header className="mt-4 mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Start a practice exam</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">
          Items are drawn to match the published blueprint weighting, so the mix mirrors
          the real exam rather than whichever domains happen to have the most questions
          authored. Your score is reported on the 100&ndash;1000 scale.
        </p>
      </header>

      {error && (
        <p className="mb-6 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-4 text-sm text-[var(--color-warn)]">
          {error}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        {LENGTHS.map((option) => (
          <button
            key={option.label}
            type="button"
            disabled={status === "loading"}
            onClick={() => void start(code, option.items)}
            className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5 text-left transition-colors hover:border-[var(--color-accent)] disabled:opacity-50"
          >
            <div className="text-sm font-medium">{option.label}</div>
            <div className="mt-1 text-xs text-[var(--color-muted)]">{option.note}</div>
          </button>
        ))}
      </div>

      {status === "loading" && (
        <p className="mt-6 text-sm text-[var(--color-muted)]">
          Composing your exam&hellip;
        </p>
      )}

      <section className="mt-10 rounded-lg border border-[var(--color-edge)] p-5 text-sm text-[var(--color-muted)]">
        <h2 className="mb-2 text-xs font-medium uppercase tracking-widest">
          Before you start
        </h2>
        <ul className="space-y-1.5 leading-relaxed">
          <li>The timer runs on wall-clock time, so leaving the tab does not pause it.</li>
          <li>You can flag questions and revisit them in any order before submitting.</li>
          <li>Multi-response items are all-or-nothing, exactly as on the real exam.</li>
          <li>
            Submission is final. Remediation appears on the review screen afterwards.
          </li>
        </ul>
      </section>
    </main>
  );
}
