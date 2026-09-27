import Link from "next/link";
import { UpgradePanel } from "@/components/UpgradePanel";
import { api } from "@/lib/api";
import { serverToken } from "@/lib/serverToken";
import type { Access } from "@/lib/types";

export const dynamic = "force-dynamic";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

export default async function AccountPage() {
  let access: Access | null = null;
  try {
    access = await api.getAccess(undefined, await serverToken());
  } catch {
    access = null;
  }

  return (
    <main>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Your plan</h1>
      </header>

      {!access ? (
        <p className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5 text-sm text-[var(--color-muted)]">
          Your plan is not available right now.
        </p>
      ) : access.plan === "readiness_pass" ? (
        <section className="rounded-lg border border-[var(--color-accent)]/60 bg-[var(--color-surface)] p-5">
          <p className="text-sm font-semibold">{access.offer.product_name}</p>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Full access{access.expires_at ? ` until ${formatDate(access.expires_at)} (UTC)` : ""}.
            It does not renew automatically.
          </p>
          <Link
            href="/tracks/CCAO-F/readiness"
            className="mt-4 inline-block text-sm text-[var(--color-accent)] hover:underline"
          >
            Go to your readiness &rarr;
          </Link>
        </section>
      ) : (
        <div className="space-y-6">
          <section className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5">
            <p className="text-sm font-semibold">Explorer (free)</p>
            <p className="mt-1 text-sm text-[var(--color-muted)]">
              A diagnostic exam, sample scenarios and a readiness preview, so you can see
              how evidence-based readiness works before you commit.
            </p>
          </section>
          <UpgradePanel
            headline="See the full evidence picture"
            outcome="Readiness Pass unlocks every practice exam and scenario, mastery bands, misconception detection and a remediation path built from your own evidence."
            checkoutAvailable={access.offer.checkout_available}
            priceUsd={access.offer.price_usd}
            durationDays={access.offer.duration_days}
          />
        </div>
      )}
    </main>
  );
}
