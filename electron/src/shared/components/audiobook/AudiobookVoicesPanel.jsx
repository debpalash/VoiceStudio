import { Mic2, Languages, Users } from 'lucide-react';

import VoiceSelector from '../VoiceSelector';
import SearchableSelect from '../SearchableSelect';
import CastPanel from './CastPanel';
import AudiobookOverrides from './AudiobookOverrides';
import ALL_LANGUAGES from '../../languages.json';
import { POPULAR_LANGS } from '../../utils/constants';

/**
 * AudiobookVoicesPanel — the Cast tab: who says it.
 *
 * Default voice + language stay pinned on top (the two controls every book
 * needs); the `[voice:NAME]` cast map and the expressive overrides follow as
 * labelled groups. Mirrors the clone workspace's Voice section (label-row
 * kickers, borderless surfaces).
 */
export default function AudiobookVoicesPanel({
  t,
  profiles,
  defaultVoice,
  setDefaultVoice,
  language,
  setLanguage,
  castNames,
  voiceCast,
  setVoiceCast,
  overrides,
  setOverrides,
  emotionSupported,
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[14px] overflow-y-auto px-[2px] py-[2px]">
      <div className="grid grid-cols-2 gap-[10px] max-[640px]:grid-cols-1">
        <div className="flex min-w-0 flex-col gap-[4px]">
          <span className="label-row mb-0">
            <Mic2 className="label-icon" size={14} aria-hidden="true" />{' '}
            {t('audiobook.default_voice')}
          </span>
          <VoiceSelector
            value={defaultVoice}
            onChange={setDefaultVoice}
            profiles={profiles}
            defaultLabel={t('audiobook.engine_default')}
            ariaLabel={t('audiobook.default_voice')}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-[4px]">
          <span className="label-row mb-0">
            <Languages className="label-icon" size={14} aria-hidden="true" />{' '}
            {t('audiobook.language')}
          </span>
          <SearchableSelect
            value={language}
            options={ALL_LANGUAGES}
            popular={POPULAR_LANGS}
            recentsKey="omnivoice.recents.audiobookLang"
            onChange={setLanguage}
            ariaLabel={t('audiobook.language')}
          />
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-[6px]">
        <span className="label-row mb-0">
          <Users className="label-icon" size={14} aria-hidden="true" /> {t('audiobook.cast')}
          {castNames.length > 0 && (
            <span
              className="ml-auto rounded-full bg-[var(--chrome-hover-bg)] px-[8px] text-center [font-variant-numeric:tabular-nums]"
              aria-hidden="true"
            >
              {castNames.length}
            </span>
          )}
        </span>
        <CastPanel
          t={t}
          castNames={castNames}
          voiceCast={voiceCast}
          setVoiceCast={setVoiceCast}
          profiles={profiles}
        />
      </div>

      <AudiobookOverrides
        t={t}
        overrides={overrides}
        onChange={setOverrides}
        emotionSupported={emotionSupported}
      />
    </div>
  );
}
