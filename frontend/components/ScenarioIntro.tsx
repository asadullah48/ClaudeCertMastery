"use client";

/**
 * Scenario Lab entry screen, shown before an attempt exists.
 *
 * The Situation (`setup_text`) is deliberately NOT shown here: Slice 2's API only
 * returns it from `POST /start`, which also creates the attempt server-side -- there
 * is no separate "preview" endpoint, and adding one is not justified by this slice's
 * needs (see the Slice 3 report's API Integrity section). Title/domain come from the
 * already-fetched discovery list instead, so this screen costs no extra request and
 * creates no server state until the learner explicitly begins. The Situation itself
 * renders as the first thing in ScenarioStepRunner once the attempt starts.
 */
export function ScenarioIntro({
  title,
  domainCode,
  loading,
  onBegin,
}: {
  title: string;
  domainCode: string;
  loading: boolean;
  onBegin: () => void;
}) {
  return (
    <div>
      <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
        {domainCode}
      </span>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">{title}</h1>

      <p className="mt-4 max-w-xl text-sm leading-relaxed text-[var(--color-muted)]">
        You are about to take responsibility for a system in a specific situation.
        You will work through a short sequence of decisions, each graded the moment
        you commit to it, with what happened and why shown before you move on.
      </p>

      <button
        type="button"
        onClick={onBegin}
        disabled={loading}
        className="mt-6 rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)] disabled:opacity-50"
      >
        {loading ? "Starting..." : "Begin scenario"}
      </button>
    </div>
  );
}
