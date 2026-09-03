import { create } from "zustand";

/**
 * Client-side selection state.
 *
 * Deliberately small in Session 1: only the chosen track lives here. Exam runner state
 * (current question, answers, timer, flags) arrives in Session 2, when there is a
 * runner to hold it.
 */
interface SelectionState {
  selectedTrackCode: string | null;
  selectTrack: (code: string | null) => void;
}

export const useSelection = create<SelectionState>((set) => ({
  selectedTrackCode: null,
  selectTrack: (code) => set({ selectedTrackCode: code }),
}));
