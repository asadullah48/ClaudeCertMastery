import Link from "next/link";

export const metadata = {
  title: "How the practice score works | Claude Cert Mastery",
};

export default function MethodologyPage() {
  return (
    <main>
      <Link
        href="/"
        className="text-xs text-[var(--color-muted)] hover:text-[var(--color-accent)]"
      >
        &larr; Back home
      </Link>

      <header className="mt-4 mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          How the practice score works
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">
          This page explains ClaudeCertMastery&apos;s own scoring model. It is a
          transparent simulation built by this platform &mdash; not an official Anthropic
          scoring system, and not a guarantee of how any real certification exam would
          score the same answers.
        </p>
      </header>

      <section className="space-y-6 text-sm leading-relaxed">
        <div>
          <h2 className="mb-2 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            1. Item grading
          </h2>
          <p>
            Single-answer items are correct only if the one selected option matches the
            key. Multi-response items are graded all-or-nothing toward the practice
            score: every correct option must be selected and no incorrect one. Partial
            credit is calculated separately and used only for the domain mastery
            breakdown, never for the headline score, so a mix of right and wrong
            selections cannot inflate it.
          </p>
        </div>

        <div>
          <h2 className="mb-2 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            2. Blueprint weighting
          </h2>
          <p>
            Each track&apos;s published domain weighting decides how many items from
            each domain appear on an exam &mdash; it is not reapplied when scoring, since
            that would count the same weighting twice. Every item you answer is worth
            the same one point toward your practice score.
          </p>
        </div>

        <div>
          <h2 className="mb-2 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            3. The 100&ndash;1000 practice scale and 720 pass line
          </h2>
          <p>
            Your raw percentage correct is mapped onto a 100&ndash;1000 scale using a
            piecewise-linear formula anchored at three points: 0% raw &rarr; 100, 70%
            raw &rarr; 720 (the pass line), and 100% raw &rarr; 1000. This anchoring
            keeps the pass line fixed at a meaningful, consistent number instead of
            drifting with a straight 0&ndash;100% line.
          </p>
          <p className="mt-2">
            <strong>This scale and the 70% raw pass threshold are ClaudeCertMastery&apos;s
            own simulation.</strong> Anthropic does not publish an official raw-to-scaled
            mapping for its certification exams, so this platform cannot and does not
            claim its number matches an official score. Treat it as an estimated
            readiness signal, not a certification result.
          </p>
        </div>

        <div>
          <h2 className="mb-2 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            4. Domain mastery
          </h2>
          <p>
            Domain percentages are diagnostic only &mdash; they show how you performed
            within each domain regardless of that domain&apos;s weight in the overall
            exam. Bands (critical / developing / proficient / strong) are drawn from
            fixed percentage thresholds to help prioritize what to study next.
          </p>
        </div>
      </section>
    </main>
  );
}
