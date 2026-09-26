/**
 * Floating Status Pill slice — P0 UI/UX Polish.
 *
 * Tracks the state of long-running background operations so the FloatingPill
 * component can show progress without prop-drilling from App.jsx. Any part of
 * the app can push a pill state (e.g. ASR model loading, dubbing progress,
 * export encoding) and the pill renders it with an elapsed timer.
 *
 * A floating on-screen indicator that walks through a state machine
 * (loading-model → recording → transcribing → done) with a live timer.
 */
import type { StateCreator } from 'zustand';

type PillStage =
  | 'idle'
  | 'loading-model'
  | 'recording'
  | 'transcribing'
  | 'translating'
  | 'generating'
  | 'exporting'
  | 'refining'
  | 'done'
  | 'error';

interface PillState {
  /** Current stage of the pill */
  stage: PillStage;
  /** Human-readable label for the current stage (e.g. "Loading ASR model…") */
  label: string;
  /** Optional progress 0–100. null = indeterminate. */
  progress: number | null;
  /** Timestamp when the current stage started (for elapsed timer) */
  startedAt: number | null;
  /** Error message if stage === 'error' */
  error: string | null;
  /** Whether the pill should be visible */
  visible: boolean;
  /** Whether the operation is cancellable */
  cancellable: boolean;
  /**
   * The workspace mode this operation "belongs to". When the user is already on
   * that mode, an in-context progress view (e.g. the dub PrepOverlay) is showing
   * the same thing, so the pill suppresses itself to avoid duplication. The pill
   * reappears the moment they navigate elsewhere. null = always show.
   */
  homeMode: string | null;
}

export interface PillSlice extends PillState {
  /** Push a new pill state. Resets startedAt automatically. */
  showPill: (
    stage: PillStage,
    label: string,
    opts?: {
      progress?: number | null;
      cancellable?: boolean;
      homeMode?: string | null;
    },
  ) => void;
  /** Update progress without changing stage */
  setPillProgress: (progress: number | null) => void;
  /** Update label without changing stage */
  setPillLabel: (label: string) => void;
  /** Dismiss the pill */
  dismissPill: () => void;
  /** Show a transient done state that auto-dismisses */
  completePill: (label?: string) => void;
  /** Show an error state */
  errorPill: (error: string) => void;
}

const INITIAL: PillState = {
  stage: 'idle',
  label: '',
  progress: null,
  startedAt: null,
  error: null,
  visible: false,
  cancellable: false,
  homeMode: null,
};

export const createPillSlice: StateCreator<PillSlice, [], [], PillSlice> = (set) => ({
  ...INITIAL,

  showPill: (stage, label, opts) =>
    set({
      stage,
      label,
      progress: opts?.progress ?? null,
      startedAt: Date.now(),
      error: null,
      visible: true,
      cancellable: opts?.cancellable ?? false,
      homeMode: opts?.homeMode ?? null,
    }),

  setPillProgress: (progress) => set({ progress }),
  setPillLabel: (label) => set({ label }),

  dismissPill: () => set(INITIAL),

  completePill: (label = 'Done') => {
    set({
      stage: 'done',
      label,
      progress: 100,
      error: null,
      cancellable: false,
      visible: true,
    });
    // Auto-dismiss after 3s
    setTimeout(() => {
      set((s) => (s.stage === 'done' ? INITIAL : s));
    }, 3000);
  },

  errorPill: (error) =>
    set({
      stage: 'error',
      label: 'Error',
      progress: null,
      error,
      cancellable: false,
      visible: true,
    }),
});
