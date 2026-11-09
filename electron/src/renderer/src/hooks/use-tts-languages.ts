import { useEngines } from './use-engines';

/** Read supported languages from the active local TTS engine. */
export function useTtsLanguages(_operation = 'clone') {
  const engines = useEngines();
  const names = engines.isError ? null : engines.activeTts?.supported_language_names;
  return {
    names,
    /** Regional accents the active model renders itself (#3143). */
    dialects: engines.isError ? null : (engines.activeTts?.rendered_dialects ?? null),
    modelLabel:
      engines.data?.tts?.active_model?.split('/').pop() ||
      engines.activeTts?.display_name?.split(' (')[0],
    state: engines.isLoading
      ? ('loading' as const)
      : engines.isError
        ? ('error' as const)
        : names == null
          ? ('unknown' as const)
          : ('known' as const),
  };
}
