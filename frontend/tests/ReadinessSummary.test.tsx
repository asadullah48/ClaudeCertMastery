import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReadinessSummary } from "@/components/ReadinessSummary";
import { ScenarioIntro } from "@/components/ScenarioIntro";
import type { DomainReadiness, TrackReadiness } from "@/lib/types";

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
    unresolved_misconception_count: 0,
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
    domains: [
      domain("OEV", 2),
      domain("PTE", 1, {
        scenario_evidence_count: 1,
        distinct_scenario_content_versions: 1,
        recent_scenario_mastery_band: "strong",
      }),
    ],
    next_action: {
      action: "ATTEMPT_SCENARIO",
      domain_code: "OEV",
      reason_codes: ["NO_APPLIED_SCENARIO_EVIDENCE"],
      scenario_external_id: "CCAO-F-OEV-SCN-001",
    },
    ...overrides,
  };
}

describe("ReadinessSummary", () => {
  it("links the recommended next step to the specific unseen scenario", () => {
    render(<ReadinessSummary readiness={readiness()} />);
    const link = screen.getByRole("link", { name: "Start CCAO-F-OEV-SCN-001" });
    expect(link).toHaveAttribute("href", "/tracks/CCAO-F/scenarios/CCAO-F-OEV-SCN-001");
    expect(screen.getByText(/Only a first attempt counts as evidence/)).toBeInTheDocument();
  });

  it("explains reasons in plain language and lists domains in blueprint order", () => {
    render(<ReadinessSummary readiness={readiness()} />);
    expect(
      screen.getAllByText("Needs first-attempt results from two different scenarios.").length,
    ).toBe(2);
    const codes = screen.getAllByText(/^(PTE|OEV)$/).map((el) => el.textContent);
    expect(codes).toEqual(["PTE", "OEV"]);
  });

  it("never presents readiness as a score, probability or official prediction", () => {
    render(<ReadinessSummary readiness={readiness()} />);
    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/%/);
    expect(body.toLowerCase()).not.toMatch(/probability|likely to pass|you will pass/);
    expect(body).toMatch(/not an official Anthropic score/);
    expect(body).toMatch(/not a prediction of\s+certification results/);
  });

  it("sends a fully-seen domain to practice rather than a repeat", () => {
    render(
      <ReadinessSummary
        readiness={readiness({
          next_action: {
            action: "COLLECT_PRACTICE_EVIDENCE",
            domain_code: "PTE",
            reason_codes: [],
            scenario_external_id: null,
          },
        })}
      />,
    );
    expect(screen.getByRole("link", { name: "Start a practice exam" })).toHaveAttribute(
      "href",
      "/tracks/CCAO-F/exam",
    );
  });
});

describe("ScenarioIntro retake notice", () => {
  it("warns that a seen scenario is practice only", () => {
    render(
      <ScenarioIntro
        title="The Vague Brief"
        domainCode="PTE"
        loading={false}
        onBegin={() => {}}
        countsAsEvidence={false}
      />,
    );
    expect(screen.getByText(/Practice only/)).toBeInTheDocument();
  });

  it("shows no notice for a scenario that still counts", () => {
    render(
      <ScenarioIntro title="The Overloaded Prompt" domainCode="PTE" loading={false} onBegin={() => {}} />,
    );
    expect(screen.queryByText(/Practice only/)).not.toBeInTheDocument();
  });
});
