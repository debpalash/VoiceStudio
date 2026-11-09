import type { Draft, Mode } from './longform-session';

export function clearedScriptPatch(mode: Mode): Partial<Draft> {
  if (mode === 'stories') return { importText: '', lines: [] };
  return { script: '' };
}

export function scriptSize(mode: Mode, draft: Draft): number {
  if (mode === 'audiobook') return Number(Boolean(draft.script.trim()));
  const bufferedImport = Boolean(draft.importText?.trim());
  return draft.lines.length + Number(bufferedImport);
}
