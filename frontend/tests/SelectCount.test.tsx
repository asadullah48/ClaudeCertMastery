import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ExamRunner } from "@/components/ExamRunner";
import { incompleteMultiResponse, useExam } from "@/lib/store";
import type { ExamQuestion } from "@/lib/types";

/** Multi-response items state how many to select, as the real exam does. */

const mr: ExamQuestion = {
  id: 7,
  external_id: "CCAO-F-OEV-004",
  stem: "Which checks belong in the validation step?",
  question_type: "mr",
  difficulty: 2,
  domain_code: "OEV",
  select_count: 2,
  options: [
    { id: 71, label: "A", text: "Trace figures to source", position: 1 },
    { id: 72, label: "B", text: "Check totals", position: 2 },
    { id: 73, label: "C", text: "Ask the model to self-certify", position: 3 },
  ],
};

beforeEach(() => {
  useExam.getState().reset();
  useExam.setState({
    ...useExam.getState(),
    attemptId: 1,
    trackCode: "CCAO-F",
    questions: [mr],
    index: 0,
    deadline: Date.now() + 60_000,
    status: "running",
  });
});

const submitButton = () => screen.getByRole("button", { name: "Submit exam" });

describe("Select N on multi-response items", () => {
  it("states the number to select instead of 'select all that apply'", () => {
    render(<ExamRunner />);
    expect(screen.getByText("Select 2.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/select all that apply/i);
  });

  it("disables submitting while an MR item holds fewer than N selections", () => {
    render(<ExamRunner />);
    fireEvent.click(screen.getByText("Trace figures to source"));
    expect(submitButton()).toBeDisabled();
    fireEvent.click(screen.getByText("Check totals"));
    expect(submitButton()).toBeEnabled();
  });

  it("will not take more than N selections", () => {
    const { select } = useExam.getState();
    act(() => {
      select(7, 71);
      select(7, 72);
      select(7, 73);
    });
    expect(useExam.getState().answers[7]).toEqual([71, 72]);
  });

  it("leaves a blank MR item submittable, scored as unanswered", () => {
    render(<ExamRunner />);
    expect(submitButton()).toBeEnabled();
    expect(incompleteMultiResponse([mr], {})).toEqual([]);
  });
});

describe("Table-format items", () => {
  it("renders the stem table with its header and every row", () => {
    useExam.setState({
      questions: [
        {
          ...mr,
          table: {
            columns: ["Plan", "Model tier", "Reviewer"],
            rows: [
              ["1", "Smaller, faster model", "None"],
              ["2", "Larger model", "Domain expert"],
            ],
          },
        },
      ],
    });
    render(<ExamRunner />);
    expect(screen.getByRole("columnheader", { name: "Model tier" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("Domain expert")).toBeInTheDocument();
  });
});
