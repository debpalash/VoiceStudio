import { LANG_CODES } from '@shared/utils/languages';

/**
 * Languages offered as translation targets (dub, batch dub, Tools → Translate).
 *
 * `LANG_CODES` doubles as the speech-recognition source list, so it holds only
 * languages Whisper can transcribe. A target only needs a translator and a
 * voice: Uyghur (#2810) has NLLB-200 (`uig_Arab`), Google and LLM translation,
 * and OmniVoice speaks it, but Whisper cannot transcribe it, so it is a target
 * here and never a source. Kabyle (Latin script) works the same way with
 * NLLB-200 (`kab_Latn`) and OmniVoice (`kab`), and no Whisper vocabulary.
 */
export const TARGET_ONLY_LANGUAGES = [
  { code: 'kab', label: 'Kabyle' },
  { code: 'ug', label: 'Uyghur' },
] as const;

/** `LANG_CODES` order, with each target-only language placed alphabetically. */
export const TRANSLATION_TARGETS: ReadonlyArray<{ code: string; label: string }> =
  TARGET_ONLY_LANGUAGES.reduce<{ code: string; label: string }[]>(
    (list, extra) => {
      const at = list.findIndex((item) => item.label.localeCompare(extra.label, 'en') > 0);
      list.splice(at < 0 ? list.length : at, 0, extra);
      return list;
    },
    [...LANG_CODES],
  );

export const TRANSLATION_TARGET_LABELS = TRANSLATION_TARGETS.map((item) => item.label);

/** ISO code for a target label, or undefined. */
export function translationTargetCode(label: string | undefined): string | undefined {
  return TRANSLATION_TARGETS.find((item) => item.label === label)?.code;
}

/** Target label for an ISO code, or undefined. */
export function translationTargetLabel(code: string | undefined): string | undefined {
  return code ? TRANSLATION_TARGETS.find((item) => item.code === code)?.label : undefined;
}
