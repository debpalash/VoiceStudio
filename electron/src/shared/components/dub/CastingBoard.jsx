import { useState } from 'react';
import { LayoutGrid, Users, UserRound, Fingerprint } from 'lucide-react';
import SearchableSelect from '../SearchableSelect';
import { PRESET_ICONS, FALLBACK_VOICE_ICON, stripVoiceEmoji } from '../../utils/voiceIcons';
import toast from 'react-hot-toast';
import { PRESETS } from '../../utils/constants';
import { autoProfileId, assignSpeakerProfile, castParts, castSpeakers } from '../../utils/segments';

// The Default voice IS the empty profile_id, but a dataTransfer payload can't
// carry '' distinguishably from "no data" — so it travels as this sentinel.
const DEFAULT_VOICE = '__default__';

/**
 * CAST — per-speaker voice assignment: the compact <select> strip
 * (pre-existing, unchanged markup) plus an expandable casting board —
 * draggable voice chips dropped onto speaker rows, with a keyboard path
 * (focus a speaker, pick from a listbox). Both views write through
 * `assignSpeakerProfile`, so a drop is byte-identical to picking the same
 * option in the dropdown — no new persistence, the job save path already carries these fields.
 */
export default function CastingBoard({ t, dubSegments, setDubSegments, speakerClones, profiles }) {
  const [boardOpen, setBoardOpen] = useState(false);
  const [dropTarget, setDropTarget] = useState(null); // speaker id under a drag
  const speakers = castSpeakers(dubSegments);
  if (!speakers.length) return null;

  const currentVoice = (spk) =>
    dubSegments.find((s) => s.speaker_id === spk)?.profile_id ||
    dubSegments.flatMap(castParts).find((part) => part.speaker_id === spk)?.profile_id ||
    '';

  const voiceLabel = (val, spk) => {
    if (!val) return t('dub.default');
    const clone = speakerClones[spk];
    if (clone && val === autoProfileId(spk)) {
      return stripVoiceEmoji(t('dub.from_video', { duration: clone.duration.toFixed(1) }));
    }
    if (val === autoProfileId(spk)) return `${t('dub.auto')} · ${spk}`;
    if (val.startsWith('preset:')) {
      return stripVoiceEmoji(t(`clone.preset_${val.replace('preset:', '')}`));
    }
    return stripVoiceEmoji(profiles.find((p) => p.id === val)?.name || val);
  };

  const paletteChips = [
    { value: '', label: t('dub.default'), Icon: UserRound },
    ...profiles.map((p) => ({
      value: p.id,
      label: stripVoiceEmoji(p.name),
      group: 'clones',
      groupLabel: t('dub.clone_profiles'),
      Icon: Fingerprint,
    })),
    ...PRESETS.map((p) => ({
      value: `preset:${p.id}`,
      label: stripVoiceEmoji(t(`clone.preset_${p.id}`)),
      group: 'presets',
      groupLabel: t('dub.design_presets'),
      Icon: PRESET_ICONS[p.id] || FALLBACK_VOICE_ICON,
    })),
  ];
  // One option source keeps the compact selector, board, and drag validation in sync.
  const voiceOptions = (spk) => [
    ...(speakerClones[spk] || currentVoice(spk) === autoProfileId(spk)
      ? [{ value: autoProfileId(spk), label: voiceLabel(autoProfileId(spk), spk), Icon: UserRound }]
      : []),
    ...paletteChips,
  ];
  const renderVoice = (option) => {
    const Icon = option.Icon;
    return (
      <span className="inline-flex min-w-0 items-center gap-2">
        <Icon size={16} aria-hidden="true" className="shrink-0 opacity-75" />
        <span>{option.label}</span>
      </span>
    );
  };

  const assign = (spk, val) => {
    setDubSegments(assignSpeakerProfile(dubSegments, spk, val));
    toast.success(t('dub.casting_assigned', { voice: voiceLabel(val, spk), speaker: spk }));
  };

  const onDrop = (e, spk) => {
    e.preventDefault();
    setDropTarget(null);
    const raw = e.dataTransfer?.getData('text/plain');
    if (!raw) return;
    const value = raw === DEFAULT_VOICE ? '' : raw;
    if (!voiceOptions(spk).some((option) => option.value === value)) return;
    assign(spk, value);
  };

  return (
    <div className="dub-casting-controls my-3 p-4 bg-[var(--chrome-hover-bg)] rounded-lg border-0">
      <div className="flex gap-[var(--space-2)] items-center flex-wrap">
        <span
          className="font-[family-name:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] text-[var(--chrome-fg-muted)] tracking-[var(--chrome-label-track)] uppercase font-semibold"
          title={t('dub.cast_title')}
        >
          <Users size={16} aria-hidden="true" className="inline-block mr-2 align-text-bottom" />
          {t('dub.cast')}
        </span>
        {speakers.map((spk) => {
          return (
            <div key={spk} className="dub-cast__pair">
              <span className="font-[family-name:var(--chrome-font-mono)] text-[0.62rem] text-[var(--chrome-fg)]">
                <UserRound
                  size={14}
                  aria-hidden="true"
                  className="inline-block mr-1 align-text-bottom"
                />
                {spk}
              </span>
              <SearchableSelect
                ariaLabel={spk}
                menuPortal
                renderGroupHeaders
                buttonClassName="dub-cast__select min-h-10 rounded-lg border-0 bg-[var(--chrome-hover-bg)] px-3 py-2 text-sm text-[var(--chrome-fg)] hover:bg-[var(--chrome-accent-bg)] focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)]"
                value={currentVoice(spk)}
                onChange={(value) => setDubSegments(assignSpeakerProfile(dubSegments, spk, value))}
                options={voiceOptions(spk)}
                renderOption={renderVoice}
              />
            </div>
          );
        })}
        <button
          type="button"
          className={`dub-cast__board-toggle ${boardOpen ? 'is-open' : ''}`}
          aria-expanded={boardOpen}
          onClick={() => setBoardOpen((o) => !o)}
          title={t('dub.casting_board_hint')}
        >
          <LayoutGrid size={15} aria-hidden="true" /> {t('dub.casting_board')}
        </button>
      </div>

      {boardOpen && (
        <div className="dub-cast__board" data-testid="casting-board">
          <div className="dub-cast__palette" role="list" aria-label={t('dub.casting_voices')}>
            {paletteChips.map((chip) => (
              <span
                key={chip.value || DEFAULT_VOICE}
                role="listitem"
                className="dub-cast__chip"
                draggable
                data-profile={chip.value}
                title={chip.groupLabel ? `${chip.groupLabel} · ${chip.label}` : chip.label}
                onDragStart={(e) => {
                  e.dataTransfer.setData('text/plain', chip.value || DEFAULT_VOICE);
                  e.dataTransfer.effectAllowed = 'copy';
                }}
              >
                <chip.Icon size={18} aria-hidden="true" />
                <span className="dub-cast__chip-text">
                  <span>{chip.label}</span>
                  {chip.groupLabel && <small>{chip.groupLabel}</small>}
                </span>
              </span>
            ))}
          </div>
          <p className="dub-cast__hint">{t('dub.casting_drag_hint')}</p>
          <div className="dub-cast__rows">
            {speakers.map((spk) => {
              const clone = speakerClones[spk];
              const options = voiceOptions(spk);
              const current = currentVoice(spk);
              return (
                <div
                  key={spk}
                  className={`dub-cast__row ${dropTarget === spk ? 'is-drop' : ''}`}
                  data-speaker={spk}
                  onDragOver={(e) => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'copy';
                  }}
                  onDragEnter={() => setDropTarget(spk)}
                  onDragLeave={(e) => {
                    if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
                      setDropTarget((cur) => (cur === spk ? null : cur));
                    }
                  }}
                  onDrop={(e) => onDrop(e, spk)}
                >
                  <span className="dub-cast__row-speaker">
                    <UserRound size={16} aria-hidden="true" />
                    {spk}
                  </span>
                  <SearchableSelect
                    ariaLabel={t('dub.casting_assign_to', { speaker: spk })}
                    menuPortal
                    renderGroupHeaders
                    buttonClassName="dub-cast__row-select"
                    value={current}
                    options={options}
                    onChange={(value) => assign(spk, value)}
                    renderOption={renderVoice}
                  />
                  {clone && (
                    <span
                      className="dub-cast__auto-chip"
                      title={t('dub.from_video', { duration: clone.duration.toFixed(1) })}
                    >
                      <UserRound size={13} aria-hidden="true" />
                      {stripVoiceEmoji(
                        t('dub.from_video', { duration: clone.duration.toFixed(1) }),
                      )}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
