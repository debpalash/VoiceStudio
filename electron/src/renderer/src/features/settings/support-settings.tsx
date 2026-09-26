import { runRendererTask } from '@/lib/global-error-recovery';
import {
  ArrowUpRightIcon,
  CheckIcon,
  CoffeeIcon,
  CreditCardIcon,
  GemIcon,
  Globe2Icon,
  LightbulbIcon,
  MailIcon,
  MessagesSquareIcon,
  RadioIcon,
  ShieldCheckIcon,
  StarIcon,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { brandIcon } from '@/lib/brand';
import { DonationGoal } from './donation-goal';
import { ReportBug } from '@/components/report-bug';
import { ExternalLink } from '@/components/external-link';
import { KOFI_URL, PAYPAL_URL } from '@shared/utils/donateLinks';
import {
  SPONSORS,
  SPONSOR_TIERS,
  SPONSOR_CONTACT,
} from '@shared/config/sponsors';
import {
  ISSUES_URL,
  DISCORD_URL,
  SECURITY_URL,
  EMAIL,
  WEBSITE_URL,
  X_URL,
} from '@shared/utils/contactLinks';
import './support-settings.css';

export function SupportSettings() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [amount, setAmount] = useState<number | 'custom' | null>(null);
  const sponsorGroups = [...SPONSOR_TIERS, '']
    .map((tier) => ({
      tier,
      sponsors: SPONSORS.filter((sponsor) =>
        tier ? sponsor.tier === tier : !SPONSOR_TIERS.includes(sponsor.tier),
      ),
    }))
    .filter((group) => group.sponsors.length);
  const channels = [
    ['contact.feature_cta', ISSUES_URL, LightbulbIcon],
    ['contact.community_cta', DISCORD_URL, MessagesSquareIcon],
    ['contact.follow_cta', X_URL, RadioIcon],
    ['contact.security_cta', SECURITY_URL, ShieldCheckIcon],
    ['contact.email', 'mailto:' + EMAIL, MailIcon],
    ['contact.website', WEBSITE_URL, Globe2Icon],
  ] as const;

  return (
    <div className="support-studio">
      <header className="support-intro">
        <div className="support-emblem" aria-hidden="true">
          <img src={brandIcon} alt="" width={100} height={100} />
        </div>
        <h1>{t('donate.hero_title')}</h1>
        <p>{t('donate.footer')}</p>
      </header>

      <section className="support-giving" aria-label={t('donate.goal.title')}>
        <div className="support-progress">
          <DonationGoal />
        </div>
        <div className="support-checkout">
          <h2>{t('donate.suggested_title')}</h2>
          <div className="support-amounts" role="group" aria-label={t('donate.suggested_title')}>
            {[10, 20, 50, 'custom' as const].map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={amount === value}
                onClick={() => setAmount(amount === value ? null : value)}
              >
                <CheckIcon aria-hidden="true" className="support-selected" />
                <span>{value === 'custom' ? t('donate.custom') : '$' + value}</span>
              </button>
            ))}
          </div>
          <div className="support-payments" role="group" aria-label={t('donate.choose_method')}>
            <ExternalLink href={KOFI_URL}>
              <CoffeeIcon aria-hidden="true" />
              Ko-fi
            </ExternalLink>
            <ExternalLink
              href={typeof amount === 'number' ? PAYPAL_URL + '/' + amount : PAYPAL_URL}
            >
              <CreditCardIcon aria-hidden="true" />
              PayPal
            </ExternalLink>
          </div>
          <p className="support-payment-note">
            {typeof amount === 'number'
              ? t('donate.choose_method_amount', { amount })
              : t('donate.choose_method')}
          </p>
        </div>
      </section>

      <div className="support-community" role="group" aria-label={t('support.other_ways')}>
        <span>{t('support.other_ways')}</span>
        <ExternalLink href="https://github.com/debpalash/VoiceStudio">
          <StarIcon aria-hidden="true" />
          {t('support.star_github')}
        </ExternalLink>
        <ExternalLink href={DISCORD_URL}>
          <MessagesSquareIcon aria-hidden="true" />
          {t('support.join_discord')}
        </ExternalLink>
      </div>

      <div className="support-opportunities">
        <section
          className="support-tile support-sponsors"
          aria-labelledby="support-sponsors-heading"
        >
          <GemIcon className="support-tile-icon" aria-hidden="true" />
          <h2 id="support-sponsors-heading">{t('support.sponsors_title')}</h2>
          <p>{t(SPONSORS.length ? 'support.sponsors_lead' : 'support.sponsors_empty_title')}</p>
          {SPONSORS.length === 0 && (
            <div className="support-logo-slot" aria-hidden="true">
              <GemIcon />
              <span>{t('support.sponsors_empty_desc')}</span>
              <span className="support-logo-plus">+</span>
            </div>
          )}
          {sponsorGroups.map(({ tier, sponsors }) => (
            <div key={tier} className="support-sponsor-group">
              {tier && <h3>{t('support.sponsors_tier_' + tier)}</h3>}
              <div>
                {sponsors.map((sponsor) => (
                  <ExternalLink key={sponsor.url} href={sponsor.url}>
                    <img src={sponsor.logoUrl} alt="" loading="lazy" />
                    {sponsor.name}
                  </ExternalLink>
                ))}
              </div>
            </div>
          ))}
          <div className="support-tile-actions">
            <ExternalLink href={SPONSOR_CONTACT.githubIssue}>
              {t('support.sponsors_become')}
            </ExternalLink>
            <ExternalLink href={SPONSOR_CONTACT.docsUrl}>
              {t('support.sponsors_learn_more')}
            </ExternalLink>
          </div>
        </section>
        <section className="support-tile support-license" aria-labelledby="support-pro-heading">
          <span className="mb-4 w-fit rounded-md bg-primary/10 px-2 py-1 text-xs font-semibold tracking-wider text-primary">
            {t('supportPlans.pro')}
          </span>
          <h2 id="support-pro-heading">{t('proPage.title')}</h2>
          <p>{t('proPage.hero_body')}</p>
          <p>{t('proPage.billing_note')}</p>
          <button
            type="button"
            onClick={() => runRendererTask('Open Pro', () => navigate({ to: '/pro' }))}
            className="mt-4 flex min-h-11 items-center gap-2 text-sm font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-primary"
          >
            {t('supportPlans.get_pro')}
            <ArrowUpRightIcon aria-hidden="true" className="size-3" />
          </button>
        </section>
      </div>

      <section className="support-contact" aria-labelledby="support-contact-heading">
        <div className="support-contact-heading">
          <h2 id="support-contact-heading">{t('contact.channels_label')}</h2>
          <ReportBug />
        </div>
        <div className="support-channel-grid">
          {channels.map(([label, href, Icon]) => (
            <ExternalLink key={href} href={href}>
              <Icon aria-hidden="true" />
              <span>{t(label)}</span>
            </ExternalLink>
          ))}
        </div>
      </section>
    </div>
  );
}
