/**
 * Glossary slice — the active project's glossary, kept in sync with the
 * server. The store just caches what the server said last; the GlossaryPanel
 * component is the writer.
 *
 * Shape matches `GET /glossary/{project_id}`:
 *   { id, project_id, source, target, note, auto, created_at }
 */
import type { StateCreator } from 'zustand';

interface GlossaryTerm {
  id: string;
  project_id: string;
  source: string;
  target: string;
  note: string;
  auto: boolean;
  created_at: number;
}

export interface GlossarySlice {
  glossaryTerms: GlossaryTerm[];
  setGlossaryTerms: (next: GlossaryTerm[]) => void;
}

export const createGlossarySlice: StateCreator<GlossarySlice, [], [], GlossarySlice> = (set) => ({
  glossaryTerms: [],
  setGlossaryTerms: (next) => set({ glossaryTerms: next }),
});
