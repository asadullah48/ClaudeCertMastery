import { create } from "zustand";
import { api } from "./api";
import type { ExamQuestion, ExamResult, SubmitAnswer } from "./types";

/**
 * Client-side selection state.
 *
 * Deliberately small: only the chosen track lives here. Everything about a sitting in
 * progress lives in useExam below.
 */
interface SelectionState {
  selectedTrackCode: string | null;
  selectTrack: (code: string | null) => void;
}

export const useSelection = create<SelectionState>((set) => ({
  selectedTrackCode: null,
  selectTrack: (code) => set({ selectedTrackCode: code }),
}));

export type ExamStatus = "idle" | "loading" | "running" | "submitting" | "done";

/** Why the sitting ended. Recorded because it changes how the result is presented. */
export type EndReason = "submitted" | "expired" | null;

interface ExamState {
  attemptId: number | null;
  trackCode: string | null;
  questions: ExamQuestion[];

  /** questionId -> selected option ids. Absent key means unanswered. */
  answers: Record<number, number[]>;
  /** questionId -> true. Flag-for-review is a navigation aid, not part of grading. */
  flagged: Record<number, boolean>;
  /** questionId -> seconds accumulated. Fed to the backend for per-item analytics. */
  timeSpent: Record<number, number>;

  index: number;
  /** Epoch ms when the exam must end. Absolute, not a countdown integer -- see below. */
  deadline: number | null;
  compositionWarning: string | null;

  status: ExamStatus;
  endReason: EndReason;
  result: ExamResult | null;
  error: string | null;

  start: (trackCode: string, itemCount?: number) => Promise<void>;
  select: (questionId: number, optionId: number) => void;
  toggleFlag: (questionId: number) => void;
  goTo: (index: number) => void;
  tickTime: (questionId: number, seconds: number) => void;
  submit: () => Promise<void>;
  onTimeExpiry: () => void;
  reset: () => void;
}

const initial = {
  attemptId: null,
  trackCode: null,
  questions: [] as ExamQuestion[],
  answers: {} as Record<number, number[]>,
  flagged: {} as Record<number, boolean>,
  timeSpent: {} as Record<number, number>,
  index: 0,
  deadline: null,
  compositionWarning: null,
  status: "idle" as ExamStatus,
  endReason: null as EndReason,
  result: null,
  error: null,
};

export const useExam = create<ExamState>((set, get) => ({
  ...initial,

  start: async (trackCode, itemCount) => {
    set({ ...initial, status: "loading", trackCode });
    try {
      const exam = await api.generateExam({
        track_code: trackCode,
        item_count: itemCount,
      });
      set({
        attemptId: exam.attempt_id,
        questions: exam.questions,
        compositionWarning: exam.composition_warning,
        // Stored as an absolute epoch deadline rather than a decrementing counter.
        // A counter drifts: background tabs throttle setInterval to once a minute or
        // less, so a 120-minute exam would end up minutes long in wall-clock terms.
        // Comparing against Date.now() makes the clock correct regardless of how often
        // the tick actually fires.
        deadline: Date.now() + exam.duration_minutes * 60_000,
        status: "running",
      });
    } catch (e) {
      set({
        status: "idle",
        error: e instanceof Error ? e.message : "Could not start the exam.",
      });
    }
  },

  select: (questionId, optionId) => {
    const { questions, answers } = get();
    const question = questions.find((q) => q.id === questionId);
    if (!question) return;

    const current = answers[questionId] ?? [];
    // MCQ replaces; MR toggles. Enforcing arity here rather than at submit time means
    // the UI can never show a state the grader would reject.
    const next =
      question.question_type === "mcq"
        ? current.includes(optionId)
          ? [] // clicking the chosen option again clears it
          : [optionId]
        : current.includes(optionId)
          ? current.filter((id) => id !== optionId)
          : [...current, optionId].sort((a, b) => a - b);

    set({ answers: { ...answers, [questionId]: next } });
  },

  toggleFlag: (questionId) => {
    const flagged = { ...get().flagged };
    if (flagged[questionId]) delete flagged[questionId];
    else flagged[questionId] = true;
    set({ flagged });
  },

  goTo: (index) => {
    const { questions } = get();
    if (index < 0 || index >= questions.length) return;
    set({ index });
  },

  tickTime: (questionId, seconds) => {
    const timeSpent = get().timeSpent;
    set({ timeSpent: { ...timeSpent, [questionId]: (timeSpent[questionId] ?? 0) + seconds } });
  },

  submit: async () => {
    const { attemptId, questions, answers, flagged, timeSpent, status } = get();
    if (attemptId === null || status === "submitting") return;

    set({ status: "submitting" });

    // Every question is sent, including unanswered ones. Omitting them would make an
    // unanswered item indistinguishable from one the backend never saw, and the
    // per-domain rollup depends on knowing the true denominator.
    const payload: SubmitAnswer[] = questions.map((q) => ({
      question_id: q.id,
      selected_option_ids: answers[q.id] ?? [],
      time_spent_seconds: Math.round(timeSpent[q.id] ?? 0),
      flagged_for_review: Boolean(flagged[q.id]),
    }));

    try {
      const result = await api.submitAttempt(attemptId, payload);
      set({
        result,
        status: "done",
        endReason: get().endReason ?? "submitted",
      });
    } catch (e) {
      set({
        status: "running",
        error: e instanceof Error ? e.message : "Submission failed. Your answers are kept.",
      });
    }
  },

  /**
   * Called exactly once when the deadline passes (D-15).
   *
   * Hard auto-submit: whatever is on screen is graded, and unanswered items count as
   * incorrect. The alternatives -- locking input for a manual submit, a grace period, or
   * stopping the clock -- all make a mis-paced sitting feel kinder, and all of them
   * corrupt the one signal this product exists to give. A candidate who needs 140
   * minutes for a 120-minute exam has not passed it, and a practice tool that lets them
   * believe otherwise has failed at the job it was bought for.
   *
   * The review screen labels an expired sitting explicitly, so a result is never
   * silently conflated with one finished inside the time.
   */
  onTimeExpiry: () => {
    set({ endReason: "expired" });
    void get().submit();
  },

  reset: () => set({ ...initial }),
}));
