import VoiceSelector from '../VoiceSelector';

/**
 * Cast / voice mapping (#1217) — the multi-voice fix's UI surface. Lists each
 * DISTINCT `[voice:NAME]` name found in the script and lets the user map it to a
 * voice profile. The map is persisted in the store (`voiceCast`) and sent to the
 * backend as `voice_map` so `[voice:Mara]` actually renders in Mara's voice
 * instead of silently falling back to the engine default.
 *
 * Only names actually present in the script are shown; an unmapped name reads as
 * "uses Default voice".
 *
 * @param {Function} t                 i18n
 * @param {string[]} castNames         distinct [voice:NAME] names in the script
 * @param {Record<string,string>} voiceCast  name → profile id
 * @param {(name:string, profileId:string|null)=>void} setVoiceCast
 * @param {Array}    profiles          voice profiles for the selector
 */
export default function CastPanel({
  t,
  castNames = [],
  voiceCast = {},
  setVoiceCast,
  profiles = [],
}) {
  if (!castNames.length) {
    return (
      <p className="muted text-[var(--text-sm)] text-fg-muted m-0">{t('audiobook.cast_empty')}</p>
    );
  }
  return (
    <div className="flex flex-col gap-[7px]">
      {castNames.map((name) => {
        const mapped = voiceCast[name] || '';
        return (
          <div
            key={name}
            className="grid grid-cols-[minmax(72px,0.7fr)_minmax(0,1.3fr)] items-center gap-[7px]"
          >
            <code className="truncate text-[0.68rem] text-fg" title={`[voice:${name}]`}>
              {name}
            </code>
            <VoiceSelector
              value={mapped}
              onChange={(v) => setVoiceCast(name, v || null)}
              profiles={profiles}
              defaultLabel={t('audiobook.engine_default')}
              ariaLabel={`${t('audiobook.cast')}: ${name}`}
            />
          </div>
        );
      })}
    </div>
  );
}
