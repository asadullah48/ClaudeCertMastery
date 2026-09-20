"use client";

import Link from "next/link";

const BAND_LABEL: Record<string, string> = {
  critical: "Critical",
  developing: "Developing",
  proficient: "Proficient",
  strong: "Strong",
};

/**
 * Scenario completion summary. Deliberately narrow: a scenario-level result only,
 * never a certification-readiness or mastery claim (Slice 3 Section 20) -- that
 * distinction is architectural, not just wording, so it lives here as the one place
 * this screen is built at all.
 */
export function ScenarioResult({
  title,
  domainCode,
  scorePct,
  masteryBand,
  trackCode,
}: {
  title: string;
  domainCode: string;
  scorePct: number;
  masteryBand: string;
  trackCode: string;
}) {
  return (
    <div>
      <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
        {domainCode}
      </span>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Scenario completed</h1>
      <p className="mt-2 max-w-xl text-sm leading-relaxed text-[var(--color-muted)]">
        You worked through &ldquo;{title}.&rdquo; This reflects your performance on
        this one scenario -- it is not a certification readiness score.
      </p>

      <section className="mt-6 rounded-xl border border-[var(--color-edge)] bg-[var(--color-surface)] p-6">
        <div className="text-xs uppercase tracking-widest text-[var(--color-muted)]">
          This scenario
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className="text-3xl font-semibold tabular-nums">{scorePct}</span>
          <span className="text-sm text-[var(--color-muted)]">/ 100</span>
          <span className="ml-2 rounded bg-[var(--color-edge)] px-2 py-0.5 text-xs font-medium capitalize">
            {BAND_LABEL[masteryBand] ?? masteryBand}
          </span>
        </div>
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          A formative practice score for this scenario only, on its own 0&ndash;100
          scale -- deliberately separate from the 100&ndash;1000 exam scale, so the two
          are never confused.
        </p>
      </section>

      <div className="mt-8 flex flex-wrap gap-2">
        <Link
          href={`/tracks/${trackCode}/scenarios`}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)]"
        >
          More scenarios
        </Link>
        <Link
          href={`/tracks/${trackCode}`}
          className="rounded-md border border-[var(--color-edge)] px-4 py-2 text-sm"
        >
          Back to track
        </Link>
      </div>
    </div>
  );
}
