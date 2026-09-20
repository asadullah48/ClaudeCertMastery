import { create } from "zustand";
import { api, ApiError } from "./api";
import type {
  ScenarioHintResponse,
  ScenarioResult,
  ScenarioStep,
  ScenarioStepAnswerResponse,
} from "./types";

export type ScenarioStatus =
  | "idle"
  | "loading"
  | "situation" // current step is presented, awaiting a decision
  | "committing" // answer request in flight
  | "consequence" // last answer's feedback is being shown
  | "submitted" // scenario complete
  | "error";

/** Distinguishes recovery UI: a content-version conflict or a completed attempt needs
 * a restart, a not-found attempt needs a restart, a transient failure just needs a
 * retry that keeps the learner's selection (Slice 2's idempotent-replay guarantee is
 * exactly what makes that retry safe). */
export type ScenarioErrorKind = "conflict" | "not_found" | "transient" | null;

interface ScenarioState {
  attemptId: number | null;
  scenarioExternalId: string | null;
  title: string | null;
  setupText: string | null;
  domainCode: string | null;
  currentStep: ScenarioStep | null;
  selectedOptionIds: number[];
  hintsRevealed: ScenarioHintResponse[]; // for the current step only
  lastAnswer: ScenarioStepAnswerResponse | null;
  result: ScenarioResult | null;
  status: ScenarioStatus;
  error: string | null;
  errorKind: ScenarioErrorKind;

  start: (externalId: string) => Promise<void>;
  /** Resumes an in-progress attempt if one was left running for this scenario --
   * Slice 2's GET /scenario-attempts/{id} already returns everything needed. Only
   * possible because the attempt id is persisted (localStorage), not because any
   * client-side state is trusted as truth: a stale/foreign id simply 404s and this
   * falls through to a fresh start. */
  resume: (externalId: string) => Promise<boolean>;
  selectOption: (optionId: number) => void;
  revealHint: () => Promise<void>;
  commit: () => Promise<void>;
  continueToNext: () => void;
  reset: () => void;
}

const initial = {
  attemptId: null,
  scenarioExternalId: null,
  title: null,
  setupText: null,
  domainCode: null,
  currentStep: null as ScenarioStep | null,
  selectedOptionIds: [] as number[],
  hintsRevealed: [] as ScenarioHintResponse[],
  lastAnswer: null as ScenarioStepAnswerResponse | null,
  result: null as ScenarioResult | null,
  status: "idle" as ScenarioStatus,
  error: null as string | null,
  errorKind: null as ScenarioErrorKind,
};

function storageKey(externalId: string): string {
  return `scenario_attempt:${externalId}`;
}

function persistAttemptId(externalId: string, attemptId: number | null) {
  try {
    if (attemptId === null) window.localStorage.removeItem(storageKey(externalId));
    else window.localStorage.setItem(storageKey(externalId), String(attemptId));
  } catch {
    // Private browsing / storage disabled: resume simply will not work this visit.
    // The scenario itself is unaffected -- server state remains authoritative.
  }
}

function readPersistedAttemptId(externalId: string): number | null {
  try {
    const raw = window.localStorage.getItem(storageKey(externalId));
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

function classifyError(e: unknown): { message: string; kind: ScenarioErrorKind } {
  if (e instanceof ApiError) {
    if (e.status === 409) {
      return {
        message:
          e.detail ??
          "This scenario attempt can no longer continue as it was. Start a new attempt.",
        kind: "conflict",
      };
    }
    if (e.status === 404) {
      return {
        message: "This scenario session could not be found. Start a new attempt.",
        kind: "not_found",
      };
    }
    if (e.status === 422) {
      // Should never happen from this UI (options are only ever chosen from the
      // current step's own list), but fails safely rather than silently.
      return {
        message: "That selection was not valid for this step. Start a new attempt.",
        kind: "conflict",
      };
    }
  }
  return {
    message: "Something went wrong reaching the server. Your selection is still here.",
    kind: "transient",
  };
}

export const useScenario = create<ScenarioState>((set, get) => ({
  ...initial,

  start: async (externalId) => {
    set({ ...initial, status: "loading", scenarioExternalId: externalId });
    try {
      const res = await api.startScenario(externalId);
      persistAttemptId(externalId, res.attempt_id);
      set({
        attemptId: res.attempt_id,
        title: res.title,
        setupText: res.setup_text,
        domainCode: res.domain_code,
        currentStep: res.current_step,
        status: "situation",
      });
    } catch (e) {
      const { message, kind } = classifyError(e);
      set({ status: "error", error: message, errorKind: kind });
    }
  },

  resume: async (externalId) => {
    const attemptId = readPersistedAttemptId(externalId);
    if (attemptId === null) return false;

    set({ ...initial, status: "loading", scenarioExternalId: externalId });
    try {
      const res = await api.getScenarioAttempt(attemptId);
      if (res.status === "submitted") {
        set({
          attemptId,
          scenarioExternalId: res.scenario_external_id,
          result: res.result,
          status: "submitted",
        });
        persistAttemptId(externalId, null); // a finished attempt has nothing to resume
        return true;
      }
      if (res.current_step) {
        set({
          attemptId,
          scenarioExternalId: res.scenario_external_id,
          currentStep: res.current_step,
          status: "situation",
        });
        return true;
      }
      // Neither submitted nor a current step -- treat as unresumable rather than guess.
      persistAttemptId(externalId, null);
      set({ ...initial, scenarioExternalId: externalId, status: "idle" });
      return false;
    } catch {
      // A stale/foreign/expired attempt id: clear it and let the caller start fresh.
      // Never fabricate a resumed state from a failed lookup.
      persistAttemptId(externalId, null);
      set({ ...initial, scenarioExternalId: externalId, status: "idle" });
      return false;
    }
  },

  selectOption: (optionId) => {
    const { currentStep, selectedOptionIds } = get();
    if (!currentStep) return;
    const next =
      currentStep.step_type === "mcq"
        ? selectedOptionIds.includes(optionId)
          ? []
          : [optionId]
        : selectedOptionIds.includes(optionId)
          ? selectedOptionIds.filter((id) => id !== optionId)
          : [...selectedOptionIds, optionId].sort((a, b) => a - b);
    set({ selectedOptionIds: next });
  },

  revealHint: async () => {
    const { attemptId, currentStep } = get();
    if (attemptId === null || !currentStep) return;
    try {
      const hint = await api.revealHint(attemptId, currentStep.position);
      set({ hintsRevealed: [...get().hintsRevealed, hint] });
    } catch (e) {
      const { message, kind } = classifyError(e);
      set({ error: message, errorKind: kind });
    }
  },

  commit: async () => {
    const { attemptId, currentStep, selectedOptionIds, status } = get();
    if (attemptId === null || !currentStep || status === "committing") return;
    if (selectedOptionIds.length === 0) return;

    set({ status: "committing", error: null, errorKind: null });
    try {
      const res = await api.answerStep(attemptId, currentStep.position, {
        selected_option_ids: selectedOptionIds,
      });
      set({ lastAnswer: res, status: "consequence" });
    } catch (e) {
      // The selection is deliberately left in place: Slice 2's idempotent-replay
      // guarantee means retrying this exact commit is always safe, whether the first
      // attempt never reached the server or succeeded but the response was lost.
      const { message, kind } = classifyError(e);
      set({ status: "situation", error: message, errorKind: kind });
    }
  },

  continueToNext: () => {
    const { lastAnswer, scenarioExternalId } = get();
    if (!lastAnswer) return;
    if (lastAnswer.attempt_status === "submitted" || !lastAnswer.next_step) {
      if (scenarioExternalId) persistAttemptId(scenarioExternalId, null);
      set({ status: "submitted", result: lastAnswer.result, lastAnswer: null });
      return;
    }
    set({
      currentStep: lastAnswer.next_step,
      lastAnswer: null,
      selectedOptionIds: [],
      hintsRevealed: [],
      status: "situation",
    });
  },

  reset: () => {
    const { scenarioExternalId } = get();
    if (scenarioExternalId) persistAttemptId(scenarioExternalId, null);
    set({ ...initial });
  },
}));
