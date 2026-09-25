import Link from "next/link";
import type { DomainReadiness, NextAction, TrackReadiness } from "@/lib/types";

/**
 * KSOR readiness, rendered from GET /me/tracks/{code}/readiness exactly as returned.
 *
 * Presentational only: every state, reason and recommendation is computed by the
 * backend's deterministic policy -- nothing is re-derived or scored here, and no
 * percentage or pass probability is ever shown. Evidence -> state -> reason -> next
 * action is the whole story this screen tells.
 */

const STATE_LABEL: Record<string, string> = {
  insufficient_evidence: "Not enough evidence yet",
  developing: "Developing",
  approaching_ready: "Approaching ready",
  ready: "Ready on current evidence",
};

const STATE_TONE: Record<string, string> = {
  insufficient_evidence: "text-[var(--color-muted)] border-[var(--color-edge)]",
  developing: "text-[var(--color-warn)] border-[var(--color-warn)]/50",
  approaching_ready: "text-[var(--color-warn)] border-[var(--color-warn)]/50",
  ready: "text-[var(--color-pass)] border-[var(--color-pass)]/50",
};

const REASON_TEXT: Record<string, string> = {
  NO_EVIDENCE: "No practice or scenario evidence yet.",
  INSUFFICIENT_PRACTICE_EVIDENCE: "More practice questions are needed (at least 20 counted items).",
  NO_APPLIED_SCENARIO_EVIDENCE: "Needs first-attempt results from two different scenarios.",
  REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE: "Repeating the same scenario does not count as new evidence.",
  DOMAIN_BELOW_THRESHOLD: "A practice or scenario result is below proficient.",
  REPEATED_MISCONCEPTION: "An identified misconception has not been resolved yet.",
  STALE_EVIDENCE: "The most recent evidence is over 90 days old.",
  INSUFFICIENT_DOMAIN_COVERAGE: "Readiness has not been calculated for this domain yet.",
};

function stateLabel(state: string): string {
  return STATE_LABEL[state] ?? state;
}

function StateBadge({ state }: { state: string }) {
  return (
    <span
      className={`rounded border px-2 py-0.5 text-xs ${STATE_TONE[state] ?? "border-[var(--color-edge)]"}`}
    >
      {stateLabel(state)}
    </span>
  );
}

function NextActionCard({ trackCode, next }: { trackCode: string; next: NextAction }) {
  const domain = next.domain_code ? ` in ${next.domain_code}` : "";
  let text: string;
  let href: string | null = null;
  let cta: string | null = null;

  switch (next.action) {
    case "ATTEMPT_SCENARIO":
      text = `Attempt a scenario you have not seen yet${domain}. Only a first attempt counts as evidence.`;
      href = next.scenario_external_id
        ? `/tracks/${trackCode}/scenarios/${next.scenario_external_id}`
        : `/tracks/${trackCode}/scenarios`;
      cta = next.scenario_external_id ? `Start ${next.scenario_external_id}` : "Browse scenarios";
      break;
    case "COLLECT_PRACTICE_EVIDENCE":
      text = `Build practice evidence${domain} with a full practice exam.`;
      href = `/tracks/${trackCode}/exam`;
      cta = "Start a practice exam";
      break;
    case "REMEDIATE_MISCONCEPTION":
      text = `Review the misconception flagged${domain}, then show it resolved in a scenario you have not seen.`;
      href = `/tracks/${trackCode}/scenarios`;
      cta = "Browse scenarios";
      break;
    case "REASSESS_DOMAIN":
      text = `Your evidence${domain} is getting old. Refresh it with new practice.`;
      href = `/tracks/${trackCode}/exam`;
      cta = "Start a practice exam";
      break;
    case "PROCEED_TO_READINESS_EVALUATION":
      text = "Every domain is ready on current evidence.";
      break;
    default:
      text = next.action;
  }

  return (
    <section className="rounded-lg border border-[var(--color-accent)]/60 bg-[var(--color-surface)] p-5">
      <h2 className="text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
        Recommended next step
      </h2>
      <p className="mt-2 text-sm leading-relaxed">{text}</p>
      {href && cta && (
        <Link
          href={href}
          className="mt-4 inline-block rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)]"
        >
          {cta}
        </Link>
      )}
    </section>
  );
}

function DomainRow({ d }: { d: DomainReadiness }) {
  return (
    <li className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
          {d.domain_code}
        </span>
        <span className="text-sm font-medium">{d.domain_name}</span>
        <StateBadge state={d.readiness_state} />
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-[var(--color-muted)] sm:grid-cols-4">
        <div>
          <dt>Practice items</dt>
          <dd>{d.practice_evidence_count}</dd>
        </div>
        <div>
          <dt>Practice band</dt>
          <dd>{d.recent_practice_mastery_band ?? "none yet"}</dd>
        </div>
        <div>
          <dt>Scenarios with evidence</dt>
          <dd>{d.distinct_scenario_content_versions}</dd>
        </div>
        <div>
          <dt>Scenario band</dt>
          <dd>{d.recent_scenario_mastery_band ?? "none yet"}</dd>
        </div>
      </dl>
      {d.reason_codes.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-xs text-[var(--color-muted)]">
          {d.reason_codes.map((code) => (
            <li key={code}>{REASON_TEXT[code] ?? code}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function ReadinessSummary({ readiness }: { readiness: TrackReadiness }) {
  const domains = [...readiness.domains].sort((a, b) => a.domain_position - b.domain_position);
  return (
    <div className="space-y-6">
      <section>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">Overall</h2>
          <StateBadge state={readiness.overall_readiness_state} />
        </div>
        <p className="mt-2 max-w-2xl text-xs leading-relaxed text-[var(--color-muted)]">
          Your overall state is your weakest domain. It is ClaudeCertMastery&apos;s own
          evidence-based estimate: not an official Anthropic score, and not a prediction of
          certification results.
        </p>
      </section>

      <NextActionCard trackCode={readiness.track_code} next={readiness.next_action} />

      <section>
        <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
          Domains
        </h2>
        <ul className="space-y-3">
          {domains.map((d) => (
            <DomainRow key={d.domain_code} d={d} />
          ))}
        </ul>
      </section>
    </div>
  );
}
