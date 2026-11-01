const RUNTIME_IMPORT_MODULES = [
  'fastapi',
  'uvicorn',
  'omnivoice',
  'faster_whisper',
  'sentencepiece',
  'torch',
  'torchaudio',
  'torchvision',
] as const;
const IMPORT_MARKER = 'VOICESTUDIO_IMPORT=';

/** Import every runtime dependency, announcing each one before import. */
export const RUNTIME_IMPORT_PROBE = RUNTIME_IMPORT_MODULES.map(
  (name) => `print('${IMPORT_MARKER}${name}', flush=True)\nimport ${name}`,
).join('\n');

/** The dependency an import probe was loading when its output stopped. */
export function lastImportAttempted(output: unknown): string | null {
  const marked = String(output ?? '')
    .split(/\r?\n/)
    .filter((line) => line.startsWith(IMPORT_MARKER))
    .at(-1);
  return marked ? marked.slice(IMPORT_MARKER.length).trim() || null : null;
}
