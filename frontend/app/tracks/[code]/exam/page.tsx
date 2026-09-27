"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ExamRunner } from "@/components/ExamRunner";
import { ReviewScreen } from "@/components/ReviewScreen";
import { UpgradePanel } from "@/components/UpgradePanel";
import { api } from "@/lib/api";
import { useExam } from "@/lib/store";
import type { Access } from "@/lib/types";

const EXAMS_LOCKED =
  "Your free diagnostic exam is complete. Readiness Pass unlocks further practice exams so you can build repeated evidence in every domain.";

/** Practice lengths. Full length mirrors the published item count for the track. */
const LENGTHS: { label: string; items?: number; note: string }[] = [
  { label: "Full exam", note: "Published length and duration." },
  { label: "30 items", items: 30, note: "Half length, same domain weighting." },
  { label: "10 items", items: 10, note: "Quick drill." },
];

export default function ExamPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const { status, error, locked, start, reset, trackCode } = useExam();
  // The backend decides whether another exam may start; asked up front so an Explorer
  // learner sees the value boundary instead of a button that will be refused.
  const [access, setAccess] = useState<Access | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .getAccess(code)
      .then((a) => !cancelled && setAccess(a))
      .catch(() => undefined); // unknown -> show the normal start; the server still enforces
    return () => {
      cancelled = true;
    };
  }, [code, status]);
  const allowance = access?.exam_allowance;
  const blocked = locked !== null || allowance?.allowed === false;

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
          Items are drawn to match the published blueprint weighting, rather than
          whichever domains happen to have the most questions authored. Your Practice
          score is reported on ClaudeCertMastery&apos;s 100&ndash;1000 scale.
        </p>
      </header>

      {error && (
        <p className="mb-6 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-4 text-sm text-[var(--color-warn)]">
          {error}
        </p>
      )}

      {access?.plan === "free" && !blocked && (
        <p className="mb-6 rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]">
          Explorer includes one complete diagnostic exam. Choose the full exam for the
          clearest picture of every domain.
        </p>
      )}

      {blocked ? (
        <UpgradePanel
          headline="Keep building evidence"
          outcome={locked ?? EXAMS_LOCKED}
          checkoutAvailable={access?.offer.checkout_available ?? false}
        />
      ) : (
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
      )}

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
          <li>Multi-response items are all-or-nothing for the practice score.</li>
          <li>
            Submission is final. Remediation appears on the review screen afterwards.
          </li>
        </ul>
      </section>
    </main>
  );
}
