import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-hot-toast';
import { apiPost } from '../api/client';
import { Dialog, Button } from '../ui';

/**
 * Supertonic-3 license acceptance modal (Phase 3 Plan 03-01 / TTS-05).
 *
 * Rendered by ``EngineCompatibilityMatrix`` when the user toggles the
 * Supertonic-3 row's Enable / Use button while the backend reports
 * ``available=false`` with ``reason`` containing
 * ``"license not accepted"``. On Accept the dialog POSTs to
 * ``/api/settings/license`` (loopback-gated, allow-list of one engine
 * id ‑‑ ``"supertonic3"``); on success it calls ``onAccepted()`` so the
 * matrix re-fetches engine status.
 *
 * Licenses surfaced:
 *   • SDK code ‑‑ MIT (https://github.com/supertone-inc/supertonic/blob/main/LICENSE)
 *   • Model weights ‑‑ OpenRAIL-M (https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE)
 *
 * Both links open in the user's default browser. The dialog does NOT
 * embed the full license text ‑‑ that's the user's call to make on
 * github.com / huggingface.co. We only need their explicit click to
 * Accept.
 *
 * Built on the shadcn-backed `src/ui` Dialog primitive (Radix dialog:
 * focus-trap, scroll-lock, ESC-to-close, ARIA). While a POST is in flight
 * the dialog is non-dismissable so the user can't close mid-save.
 *
 * Props:
 *   - open: boolean         ‑‑ controls visibility
 *   - onClose: () => void   ‑‑ user clicked Cancel / dismissed
 *   - onAccepted: () => void‑‑ user clicked Accept and POST succeeded
 */

const LICENSE_URLS = {
  code: 'https://github.com/supertone-inc/supertonic/blob/main/LICENSE',
  model: 'https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE',
};

const LINK_CLS =
  'text-[0.83rem] text-[color:var(--accent,#8ab4f8)] no-underline hover:underline focus-visible:underline';
const SECTION_CLS = 'rounded-lg border border-transparent bg-white/[0.04] px-[0.9rem] py-3';
const SECTION_H_CLS =
  'm-0 mb-[0.3rem] text-[0.85rem] font-semibold uppercase tracking-[0.02em] opacity-85';
const SECTION_P_CLS = 'm-0 mb-2 text-[0.85rem] leading-[1.5] opacity-90';

export default function SupertonicLicenseDialog({ open, onClose, onAccepted }) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);

  const accept = useCallback(async () => {
    setSubmitting(true);
    try {
      await apiPost('/api/settings/license', {
        engine_id: 'supertonic3',
        accepted: true,
      });
      toast.success(t('license.accepted_toast'));
      onAccepted?.();
      onClose?.();
    } catch (e) {
      const msg = e?.message || String(e);
      toast.error(t('license.accept_error', { message: msg }));
    } finally {
      setSubmitting(false);
    }
  }, [onAccepted, onClose, t]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="md"
      dismissable={!submitting}
      title={t('license.title')}
      footer={
        <>
          <Button variant="subtle" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            onClick={accept}
            disabled={submitting}
            loading={submitting}
            autoFocus
          >
            {submitting ? t('license.saving') : t('license.accept')}
          </Button>
        </>
      }
    >
      <p className="m-0 mb-4 text-[0.9rem] leading-[1.5] opacity-85">{t('license.intro')}</p>

      <div className="mb-1 grid gap-[0.85rem]">
        <section className={SECTION_CLS}>
          <h3 className={SECTION_H_CLS}>{t('license.sdk_heading')}</h3>
          <p className={SECTION_P_CLS}>{t('license.sdk_desc')}</p>
          <a
            href={LICENSE_URLS.code}
            target="_blank"
            rel="noopener noreferrer"
            className={LINK_CLS}
          >
            {t('license.read_mit')}
          </a>
        </section>

        <section className={SECTION_CLS}>
          <h3 className={SECTION_H_CLS}>{t('license.model_heading')}</h3>
          <p className={SECTION_P_CLS}>{t('license.model_desc')}</p>
          <a
            href={LICENSE_URLS.model}
            target="_blank"
            rel="noopener noreferrer"
            className={LINK_CLS}
          >
            {t('license.read_openrail')}
          </a>
        </section>
      </div>

      <p className="m-0 mt-3 text-[0.78rem] leading-[1.5] opacity-70">{t('license.footer')}</p>
    </Dialog>
  );
}
