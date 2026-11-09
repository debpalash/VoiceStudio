import { useState, type PointerEvent as ReactPointerEvent } from 'react';
import {
  BriefcaseBusinessIcon,
  CheckIcon,
  GitCompareArrowsIcon,
  ListChecksIcon,
  PackageCheckIcon,
  ShieldCheckIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { ExternalLink } from '@/components/external-link';
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
    unitPrice: 199,
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
const ENTERPRISE_URL = 'https://voicestudio.sh/commercial';
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
  const [quantities, setQuantities] = useState({ yearly: 1, lifetime: 1 });
  function changeQuantity(plan: 'yearly' | 'lifetime', change: number) {
    setQuantities((current) => ({
      ...current,
      [plan]: Math.min(99, Math.max(1, current[plan] + change)),
    }));
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
        </main>
      </div>
    </div>
  );
}
