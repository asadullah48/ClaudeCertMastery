import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ExamRunner } from "@/components/ExamRunner";
import { useExam } from "@/lib/store";
import type { ExamQuestion } from "@/lib/types";

/** Existing-flow regression check (Slice 3 Section 24): confirms adding Scenario Lab
 * did not disturb the exam runner it deliberately reuses conventions from. */

const question: ExamQuestion = {
  id: 1,
  external_id: "CCAO-F-PTE-001",
  stem: "Which approach is best?",
  question_type: "mcq",
  difficulty: 2,
  domain_code: "PTE",
  options: [
    { id: 1, label: "A", text: "Option A", position: 1 },
    { id: 2, label: "B", text: "Option B", position: 2 },
  ],
};

beforeEach(() => {
  useExam.getState().reset();
  useExam.setState({
    ...useExam.getState(),
    attemptId: 1,
    trackCode: "CCAO-F",
    questions: [question],
    index: 0,
    deadline: Date.now() + 60_000,
    status: "running",
  });
});

describe("Existing exam flow remains intact", () => {
  it("still renders the question, its options, and no scenario-lab content", () => {
    render(<ExamRunner />);
    expect(screen.getByText("Which approach is best?")).toBeInTheDocument();
    expect(screen.getByText("Option A")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Answer options" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Commit decision/);
  });
});
