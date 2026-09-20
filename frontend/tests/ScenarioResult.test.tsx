import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScenarioResult } from "@/components/ScenarioResult";

describe("Completion makes no unsupported readiness/mastery claims", () => {
  it("shows only the scenario-level result, never a readiness/mastery claim", () => {
    render(
      <ScenarioResult
        title="The Silent Handoff"
        domainCode="WISD"
        scorePct={100}
        masteryBand="strong"
        trackCode="CCAO-F"
      />,
    );
    expect(screen.getByText("Scenario completed")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();

    const body = (document.body.textContent ?? "").toLowerCase();
    expect(body).not.toMatch(/certification ready/);
    expect(body).not.toMatch(/you are ready/);
    expect(body).not.toMatch(/mastery of/);
    expect(body).not.toMatch(/competency proficient/);
    // The scale distinction is explicit, so a 0-100 scenario score can never be
    // mistaken for the 100-1000 exam scale.
    expect(body).toMatch(/not a certification readiness score/);
  });
});
