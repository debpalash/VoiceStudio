import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-hot-toast';
import { apiPost } from '../api/client';
import { Dialog, Button } from '../ui';

/**
 * PocketTTS license acceptance modal (#1306).
 *
 * Same shape as SupertonicLicenseDialog: rendered by EngineCompatibilityMatrix
 * when the user enables PocketTTS while the backend reports
 * ``available=false`` with ``reason`` containing ``"license not accepted"``.
 *
 * Licenses surfaced:
 *   - Code: MIT (https://github.com/kyutai-labs/pocket-tts/blob/main/LICENSE)
 *   - Model weights: CC-BY-4.0 (https://huggingface.co/kyutai/pocket-tts)
 *
 * The weights are gated on HuggingFace (access agreement + contact info); the
 * dialog says so up front so the user knows the first download needs HF auth.
 */

const LICENSE_URLS = {
  code: 'https://github.com/kyutai-labs/pocket-tts/blob/main/LICENSE',
  model: 'https://huggingface.co/kyutai/pocket-tts',
  conditions: 'https://huggingface.co/kyutai/pocket-tts',
};

const LINK_CLS =
  'text-[0.83rem] text-[color:var(--accent,#8ab4f8)] no-underline hover:underline focus-visible:underline';
const SECTION_CLS = 'rounded-lg border border-transparent bg-white/[0.04] px-[0.9rem] py-3';
const SECTION_H_CLS =
  'm-0 mb-[0.3rem] text-[0.85rem] font-semibold uppercase tracking-[0.02em] opacity-85';
const SECTION_P_CLS = 'm-0 mb-2 text-[0.85rem] leading-[1.5] opacity-90';

export default function PocketTTSLicenseDialog({ open, onClose, onAccepted }) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);

  const accept = useCallback(async () => {
    setSubmitting(true);
    try {
      await apiPost('/api/settings/license', {
        engine_id: 'pockettts',
        accepted: true,
      });
      toast.success(t('license.pockettts_accepted_toast'));
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
      title={t('license.pockettts_title')}
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
      <p className="m-0 mb-4 text-[0.9rem] leading-[1.5] opacity-85">
        {t('license.pockettts_intro')}
      </p>

      <div className="mb-1 grid gap-[0.85rem]">
        <section className={SECTION_CLS}>
          <h3 className={SECTION_H_CLS}>{t('license.sdk_heading')}</h3>
          <p className={SECTION_P_CLS}>{t('license.pockettts_sdk_desc')}</p>
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
          <h3 className={SECTION_H_CLS}>{t('license.pockettts_model_heading')}</h3>
          <p className={SECTION_P_CLS}>{t('license.pockettts_model_desc')}</p>
          <a
            href={LICENSE_URLS.model}
            target="_blank"
            rel="noopener noreferrer"
            className={LINK_CLS}
          >
            {t('license.read_cc_by')}
          </a>
        </section>

        <section className={SECTION_CLS}>
          <h3 className={SECTION_H_CLS}>{t('license.gate_heading')}</h3>
          <p className={SECTION_P_CLS}>{t('license.gate_desc')}</p>
          <p className={SECTION_P_CLS}>{t('license.pockettts_conditions_desc')}</p>
          <a
            href={LICENSE_URLS.conditions}
            target="_blank"
            rel="noopener noreferrer"
            className={LINK_CLS}
          >
            {t('license.review_access_conditions')}
          </a>
        </section>
      </div>

      <p className="m-0 mt-3 text-[0.78rem] leading-[1.5] opacity-70">
        {t('license.pockettts_footer')}
      </p>
    </Dialog>
  );
}
