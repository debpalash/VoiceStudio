import { useEffect, useState, type PointerEvent as ReactPointerEvent } from 'react';
import {
  BriefcaseBusinessIcon,
  CheckIcon,
  GitCompareArrowsIcon,
  KeyRoundIcon,
  ListChecksIcon,
  PackageCheckIcon,
  ShieldCheckIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { ExternalLink } from '@/components/external-link';
import { getBridge } from '@/components/bridge';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import type { VoiceStudioBridge } from '../../../../preload/index.d';
import './pro-page.css';

const planUrl = (plan: string, quantity: number) =>
  `https://voicestudio.sh/pro?plan=${plan}&quantity=${quantity}`;
const plans = [
  {
    id: 'yearly',
    unitPrice: 99,
    period: 'per_user_year',
    detail: 'yearly_detail',
  },
  {
    id: 'lifetime',
    unitPrice: 299,
    period: 'per_user_once',
    detail: 'lifetime_detail',
  },
  {
    id: 'enterprise',
    period: 'for_teams',
    detail: 'enterprise_detail',
  },
] as const;
const planFeatures = [
  'automation_title',
  'history_title',
  'delivery_title',
  'commercial_title',
] as const;
const lifetimeFeatures = [
  'lifetime_includes_pro',
  'lifetime_pay_once',
  'lifetime_no_subscription',
] as const;
const enterpriseFeatures = [
  'lifetime_includes_pro',
  'enterprise_deployment',
  'enterprise_policy',
  'enterprise_sso',
  'enterprise_private',
  'enterprise_support',
] as const;
const TERMS_URL = 'https://voicestudio.sh/terms';
const ENTERPRISE_URL = 'https://voicestudio.sh/commercial';
type Status = Awaited<ReturnType<VoiceStudioBridge['pro']['status']>>;
const benefits = [
  {
    icon: BriefcaseBusinessIcon,
    title: 'commercial_title',
    body: 'commercial_body',
    effect: 'seal',
  },
  { icon: ListChecksIcon, title: 'automation_title', body: 'automation_body', effect: 'lanes' },
  { icon: GitCompareArrowsIcon, title: 'history_title', body: 'history_body', effect: 'wave' },
  { icon: PackageCheckIcon, title: 'delivery_title', body: 'delivery_body', effect: 'pixels' },
  { icon: ShieldCheckIcon, title: 'preflight_title', body: 'preflight_body', effect: 'scan' },
] as const;

function trackFeaturePointer(event: ReactPointerEvent<HTMLElement>) {
  const card = event.currentTarget;
  const bounds = card.getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  card.style.setProperty('--feature-x', `${x}px`);
  card.style.setProperty('--feature-y', `${y}px`);
  card.style.setProperty(
    '--feature-angle',
    `${(Math.atan2(y - bounds.height / 2, x - bounds.width / 2) * 180) / Math.PI + 90}deg`,
  );
}

export function ProPage() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<Status | null>(null);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [licenseOpen, setLicenseOpen] = useState(false);
  const [quantities, setQuantities] = useState({ yearly: 1, lifetime: 1 });
  function changeQuantity(plan: 'yearly' | 'lifetime', change: number) {
    setQuantities((current) => ({
      ...current,
      [plan]: Math.min(99, Math.max(1, current[plan] + change)),
    }));
  }
  useEffect(() => {
    let mounted = true;
    void getBridge()
      ?.pro.status()
      .then((value) => {
        if (mounted) setStatus(value);
      })
      .catch(() => {
        // A renderer hot reload can briefly outlive an older main process that
        // does not expose Pro IPC yet. Keep the page usable until Electron restarts.
        if (mounted) setStatus({ active: false, configured: false });
      });
    return () => {
      mounted = false;
    };
  }, []);
  async function activate() {
    const bridge = getBridge();
    if (!bridge || busy) return;
    setBusy(true);
    try {
      setStatus(await bridge.pro.activate(key));
      setKey('');
    } catch {
      setStatus({ active: false, configured: true, error: 'offline' });
    } finally {
      setBusy(false);
    }
  }
  async function deactivate() {
    const bridge = getBridge();
    if (!bridge || busy) return;
    setBusy(true);
    try {
      setStatus(await bridge.pro.deactivate());
    } catch {
      setStatus({ active: true, configured: true, error: 'offline' });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="pro-page">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('proPage.title')}</h1>
      </WorkspaceHeader>
      <div className="pro-page-scroll">
        <main className="pro-page-content">
          <section className="pro-pricing" id="pro-compare" aria-labelledby="pro-pricing-title">
            <div className="pro-section-head">
              <h2 id="pro-pricing-title">{t('proPage.hero_title')}</h2>
              <p>{t('proPage.hero_body')}</p>
            </div>
            <div className="pro-plans">
              {plans.map((plan) => {
                const quantity = plan.id === 'enterprise' ? 1 : quantities[plan.id];
                return (
                  <article
                    key={plan.id}
                    className={`pro-plan${plan.id === 'yearly' ? ' pro-plan--featured' : ''}${plan.id === 'enterprise' ? ' pro-plan--enterprise' : ''}`}
                  >
                    <div className="pro-plan-top">
                      <h3>{t(`proPage.plan_${plan.id}`)}</h3>
                      {plan.id === 'yearly' && (
                        <span className="pro-plan-badge">{t('proPage.best_value')}</span>
                      )}
                    </div>
                    <div className="pro-plan-price">
                      <strong>
                        {plan.id === 'enterprise'
                          ? t('proPage.enterprise_price')
                          : t('proPage.usd_price', { amount: plan.unitPrice })}
                      </strong>
                      <span>{t(`proPage.${plan.period}`)}</span>
                    </div>
                    <p>{t(`proPage.${plan.detail}`)}</p>
                    {plan.id !== 'enterprise' && (
                      <div className="pro-seat-picker">
                        <div>
                          <span>{t('proPage.users')}</span>
                          <strong aria-live="polite">{quantity}</strong>
                        </div>
                        <div className="pro-seat-controls">
                          <button
                            type="button"
                            onClick={() => changeQuantity(plan.id, -1)}
                            disabled={quantity === 1}
                            aria-label={t('proPage.decrease_users')}
                          >
                            −
                          </button>
                          <button
                            type="button"
                            onClick={() => changeQuantity(plan.id, 1)}
                            disabled={quantity === 99}
                            aria-label={t('proPage.increase_users')}
                          >
                            +
                          </button>
                        </div>
                        <div className="pro-seat-total">
                          <span>{t('proPage.total')}</span>
                          <strong>
                            {t('proPage.usd_price', { amount: plan.unitPrice * quantity })}
                          </strong>
                        </div>
                      </div>
                    )}
                    <ul className="pro-plan-features">
                      {(plan.id === 'enterprise'
                        ? enterpriseFeatures
                        : plan.id === 'lifetime'
                          ? lifetimeFeatures
                          : planFeatures
                      ).map((feature) => (
                        <li key={feature}>
                          <CheckIcon aria-hidden="true" />
                          <span>{t(`proPage.${feature}`)}</span>
                        </li>
                      ))}
                    </ul>
                    <div className="pro-plan-end pro-plan-buy">
                      {plan.id === 'enterprise' ? (
                        <ExternalLink href={ENTERPRISE_URL} showIcon={false}>
                          {t('proPage.contact_enterprise')}
                        </ExternalLink>
                      ) : (
                        <ExternalLink href={planUrl(plan.id, quantity)} showIcon={false}>
                          {t(`proPage.choose_${plan.id}`)}
                        </ExternalLink>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
            <p className="pro-billing-note">{t('proPage.billing_note')}</p>
            <button className="pro-have-license" type="button" onClick={() => setLicenseOpen(true)}>
              {t('proPage.activate_title')}
            </button>
          </section>
          <section className="pro-features" aria-labelledby="pro-features-title">
            <div className="pro-section-head">
              <h2 id="pro-features-title">{t('proPage.features_heading')}</h2>
              <p>{t('proPage.features_subtitle')}</p>
            </div>
            <div className="pro-benefits">
              {benefits.map(({ icon: Icon, title, body, effect }) => (
                <article
                  key={title}
                  className={`pro-feature-card pro-feature-card--${effect}`}
                  onPointerMove={trackFeaturePointer}
                >
                  <span className="pro-feature-effect" aria-hidden="true" />
                  <div className="pro-feature-copy">
                    <Icon aria-hidden="true" />
                    <h3>{t(`proPage.${title}`)}</h3>
                    <p>{t(`proPage.${body}`)}</p>
                  </div>
                </article>
              ))}
            </div>
            <p className="pro-free-promise">{t('proPage.free_promise')}</p>
          </section>
          <Dialog open={licenseOpen} onOpenChange={setLicenseOpen}>
            <DialogContent showCloseButton={false} className="pro-license-modal">
              <button
                className="pro-license-close"
                type="button"
                onClick={() => setLicenseOpen(false)}
                aria-label={t('common.close')}
              >
                ×
              </button>
              <DialogTitle id="pro-activation-title">{t('proPage.activate_title')}</DialogTitle>
              <div className="pro-activation-card">
                <KeyRoundIcon aria-hidden="true" />
                <h3>{status?.active ? t('proPage.active_title') : t('proPage.enter_key')}</h3>
                {status?.active ? (
                  <>
                    <p role="status">{t('proPage.active_body')}</p>
                    <button type="button" disabled={busy} onClick={() => void deactivate()}>
                      {t('proPage.deactivate')}
                    </button>
                  </>
                ) : (
                  <>
                    <label htmlFor="pro-license-key">{t('proPage.key_label')}</label>
                    <input
                      id="pro-license-key"
                      autoFocus
                      autoComplete="off"
                      value={key}
                      onChange={(event) => setKey(event.target.value)}
                      placeholder={t('proPage.key_placeholder')}
                      disabled={busy || status?.configured === false}
                    />
                    <button
                      type="button"
                      disabled={busy || !key.trim() || status?.configured === false}
                      onClick={() => void activate()}
                    >
                      {busy ? t('proPage.activating') : t('proPage.activate')}
                    </button>
                    {status?.configured === false && (
                      <p role="status">{t('proPage.not_configured')}</p>
                    )}
                  </>
                )}
                {status?.error && <p role="alert">{t(`proPage.error_${status.error}`)}</p>}
              </div>
              <div className="pro-terms-link">
                <ExternalLink href={TERMS_URL}>{t('proPage.read_terms')}</ExternalLink>
              </div>
            </DialogContent>
          </Dialog>
        </main>
      </div>
    </div>
  );
}
