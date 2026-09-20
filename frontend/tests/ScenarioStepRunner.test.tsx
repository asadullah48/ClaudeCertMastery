import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ScenarioStepRunner } from "@/components/ScenarioStepRunner";
import { ApiError, api } from "@/lib/api";
import { useScenario } from "@/lib/scenarioStore";
import type { ScenarioStep, ScenarioStepAnswerResponse } from "@/lib/types";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, answerStep: vi.fn(), revealHint: vi.fn() } };
});

const step1: ScenarioStep = {
  position: 1,
  total_steps: 2,
  prompt_text: "What now?",
  step_type: "mcq",
  options: [
    { id: 1, label: "A", text: "Route to review.", position: 1 },
    { id: 2, label: "B", text: "Send automatically.", position: 2 },
  ],
  hints_available: 0,
};

const step2: ScenarioStep = {
  position: 2,
  total_steps: 2,
  prompt_text: "Later...",
  step_type: "mcq",
  options: [{ id: 3, label: "A", text: "Adjust the policy.", position: 1 }],
  hints_available: 0,
};

function primeStore(overrides: Partial<ReturnType<typeof useScenario.getState>> = {}) {
  useScenario.setState({
    ...useScenario.getState(),
    attemptId: 1,
    scenarioExternalId: "CCAO-F-WISD-SCN-001",
    title: "The Silent Handoff",
    setupText: "An operator wired Claude into a ticketing system.",
    domainCode: "WISD",
    currentStep: step1,
    selectedOptionIds: [],
    hintsRevealed: [],
    lastAnswer: null,
    result: null,
    status: "situation",
    error: null,
    errorKind: null,
    ...overrides,
  });
}

beforeEach(() => {
  useScenario.getState().reset();
  vi.mocked(api.answerStep).mockReset();
  vi.mocked(api.revealHint).mockReset();
});

describe("Pre-decision integrity", () => {
  it("renders only learner-safe option data before any decision", () => {
    primeStore();
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    expect(screen.getByText("Route to review.")).toBeInTheDocument();
    expect(screen.getByText("Send automatically.")).toBeInTheDocument();
    // Nothing grading-sensitive appears anywhere on the page before commitment.
    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/misconception/i);
    expect(body).not.toMatch(/rationale/i);
  });
});

describe("Decision selection does not auto-commit", () => {
  it("selecting an option does not call the API", () => {
    primeStore();
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    fireEvent.click(screen.getByText("Route to review."));
    expect(api.answerStep).not.toHaveBeenCalled();
    // Selecting only enables the commit button and opens no dialog by itself.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Explicit commitment", () => {
  it("calls the backend exactly once after review + confirm", async () => {
    primeStore();
    const response: ScenarioStepAnswerResponse = {
      step_position: 1, is_correct: true, step_credit: 1.0,
      selected_option_ids: [1], correct_option_ids: [1],
      feedback: [{ option_id: 1, label: "A", is_correct: true, rationale: "Targets the blind spot.", misconception_tag: null }],
      attempt_status: "in_progress", next_step: step2, result: null,
    };
    vi.mocked(api.answerStep).mockResolvedValue(response);

    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    fireEvent.click(screen.getByText("Route to review."));
    fireEvent.click(screen.getByText("Commit decision"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getAllByText("Commit decision")[1]); // the dialog's confirm button
    await waitFor(() => expect(api.answerStep).toHaveBeenCalledTimes(1));
    expect(api.answerStep).toHaveBeenCalledWith(1, 1, { selected_option_ids: [1] });
  });
});

describe("Duplicate protection", () => {
  it("rapid repeated confirm clicks still call the backend once", async () => {
    primeStore({ selectedOptionIds: [1] });
    let resolveFn!: (v: ScenarioStepAnswerResponse) => void;
    vi.mocked(api.answerStep).mockReturnValue(
      new Promise((resolve) => {
        resolveFn = resolve;
      }),
    );

    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );

    // Fire both calls while the first is still in flight -- neither is awaited to
    // completion here, since the mock will not resolve until told to below. The
    // store's own status==="committing" guard is what must stop the second call.
    let firstCall: Promise<void>;
    await act(async () => {
      firstCall = useScenario.getState().commit();
      await useScenario.getState().commit(); // second, rapid, while first pending
    });
    expect(api.answerStep).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFn({
        step_position: 1, is_correct: true, step_credit: 1, selected_option_ids: [1],
        correct_option_ids: [1], feedback: [], attempt_status: "in_progress",
        next_step: step2, result: null,
      });
      await firstCall;
    });
  });
});

describe("Consequence and reflection", () => {
  it("renders consequence and reflection only after commitment, and hides the raw misconception tag", async () => {
    primeStore({
      status: "consequence",
      lastAnswer: {
        step_position: 1, is_correct: false, step_credit: 0,
        selected_option_ids: [2], correct_option_ids: [1],
        feedback: [
          {
            option_id: 2, label: "B", is_correct: false,
            rationale: "Fluent, confident prose is not evidence the model is grounded.",
            misconception_tag: "confidence_as_correctness",
          },
        ],
        attempt_status: "in_progress", next_step: step2, result: null,
      },
    });
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );

    expect(
      screen.getByText("Fluent, confident prose is not evidence the model is grounded."),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("confidence_as_correctness");
    expect(screen.getByText(/Outcome: Needs correction/)).toBeInTheDocument();
  });
});

describe("Progression", () => {
  it("Continue renders the next step only after the server response, never before", async () => {
    primeStore({
      status: "consequence",
      lastAnswer: {
        step_position: 1, is_correct: true, step_credit: 1, selected_option_ids: [1],
        correct_option_ids: [1], feedback: [], attempt_status: "in_progress",
        next_step: step2, result: null,
      },
    });
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    expect(screen.queryByText("Later...")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Continue"));
    expect(screen.getByText("Later...")).toBeInTheDocument();
  });
});

describe("Error handling", () => {
  it("a transient failure preserves the learner's selection and offers no destructive restart", async () => {
    primeStore({ selectedOptionIds: [1] });
    vi.mocked(api.answerStep).mockRejectedValue(new Error("network down"));
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    await act(async () => {
      await useScenario.getState().commit();
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/your selection is still here/i);
    expect(useScenario.getState().selectedOptionIds).toEqual([1]); // not cleared
    expect(screen.queryByText("Start a new attempt")).not.toBeInTheDocument();
  });

  it("a content-version conflict (409) offers explicit restart guidance", async () => {
    primeStore({ selectedOptionIds: [1] });
    vi.mocked(api.answerStep).mockRejectedValue(
      new ApiError("conflict", 409, "This scenario's content has changed since the attempt started."),
    );
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    await act(async () => {
      await useScenario.getState().commit();
    });
    expect(screen.getByText(/content has changed/i)).toBeInTheDocument();
    expect(screen.getByText("Start a new attempt")).toBeInTheDocument();
  });
});

describe("Accessibility", () => {
  it("options form a labeled group with aria-pressed state", () => {
    primeStore();
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    const group = screen.getByRole("group", { name: "Decision options" });
    expect(group).toBeInTheDocument();
    const optionButton = screen.getByText("Route to review.").closest("button")!;
    expect(optionButton).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(optionButton);
    expect(optionButton).toHaveAttribute("aria-pressed", "true");
  });

  it("the commit review step is an accessible modal dialog", () => {
    primeStore({ selectedOptionIds: [1] });
    render(
      <ScenarioStepRunner
        title="The Silent Handoff"
        domainCode="WISD"
        setupText="An operator wired Claude into a ticketing system."
      />,
    );
    fireEvent.click(screen.getByText("Commit decision"));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "commit-title");
  });
});
