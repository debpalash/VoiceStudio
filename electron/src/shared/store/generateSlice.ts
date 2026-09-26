/**
 * Generate-tab slice — Phase 2.2 (App.jsx monolith reduction).
 *
 * Owns the voice-synthesis form state that the Generate / Clone / Design tab
 * reads: prompt text, reference text, style instruct, language, the eight
 * production-override knobs, and the voice-design category picks. These used
 * to be ~13 `useState` calls at the top of App.jsx; consolidating them into
 * a slice removes a prop-drilling layer and lets us persist the knobs so
 * they survive reloads.
 *
 * Persisted: all config knobs + `vdStates` (user preferences). Transient:
 * none — even `text` is persisted because the old `omni_ui` localStorage
 * already did it.
 */
import type { StateCreator } from 'zustand';

type VDCategory = 'Gender' | 'Age' | 'Pitch' | 'Style' | 'EnglishAccent' | 'ChineseDialect';

type VDStates = Record<VDCategory, string>;

export interface GenerateSlice {
  // Prompt + source
  text: string;
  refText: string;
  instruct: string;
  language: string;

  // Production overrides
  speed: number;
  steps: number;
  cfg: number;
  tShift: number;
  posTemp: number;
  classTemp: number;
  layerPenalty: number;
  denoise: boolean;
  postprocess: boolean;
  duration: string;

  // Voice-design category picks
  vdStates: VDStates;

  // Voice-design seed (#526): the last seed the backend used, and whether to
  // reuse it on the next synth so voice tweaks stay on the same base timbre.
  designSeed: number | null;
  keepSeed: boolean;

  /**
   * How many synth requests are in flight right now.
   *
   * A COUNT, not a flag. Syntheses overlap — the Generate tab, voice previews,
   * the compare modal, the stories editor and profile previews can all be
   * running at once, and a boolean would be cleared by whichever finished
   * first while the others were still going, letting the updater relaunch and
   * discard them.
   *
   * Maintained by `api/generate.ts` around the single `/generate` call every
   * one of those paths goes through, so a new caller is covered without having
   * to remember this exists. Read via `utils/appBusy`. Transient: never
   * persisted, since a synth cannot survive a reload.
   */
  ttsInflight: number;

  setText: (v: string) => void;
  setRefText: (v: string) => void;
  setInstruct: (v: string) => void;
  setLanguage: (v: string) => void;

  setSpeed: (v: number) => void;
  setSteps: (v: number) => void;
  setCfg: (v: number) => void;
  setTShift: (v: number) => void;
  setPosTemp: (v: number) => void;
  setClassTemp: (v: number) => void;
  setLayerPenalty: (v: number) => void;
  setDenoise: (v: boolean) => void;
  setPostprocess: (v: boolean) => void;
  setDuration: (v: string) => void;

  setVdStates: (v: VDStates | ((prev: VDStates) => VDStates)) => void;

  setDesignSeed: (v: number | null) => void;
  setKeepSeed: (v: boolean) => void;
  /** +1 on synth start, -1 on settle. Floors at 0; never goes negative. */
  addTtsInflight: (delta: number) => void;
}

const INITIAL_VD: VDStates = {
  Gender: 'Auto',
  Age: 'Auto',
  Pitch: 'Auto',
  Style: 'Auto',
  EnglishAccent: 'Auto',
  ChineseDialect: 'Auto',
};

export const createGenerateSlice: StateCreator<GenerateSlice, [], [], GenerateSlice> = (set) => ({
  text: '',
  refText: '',
  instruct: '',
  language: 'Auto',

  speed: 1.0,
  steps: 16, // ~16 to avoid ODE destabilisation in the flow-matcher.
  cfg: 2.0,
  tShift: 0.1,
  posTemp: 5.0,
  classTemp: 0.0,
  layerPenalty: 5.0,
  denoise: true,
  postprocess: true,
  duration: '',

  vdStates: INITIAL_VD,

  designSeed: null,
  keepSeed: false,
  ttsInflight: 0,

  setText: (v) => set({ text: v }),
  setRefText: (v) => set({ refText: v }),
  setInstruct: (v) => set({ instruct: v }),
  setLanguage: (v) => set({ language: v }),

  setSpeed: (v) => set({ speed: v }),
  setSteps: (v) => set({ steps: v }),
  setCfg: (v) => set({ cfg: v }),
  setTShift: (v) => set({ tShift: v }),
  setPosTemp: (v) => set({ posTemp: v }),
  setClassTemp: (v) => set({ classTemp: v }),
  setLayerPenalty: (v) => set({ layerPenalty: v }),
  setDenoise: (v) => set({ denoise: v }),
  setPostprocess: (v) => set({ postprocess: v }),
  setDuration: (v) => set({ duration: v }),

  setVdStates: (v) =>
    set((s) => ({
      vdStates: typeof v === 'function' ? (v as (p: VDStates) => VDStates)(s.vdStates) : v,
    })),

  setDesignSeed: (v) => set({ designSeed: v }),
  setKeepSeed: (v) => set({ keepSeed: v }),
  addTtsInflight: (delta) => set((st) => ({ ttsInflight: Math.max(0, st.ttsInflight + delta) })),
});
