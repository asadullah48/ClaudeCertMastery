import Link from "next/link";

/**
 * The one place a paid capability is offered. Always says what the learner would GAIN
 * (the readiness outcome), never just "upgrade required".
 *
 * Honest by construction: until a merchant of record is connected the backend reports
 * checkout_available=false and the primary action is a disabled "Checkout coming soon"
 * state. Nothing here can simulate or imply a completed purchase.
 */
export function UpgradePanel({
  headline,
  outcome,
  checkoutAvailable = false,
  priceUsd = 29,
  durationDays = 90,
}: {
  headline: string;
  outcome: string;
  checkoutAvailable?: boolean;
  priceUsd?: number;
  durationDays?: number;
}) {
  return (
    <section
      aria-label="Readiness Pass"
      className="rounded-lg border border-[var(--color-accent)]/60 bg-[var(--color-surface)] p-5"
    >
      <h2 className="text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
        Readiness Pass
      </h2>
      <p className="mt-2 text-sm font-medium">{headline}</p>
      <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">{outcome}</p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={!checkoutAvailable}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          Unlock Readiness Pass
        </button>
        <span className="text-xs text-[var(--color-muted)]">
          ${priceUsd} for {durationDays} days, one payment, no subscription.
          {!checkoutAvailable && " Checkout coming soon."}
        </span>
        <Link href="/pricing" className="text-xs text-[var(--color-accent)] hover:underline">
          Compare plans
        </Link>
      </div>
    </section>
  );
}
