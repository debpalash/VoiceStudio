import React from 'react';
import { Command } from 'lucide-react';
import { Trans, useTranslation } from 'react-i18next';
import { Dialog } from '../ui';

function Kbd({ children }) {
  return (
    <span className="inline-flex h-[22px] min-w-[28px] items-center justify-center gap-[2px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-hover-bg)] px-2 py-[2px] font-mono text-[0.7rem] font-medium text-[var(--chrome-fg)]">
      {children}
    </span>
  );
}

export default function KeyboardCheatsheet({ open, onClose }) {
  const { t } = useTranslation();

  const SECTIONS = [
    {
      title: t('keyboard.nav'),
      items: [
        ['?', t('keyboard.nav_cheatsheet')],
        [t('keyboard.nav_enginePickerKey'), t('engines.matrixTitle')],
        [t('keyboard.nav_workspacesKey'), t('keyboard.nav')],
        ['Esc', t('keyboard.nav_closeModal')],
        ['Cmd/Ctrl+S', t('keyboard.nav_save')],
      ],
    },
    {
      title: t('keyboard.segmentEditor'),
      items: [
        ['Cmd/Ctrl+D', t('keyboard.seg_split')],
        ['Cmd/Ctrl+M', t('keyboard.seg_merge')],
        ['Cmd/Ctrl+Shift+M', t('keyboard.seg_merge_prev')],
        ['Cmd/Ctrl+Z', t('keyboard.seg_undo')],
        ['Cmd/Ctrl+Shift+Z', t('keyboard.seg_redo')],
        ['Click row', t('keyboard.seg_click')],
        ['Shift+click row', t('keyboard.seg_shiftClick')],
      ],
    },
    {
      title: t('keyboard.trimmer'),
      items: [
        ['Space', t('keyboard.trim_playPause')],
        ['← / →', t('keyboard.trim_nudgeStart')],
        ['Ctrl+← / →', t('keyboard.trim_nudgeEnd')],
        ['Shift+arrow', t('keyboard.trim_fineNudge')],
        ['Alt+arrow', t('keyboard.trim_coarseNudge')],
        ['+ / −', t('keyboard.trim_zoomIn')],
        ['Home / End', t('keyboard.trim_fitAll')],
        ['Enter', t('keyboard.trim_confirm')],
      ],
    },
    {
      title: t('keyboard.dub'),
      items: [
        ['Cmd/Ctrl+Enter', t('keyboard.dub_generate')],
        ['Cmd/Ctrl+B', t('keyboard.dub_sidebar')],
      ],
    },
  ];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title={
        <span className="inline-flex items-center gap-[10px]">
          <Command size={16} color="var(--chrome-accent)" />
          {t('keyboard.title')}
        </span>
      }
      footer={
        <span className="flex-1 text-center text-[0.72rem] text-[var(--chrome-fg-dim)]">
          <Trans i18nKey="keyboard.footer" components={{ 1: <Kbd /> }}>
            {'Press <1>?</1> any time to open this.'}
          </Trans>
        </span>
      }
    >
      <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-[18px]">
        {SECTIONS.map((sec) => (
          <div key={sec.title}>
            <div className="mb-[10px] border-b border-transparent pb-[6px] font-mono text-[length:var(--chrome-label-size)] font-semibold uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg-muted)]">
              {sec.title}
            </div>
            <div className="flex flex-col gap-[6px]">
              {sec.items.map(([keys, desc]) => (
                <div key={keys} className="flex items-center justify-between gap-[10px]">
                  <span className="text-[0.8rem] text-[var(--chrome-fg-muted)]">{desc}</span>
                  <span className="flex shrink-0 gap-[3px]">
                    {keys.split(' / ').map((group, i, arr) => (
                      <React.Fragment key={group}>
                        <span className="flex gap-[2px]">
                          {group.split('+').map((k) => (
                            <Kbd key={k}>{k}</Kbd>
                          ))}
                        </span>
                        {i < arr.length - 1 && (
                          <span className="self-center text-[0.7rem] text-[var(--chrome-fg-dim)]">
                            {t('keyboard.or')}
                          </span>
                        )}
                      </React.Fragment>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Dialog>
  );
}
