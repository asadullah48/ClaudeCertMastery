import Link from "next/link";
import { UpgradePanel } from "@/components/UpgradePanel";
import { api } from "@/lib/api";
import type { Offer } from "@/lib/types";

export const dynamic = "force-dynamic";

const EXPLORER = [
  "Your learner account and evidence record",
  "One complete diagnostic exam with score and domain breakdown",
  "Two sample Scenario Lab scenarios",
  "Readiness preview: state per domain and what evidence is missing",
  "Your recommended next step",
];

const PASS = [
  "Unlimited practice exams across the full blueprint",
  "The complete Scenario Lab",
  "Full readiness: practice and scenario bands per domain",
  "Misconception detection and remediation guidance",
  "Personalized next actions, including locked recommendations",
  "Evidence history, readiness reporting and retake practice",
];

function Column({
  name,
  price,
  period,
  items,
  highlight,
}: {
  name: string;
  price: string;
  period: string;
  items: string[];
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border bg-[var(--color-surface)] p-6 ${
        highlight ? "border-[var(--color-accent)]/60" : "border-[var(--color-edge)]"
      }`}
    >
      <h2 className="text-sm font-semibold">{name}</h2>
      <p className="mt-3">
        <span className="text-3xl font-semibold tracking-tight">{price}</span>
        <span className="ml-1 text-sm text-[var(--color-muted)]">{period}</span>
      </p>
      <ul className="mt-5 space-y-2 text-sm leading-relaxed">
        {items.map((item) => (
          <li key={item} className="flex gap-2">
            <span aria-hidden="true" className="text-[var(--color-accent)]">
              &#10003;
            </span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default async function PricingPage() {
  let offer: Offer | null = null;
  try {
    offer = await api.getOffer();
  } catch {
    offer = null; // the page still explains both plans; checkout stays unavailable
  }
  const price = offer?.price_usd ?? 29;
  const days = offer?.duration_days ?? 90;

  return (
    <main>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Plans</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">
          Practice tells you what you scored. ClaudeCertMastery tells you what your evidence
          says you should work on next. Start free; add the Readiness Pass when you want
          the full evidence picture.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <Column name="Explorer" price="$0" period="free" items={EXPLORER} />
        <Column
          name={offer?.product_name ?? "Readiness Pass"}
          price={`$${price}`}
          period={`/ ${days} days`}
          items={PASS}
          highlight
        />
      </div>

      <div className="mt-6">
        <UpgradePanel
          headline={offer?.subtitle ?? "90-Day CCAO-F Preparation"}
          outcome={`Launch price. One payment covers ${days} days of access; it does not renew automatically.`}
          checkoutAvailable={offer?.checkout_available ?? false}
          priceUsd={price}
          durationDays={days}
        />
      </div>

      <p className="mt-8 max-w-2xl text-xs leading-relaxed text-[var(--color-muted)]">
        ClaudeCertMastery is an independent preparation platform, not affiliated with or
        endorsed by Anthropic. Readiness is our own evidence-based estimate. It is not an
        official score and it does not predict or guarantee certification results.{" "}
        <Link href="/methodology" className="text-[var(--color-accent)] hover:underline">
          How readiness works
        </Link>
      </p>
    </main>
  );
}
