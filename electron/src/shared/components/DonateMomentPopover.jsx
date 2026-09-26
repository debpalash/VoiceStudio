import React from 'react';
import { useTranslation } from 'react-i18next';
import { openExternal } from '../api/external';
import { KOFI_URL, PAYPAL_URL } from '../utils/donateLinks';
import { DONATE_LINE_COUNT } from '../utils/donationMoments';

/**
 * DonateMomentPopover — the friendly "Clippy-like" speech bubble that
 * LogsFooter anchors above the donate heart when a donation moment fires
 * (see utils/donationMoments.js for the strict eligibility gates).
 *
 * Deliberately NO backdrop and NO focus trap: it's an aside, not a modal —
 * the user can keep working and it auto-dismisses on its own. Bounce-in
 * animation collapses to a plain fade under `prefers-reduced-motion`.
 * Chrome palette throughout so it reads as part of the footer.
 */

/** How long the popover lingers before quietly dismissing itself. */
export const DONATE_POPOVER_AUTO_DISMISS_MS = 15_000;

// Small pill CTA shared by the Ko-fi / PayPal buttons.
const CTA_BTN =
  'inline-flex items-center gap-[5px] px-[10px] h-[24px] rounded-[5px] cursor-pointer ' +
  'border border-solid border-transparent text-[11px] font-semibold [font-family:inherit] ' +
  '[background:var(--chrome-accent-bg)] [color:var(--chrome-fg)] transition-colors ' +
  'hover:[border-color:var(--chrome-accent-border)] hover:[color:var(--chrome-accent)]';

const QUIET_BTN =
  'bg-transparent border-0 cursor-pointer p-0 text-[11px] [font-family:inherit] ' +
  '[color:var(--chrome-fg-muted)] hover:[color:var(--chrome-fg)] transition-colors';

export default function DonateMomentPopover({ line = 0, onLater, onOptOut }) {
  const { t } = useTranslation();
  // Rotate through the friendly lines; clamp so a stale/oversized index from
  // the event detail can never resolve to a missing i18n key.
  const lineKey = `footer_donate.line_${(Math.abs(line | 0) % DONATE_LINE_COUNT) + 1}`;

  return (
    <div
      role="status"
      aria-label={t('footer_donate.aria')}
      data-testid="donate-moment-popover"
      className={
        'absolute bottom-[calc(100%+10px)] right-0 z-[60] w-[272px] p-[12px] rounded-[10px] ' +
        'flex flex-col gap-[10px] select-none [background:var(--chrome-bg,#1d2021)] ' +
        'border border-solid [border-color:var(--chrome-border,rgba(255,255,255,0.08))] ' +
        'shadow-[0_8px_24px_rgba(0,0,0,0.45)] ' +
        '[animation:donate-pop-in_0.45s_cubic-bezier(0.34,1.56,0.64,1)_both] ' +
        'motion-reduce:[animation:donate-fade-in_0.2s_ease-out_both]'
      }
    >
      {/* Speech-bubble tail, pointing down at the heart. */}
      <span
        aria-hidden="true"
        className={
          'absolute -bottom-[6px] right-[12px] w-[10px] h-[10px] rotate-45 ' +
          '[background:var(--chrome-bg,#1d2021)] ' +
          '[border-right:1px_solid_var(--chrome-border,rgba(255,255,255,0.08))] ' +
          '[border-bottom:1px_solid_var(--chrome-border,rgba(255,255,255,0.08))]'
        }
      />
      <p className="m-0 text-[11.5px] leading-[1.55] [color:var(--chrome-fg)]">{t(lineKey)}</p>
      <div className="flex items-center gap-[6px]">
        <button
          type="button"
          className={CTA_BTN}
          aria-label={t('footer_donate.kofi_aria')}
          onClick={() => {
            openExternal(KOFI_URL);
            onLater();
          }}
        >
          <span aria-hidden="true">☕</span> {t('footer_donate.kofi')}
        </button>
        <button
          type="button"
          className={CTA_BTN}
          aria-label={t('footer_donate.paypal_aria')}
          onClick={() => {
            openExternal(PAYPAL_URL);
            onLater();
          }}
        >
          <span aria-hidden="true">💳</span> {t('footer_donate.paypal')}
        </button>
        <span className="flex-1" />
        <button type="button" className={QUIET_BTN} onClick={onLater}>
          {t('footer_donate.later')}
        </button>
      </div>
      <button
        type="button"
        onClick={onOptOut}
        className={
          'self-end bg-transparent border-0 cursor-pointer p-0 text-[10px] [font-family:inherit] ' +
          '[color:var(--chrome-fg-dim,var(--chrome-fg-muted))] hover:underline ' +
          'hover:[color:var(--chrome-fg-muted)] transition-colors'
        }
      >
        {t('footer_donate.dont_ask')}
      </button>
    </div>
  );
}
