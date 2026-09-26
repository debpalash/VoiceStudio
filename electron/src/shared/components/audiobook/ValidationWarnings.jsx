import React from 'react';
import { AlertTriangle, X } from 'lucide-react';

/**
 * Pre-flight validation warnings (#1217) — a compact, dismissible, NON-blocking
 * list shown near the actions. It never blocks Create (the user may know
 * better). Each warning is a `{ type, ... }` from `validateScript`:
 *   unknown_voice · empty_chapter · unknown_tag.
 */
export default function ValidationWarnings({ t, warnings = [], onDismiss }) {
  if (!warnings.length) return null;

  const message = (w) => {
    if (w.type === 'unknown_voice') return t('audiobook.warn_unknown_voice', { name: w.name });
    if (w.type === 'empty_chapter')
      return t('audiobook.warn_empty_chapter', { title: w.title || t('audiobook.untitled') });
    if (w.type === 'unknown_tag') return t('audiobook.warn_unknown_tag', { tag: w.tag });
    return '';
  };

  return (
    <div
      className="flex flex-col gap-[6px] p-[10px] rounded-[8px] [border:1px_solid_rgba(250,189,47,0.35)] bg-[rgba(250,189,47,0.07)]"
      role="status"
    >
      <div className="flex items-center justify-between gap-[8px]">
        <div className="flex items-center gap-[6px] text-[0.72rem] font-semibold text-fg">
          <AlertTriangle size={13} /> {t('audiobook.warnings_title')}
        </div>
        <button
          type="button"
          className="border-0 bg-transparent text-[var(--color-fg-muted)] cursor-pointer p-1 rounded-[4px] hover:bg-white/[0.08] hover:text-[var(--color-fg)]"
          onClick={onDismiss}
          aria-label={t('audiobook.dismiss')}
          title={t('audiobook.dismiss')}
        >
          <X size={13} />
        </button>
      </div>
      <ul className="list-disc pl-[18px] m-0 flex flex-col gap-[3px]">
        {warnings.map((w, i) => (
          <li key={i} className="text-[0.7rem] leading-[1.45] text-fg-muted">
            {message(w)}
          </li>
        ))}
      </ul>
    </div>
  );
}
