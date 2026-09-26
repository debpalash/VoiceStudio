import {
  CheckCheck,
  Fingerprint,
  Languages,
  Mic,
  RotateCcw,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import SearchableSelect from '../SearchableSelect';
import LanguageFlag from '../LanguageFlag';
import { Button } from '../../ui';
import { LANG_CODES } from '../../utils/languages';
import { autoProfileId } from '../../utils/segments';
import './DubSelectionToolbar.css';

export default function DubSelectionToolbar({
  t,
  count,
  profiles = [],
  speakerClones = {},
  disabled = false,
  onApply,
  onDelete,
  onClear,
}) {
  const voiceOptions = [
    { value: '__clear__', label: t('dub.clear_voice'), Icon: RotateCcw },
    ...Object.keys(speakerClones || {}).map((speaker) => ({
      value: autoProfileId(speaker),
      label: speaker,
      Icon: Mic,
      group: 'cast',
      groupLabel: t('dub.cast'),
    })),
    ...profiles
      .filter((profile) => !profile.instruct)
      .map((profile) => ({
        value: profile.id,
        label: profile.name,
        Icon: Fingerprint,
        group: 'clones',
        groupLabel: t('dub.clone_profiles'),
      })),
    ...profiles
      .filter((profile) => profile.instruct)
      .map((profile) => ({
        value: profile.id,
        label: profile.name,
        Icon: Sparkles,
        group: 'designed',
        groupLabel: t('dub.design_presets'),
      })),
  ];
  return (
    <div className="dub-selection-toolbar">
      <div className="dub-selection-heading">
        <span className="dub-selection-count" aria-live="polite">
          <CheckCheck size={17} aria-hidden="true" />
          {t('dub.selected_count', { count })}
        </span>
        <Button variant="ghost" size="sm" onClick={onClear}>
          <X size={14} aria-hidden="true" /> {t('dub.clear_selection')}
        </Button>
      </div>
      <div className="dub-selection-fields">
        <div className="dub-selection-picker">
          <Mic size={16} aria-hidden="true" />
          <SearchableSelect
            value=""
            placeholder={t('dub.set_voice')}
            ariaLabel={t('dub.set_voice')}
            disabled={disabled}
            menuPortal
            renderGroupHeaders
            buttonClassName="dub-selection-trigger"
            options={voiceOptions}
            onChange={(value) => onApply({ profile_id: value === '__clear__' ? '' : value })}
            renderOption={(option) => (
              <span className="inline-flex items-center gap-2">
                <option.Icon size={16} aria-hidden="true" className="shrink-0 opacity-75" />
                <span>{option.label}</span>
              </span>
            )}
          />
        </div>
        <div className="dub-selection-picker">
          <Languages size={16} aria-hidden="true" />
          <SearchableSelect
            value=""
            placeholder={t('dub.set_lang')}
            ariaLabel={t('dub.set_lang')}
            disabled={disabled}
            menuPortal
            buttonClassName="dub-selection-trigger"
            options={[
              { value: '__def__', label: t('dub.default_lang') },
              ...LANG_CODES.map((lang) => ({ value: lang.code, label: lang.label })),
            ]}
            onChange={(value) => onApply({ target_lang: value === '__def__' ? null : value })}
            renderOption={(option) => (
              <span className="flex items-center gap-2">
                {option.value === '__def__' ? (
                  <RotateCcw size={16} aria-hidden="true" />
                ) : (
                  <LanguageFlag code={option.value} className="h-3 w-[18px] shrink-0 rounded-sm" />
                )}
                <span className="flex-1">{option.label}</span>
                {option.value !== '__def__' && (
                  <span className="text-xs opacity-60">{option.value.toUpperCase()}</span>
                )}
              </span>
            )}
          />
        </div>
        <Button variant="danger" size="sm" disabled={disabled} onClick={onDelete}>
          <Trash2 size={14} aria-hidden="true" /> {t('dub.delete_selected')}
        </Button>
      </div>
    </div>
  );
}
