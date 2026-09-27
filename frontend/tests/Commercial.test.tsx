import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReadinessSummary, previewOutcome } from "@/components/ReadinessSummary";
import { UpgradePanel } from "@/components/UpgradePanel";
import type { DomainReadiness, TrackReadiness } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: {
    getOffer: vi.fn(async () => ({
      product_name: "ClaudeCertMastery Readiness Pass",
      subtitle: "90-Day CCAO-F Preparation",
      price_usd: 29,
      duration_days: 90,
      recurring: false,
      checkout_available: false,
    })),
  },
}));

function domain(code: string, position: number, overrides: Partial<DomainReadiness> = {}): DomainReadiness {
  return {
    domain_code: code,
    domain_name: `${code} name`,
    domain_position: position,
    readiness_state: "insufficient_evidence",
    reason_codes: ["NO_APPLIED_SCENARIO_EVIDENCE"],
    is_materialized: true,
    practice_evidence_count: 60,
    scenario_evidence_count: 0,
    distinct_scenario_content_versions: 0,
    recent_practice_mastery_band: "proficient",
    recent_scenario_mastery_band: null,
    most_recent_evidence_at: "2026-09-22T15:03:45Z",
    unresolved_misconception_count: 2,
    calculated_at: "2026-09-25T10:11:46Z",
    ...overrides,
  };
}

function readiness(overrides: Partial<TrackReadiness> = {}): TrackReadiness {
  return {
    track_code: "CCAO-F",
    track_name: "Claude Certified AI Operator - Foundation",
    overall_readiness_state: "insufficient_evidence",
    overall_reason_codes: ["NO_APPLIED_SCENARIO_EVIDENCE"],
    domains: [domain("PTE", 1)],
    next_action: {
      action: "ATTEMPT_SCENARIO",
      domain_code: "PTE",
      reason_codes: ["NO_APPLIED_SCENARIO_EVIDENCE"],
      scenario_external_id: "CCAO-F-PTE-SCN-003",
    },
    ...overrides,
  };
}

const preview = () =>
  readiness({
    depth: "preview",
    domains: [
      domain("PTE", 1, {
        recent_practice_mastery_band: null,
        recent_scenario_mastery_band: null,
        unresolved_misconception_count: null,
      }),
    ],
  });

describe("UpgradePanel", () => {
  it("never pretends checkout exists", () => {
    render(<UpgradePanel headline="h" outcome="Unlocks the full Scenario Lab." />);
    const cta = screen.getByRole("button", { name: "Unlock Readiness Pass" });
    expect(cta).toBeDisabled();
    expect(screen.getByText(/Checkout coming soon/)).toBeInTheDocument();
    expect(screen.getByText(/\$29 for 90 days, one payment, no subscription/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Compare plans" })).toHaveAttribute("href", "/pricing");
    expect(screen.getByText("Unlocks the full Scenario Lab.")).toBeInTheDocument();
  });
});

describe("ReadinessSummary entitlement presentation", () => {
  it("full readiness is unchanged: bands, misconceptions, no upgrade panel", () => {
    render(<ReadinessSummary readiness={readiness()} />);
    expect(screen.getByText("proficient")).toBeInTheDocument();
    expect(screen.getByText(/2 unresolved misconceptions detected/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Readiness Pass" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start CCAO-F-PTE-SCN-003" })).toBeInTheDocument();
  });

  it("preview shows withheld bands as Readiness Pass, never as 'none yet'", () => {
    render(<ReadinessSummary readiness={preview()} />);
    expect(screen.getAllByText("Readiness Pass", { selector: "span.italic" })).toHaveLength(2);
    expect(screen.queryByText("none yet")).not.toBeInTheDocument();
    expect(screen.queryByText(/misconception.*detected/)).not.toBeInTheDocument();
    // what evidence exists and what is missing stays visible
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText(/Needs first-attempt results from two different scenarios/)).toBeInTheDocument();
  });

  it("preview explains the specific value boundary from the learner's evidence", () => {
    render(<ReadinessSummary readiness={preview()} />);
    expect(
      screen.getByText(/You have practice evidence, but your readiness profile still needs independent scenario evidence/),
    ).toBeInTheDocument();
  });

  it("a locked recommendation explains the outcome instead of linking to a refusal", () => {
    const r = preview();
    r.next_action = { ...r.next_action, scenario_locked: true };
    render(<ReadinessSummary readiness={r} />);
    expect(screen.queryByRole("link", { name: /^Start / })).not.toBeInTheDocument();
    expect(screen.getByText(/Your evidence points to CCAO-F-PTE-SCN-003/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Unlock with Readiness Pass" })).toHaveAttribute("href", "/pricing");
  });

  it("never shows a probability or percentage", () => {
    const { container } = render(<ReadinessSummary readiness={preview()} />);
    expect(container.textContent).not.toMatch(/%|probability|guarantee/i);
  });
});

describe("previewOutcome", () => {
  it("chooses copy from evidence counts only", () => {
    expect(previewOutcome([domain("A", 1, { practice_evidence_count: 0 })])).toMatch(/Start with your free diagnostic/);
    expect(previewOutcome([domain("A", 1)])).toMatch(/needs independent scenario evidence/);
    expect(previewOutcome([domain("A", 1, { distinct_scenario_content_versions: 1 })])).toMatch(/mastery bands/);
  });
});

describe("Pricing page", () => {
  it("is one two-plan comparison with the backend price and an honest CTA", async () => {
    const { default: PricingPage } = await import("@/app/pricing/page");
    render(await PricingPage());
    expect(screen.getByText("Explorer")).toBeInTheDocument();
    expect(screen.getByText("ClaudeCertMastery Readiness Pass")).toBeInTheDocument();
    expect(screen.getByText("$0")).toBeInTheDocument();
    expect(screen.getByText("$29")).toBeInTheDocument();
    expect(screen.getByText("/ 90 days")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unlock Readiness Pass" })).toBeDisabled();
    expect(screen.getByText(/does not renew automatically/)).toBeInTheDocument();
    expect(screen.getByText(/not affiliated with or\s+endorsed by Anthropic/)).toBeInTheDocument();
  });
});
