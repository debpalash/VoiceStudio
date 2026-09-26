import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Coffee,
  CreditCard,
  Heart,
  ExternalLink,
  ArrowLeft,
  Building2,
  Shield,
  Zap,
  Headphones,
  Mail,
  Star,
  MessageCircle,
  Gem,
} from 'lucide-react';
import { Button, Badge, Tabs } from '../ui';
import { Card } from '@/components/ui/card';
import { openExternal } from '../api/external';
import GoalBar from '../components/donate/GoalBar';
import { loadDonationProgress, BUNDLED_PROGRESS } from '../api/donation';

// Ko-fi / PayPal destinations are shared with the footer's donation-moment
// popover — single source of truth in utils/donateLinks.js.
import { KOFI_URL, PAYPAL_URL } from '../utils/donateLinks';
// Sponsor roster + "become a sponsor" links — single source of truth in
// config/sponsors.js (kept in lockstep with SPONSORS.md).
import { SPONSORS, SPONSOR_TIERS, SPONSOR_CONTACT } from '../config/sponsors';
import { ContactSections } from './ContactPage';

const VIEWS = ['support', 'license', 'contact'];
const viewFromRoute = (view) => (VIEWS.includes(view) ? view : 'support');
// Suggested amounts — ladder starts at $10; middle ($20) is "most common".
const SUGGESTED_AMOUNTS = [
  { value: 10, label: '$10' },
  { value: 20, label: '$20', common: true },
  { value: 50, label: '$50' },
];

const METHODS = [
  { id: 'kofi', label: 'Ko-fi', url: KOFI_URL, icon: <Coffee size={21} aria-hidden="true" /> },
  {
    id: 'paypal',
    label: 'PayPal',
    url: PAYPAL_URL,
    icon: <CreditCard size={21} aria-hidden="true" />,
  },
];

// Donate/support accent tracks the themed brand token (per-[data-theme]) so the
// panel recolors with the app theme instead of the old fixed pink.
const DONATE_HUE = 'var(--color-brand)';

// PayPal.me carries the chosen amount straight into the checkout; Ko-fi opens
// its tip page (no reliable preset-amount URL). A non-numeric/"custom" amount
// falls back to the bare link.
function methodUrl(method, amount) {
  if (method.id === 'paypal' && typeof amount === 'number') return `${PAYPAL_URL}/${amount}`;
  return method.url;
}

// Compact payment button. `hue` tints the icon bubble + hover.
function LinkCard({ icon, label, hue, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ '--card-hue': hue }}
      className="group flex min-h-14 w-full items-center gap-3 overflow-hidden rounded-xl border border-border bg-transparent px-3 py-2 text-left motion-safe:transition-all motion-safe:duration-200 motion-safe:hover:-translate-y-0.5 motion-safe:active:translate-y-0 focus-visible:outline-2 focus-visible:outline-[var(--color-brand)] hover:border-transparent hover:bg-[color-mix(in_srgb,var(--card-hue)_6%,transparent)]"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-transparent bg-[color-mix(in_srgb,var(--card-hue)_10%,transparent)] text-base">
        {icon}
      </span>
      <span className="min-w-0 flex-1 font-mono text-xs font-semibold uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg)]">
        {label}
      </span>
      <span className="flex size-6 shrink-0 items-center justify-center text-[var(--chrome-fg-muted)]">
        <ExternalLink size={13} />
      </span>
    </button>
  );
}

// Section label: a mono uppercase caption with a trailing hairline.
function SectionTitle({ children }) {
  return (
    <div className="mb-2 flex items-center gap-3">
      <span className="whitespace-nowrap font-mono text-[var(--chrome-label-size)] font-semibold uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg-muted)]">
        {children}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

/* ── Sponsors ─────────────────────────────────────────────────────────────
   Logo grid grouped by tier when SPONSORS has entries; a tasteful "be the
   first" outlined slot when it's empty. Logos link out via the app's
   openExternal (Tauri-safe) while keeping a real href for accessibility. */

// A single clickable sponsor logo. Real <a href> (right-click / a11y) but the
// click is intercepted so it opens in the system browser, not the webview.
function SponsorLogo({ sponsor }) {
  const { t } = useTranslation();
  return (
    <a
      href={sponsor.url}
      target="_blank"
      rel="noreferrer"
      onClick={(e) => {
        e.preventDefault();
        openExternal(sponsor.url);
      }}
      title={sponsor.name}
      aria-label={t('support.sponsors_logo_aria', {
        defaultValue: 'Visit {{name}}, a VoiceStudio sponsor',
        name: sponsor.name,
      })}
      className="flex min-h-11 items-center justify-center rounded-md border border-border bg-transparent px-3 py-2 transition-colors hover:border-transparent hover:bg-[var(--chrome-hover-bg)]"
    >
      <img
        src={sponsor.logoUrl}
        alt={sponsor.name}
        loading="lazy"
        className="max-h-8 w-auto max-w-full object-contain"
      />
    </a>
  );
}

function SponsorsSection() {
  const { t } = useTranslation();

  // Group by tier in the configured order; anything with an unrecognized (or
  // missing) tier is collected into a trailing untiered group.
  const groups = SPONSOR_TIERS.map((tier) => [
    tier,
    SPONSORS.filter((s) => s.tier === tier),
  ]).filter(([, list]) => list.length > 0);
  const untiered = SPONSORS.filter((s) => !SPONSOR_TIERS.includes(s.tier));
  if (untiered.length) groups.push(['', untiered]);

  return (
    <section className="min-w-0">
      <SectionTitle>{t('support.sponsors_title', { defaultValue: 'Sponsors' })}</SectionTitle>

      {SPONSORS.length === 0 ? (
        <div
          data-testid="sponsors-empty"
          className="flex flex-wrap items-center gap-2.5 rounded-md border border-dashed border-border-strong bg-[color-mix(in_srgb,var(--color-brand)_4%,transparent)] p-3"
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-transparent bg-[color-mix(in_srgb,var(--color-brand)_12%,transparent)] text-[var(--color-brand)]">
            <Gem size={16} />
          </span>
          <span className="min-w-[150px] flex-1">
            <span className="block font-serif text-[0.9rem] leading-tight text-[var(--chrome-fg)]">
              {t('support.sponsors_empty_title', {
                defaultValue: 'Be the first to sponsor VoiceStudio',
              })}
            </span>
            <span className="block font-mono text-[0.6rem] uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg-dim)]">
              {t('support.sponsors_empty_desc', { defaultValue: 'Your logo here' })}
            </span>
          </span>
          <Button
            variant="primary"
            size="sm"
            leading={<Gem size={13} />}
            onClick={() => openExternal(SPONSOR_CONTACT.githubIssue)}
            aria-label={t('support.sponsors_become_aria', {
              defaultValue: 'Become a sponsor — opens a prefilled GitHub issue in your browser',
            })}
          >
            {t('support.sponsors_become', { defaultValue: 'Become a sponsor' })}
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {groups.map(([tier, list]) => (
            <div key={tier || 'untiered'}>
              {tier && (
                <div className="mb-1.5 font-mono text-[0.6rem] font-semibold uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg-dim)]">
                  {t(`support.sponsors_tier_${tier}`, { defaultValue: tier })}
                </div>
              )}
              <div className="grid grid-cols-[repeat(auto-fill,minmax(100px,1fr))] gap-2">
                {list.map((s) => (
                  <SponsorLogo key={s.name} sponsor={s} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {SPONSORS.length > 0 && (
        <div className="mt-2.5 flex flex-wrap items-center justify-center gap-2">
          <Button
            variant="primary"
            size="sm"
            leading={<Gem size={13} />}
            onClick={() => openExternal(SPONSOR_CONTACT.githubIssue)}
            aria-label={t('support.sponsors_become_aria', {
              defaultValue: 'Become a sponsor — opens a prefilled GitHub issue in your browser',
            })}
          >
            {t('support.sponsors_become', { defaultValue: 'Become a sponsor' })}
          </Button>
          <button
            type="button"
            onClick={() => openExternal(SPONSOR_CONTACT.docsUrl)}
            className="font-sans text-[0.7rem] font-semibold text-[var(--chrome-accent)] hover:underline"
          >
            {t('support.sponsors_learn_more', { defaultValue: 'What sponsors get' })}
          </button>
        </div>
      )}
    </section>
  );
}

/* ── Support (donate) panel ───────────────────────────────────────────── */
function SupportView() {
  const { t } = useTranslation();
  const [progress, setProgress] = useState(BUNDLED_PROGRESS);
  const [amount, setAmount] = useState(null); // none pre-selected by design

  useEffect(() => {
    let alive = true;
    loadDonationProgress().then((p) => {
      if (alive) setProgress(p);
    });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-col items-center justify-center gap-4 py-3 text-center">
        <span className="group flex size-16 shrink-0 items-center justify-center rounded-2xl border border-transparent bg-[color-mix(in_srgb,var(--color-brand)_12%,transparent)]">
          <Heart
            size={30}
            className="motion-safe:transition-transform motion-safe:duration-200 motion-safe:group-hover:scale-110 motion-safe:group-hover:-rotate-6 text-[var(--color-brand)] [fill:color-mix(in_srgb,var(--color-brand)_35%,transparent)] drop-shadow-[0_0_12px_color-mix(in_srgb,var(--color-brand)_50%,transparent)]"
          />
        </span>
        <h2 className="relative inline-block font-serif text-[1.7rem] font-normal leading-tight tracking-[-0.02em] text-[var(--chrome-fg)]">
          {t('donate.hero_title')}
          <span className="lp-hero__sweep" aria-hidden="true" />
        </h2>
      </header>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-6">
        <div className="flex min-w-0 flex-col gap-3.5">
          <Card className="gap-0 rounded-2xl border-border bg-[color-mix(in_srgb,var(--chrome-accent)_4%,transparent)] p-4 shadow-none">
            <GoalBar progress={progress} />
          </Card>

          <section>
            <SectionTitle>
              {t('donate.suggested_title', { defaultValue: 'Pick an amount' })}
            </SectionTitle>
            <div
              className="grid grid-cols-4 gap-1.5"
              role="group"
              aria-label={t('donate.suggested_title', { defaultValue: 'Pick an amount' })}
            >
              {SUGGESTED_AMOUNTS.map((a) => {
                const selected = amount === a.value;
                return (
                  <button
                    key={a.value}
                    type="button"
                    aria-pressed={selected}
                    aria-label={
                      a.common
                        ? `${a.label} — ${t('donate.most_common', { defaultValue: 'most common' })}`
                        : a.label
                    }
                    onClick={() => setAmount(selected ? null : a.value)}
                    className={`flex min-h-12 items-center justify-center rounded-xl border px-1.5 py-1.5 motion-safe:transition-all motion-safe:duration-200 motion-safe:active:scale-95 ${
                      selected
                        ? 'border-[var(--chrome-accent)] bg-[var(--chrome-accent-bg)]'
                        : `${a.common ? 'border-transparent' : 'border-border'} hover:border-transparent hover:bg-[color-mix(in_srgb,var(--chrome-accent)_7%,transparent)]`
                    }`}
                  >
                    <span className="font-serif text-[0.95rem] font-medium text-[var(--chrome-fg)]">
                      {a.label}
                    </span>
                  </button>
                );
              })}
              <button
                type="button"
                aria-pressed={amount === 'custom'}
                onClick={() => setAmount(amount === 'custom' ? null : 'custom')}
                className={`flex min-h-12 items-center justify-center rounded-xl border px-1.5 py-1.5 motion-safe:transition-all motion-safe:duration-200 motion-safe:active:scale-95 ${
                  amount === 'custom'
                    ? 'border-[var(--chrome-accent)] bg-[var(--chrome-accent-bg)]'
                    : 'border-border hover:border-transparent hover:bg-[color-mix(in_srgb,var(--chrome-accent)_7%,transparent)]'
                }`}
              >
                <span className="font-mono text-[0.7rem] uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg-muted)]">
                  {t('donate.custom', { defaultValue: 'Custom' })}
                </span>
              </button>
            </div>
          </section>

          <section>
            <SectionTitle>
              {typeof amount === 'number'
                ? t('donate.choose_method_amount', {
                    defaultValue: 'Continue with ${{amount}}',
                    amount,
                  })
                : t('donate.choose_method', { defaultValue: 'Choose how to give' })}
            </SectionTitle>
            <div className="grid grid-cols-2 gap-2">
              {METHODS.map((m) => (
                <LinkCard
                  key={m.id}
                  icon={m.icon}
                  label={m.label}
                  hue={DONATE_HUE}
                  onClick={() =>
                    openExternal(methodUrl(m, typeof amount === 'number' ? amount : null))
                  }
                />
              ))}
            </div>
          </section>
        </div>

        <div className="flex min-w-0 flex-col gap-3.5">
          <SponsorsSection />
          <section>
            <SectionTitle>{t('support.other_ways')}</SectionTitle>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="subtle"
                size="sm"
                leading={<Star size={13} />}
                onClick={() => openExternal('https://github.com/debpalash/VoiceStudio')}
              >
                {t('support.star_github')}
              </Button>
              <Button
                variant="subtle"
                size="sm"
                leading={<MessageCircle size={13} />}
                onClick={() => openExternal('https://discord.gg/bzQavDfVV9')}
              >
                {t('support.join_discord')}
              </Button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

/* ── Commercial License panel ─────────────────────────────────────────── */
const LICENSE_EMAIL = 'VoiceStudio@palash.dev';
const LICENSE_MAILTO =
  'mailto:VoiceStudio@palash.dev?subject=VoiceStudio Commercial License Inquiry' +
  '&body=Hi Palash,%0A%0AI%27d like to talk about a commercial license for VoiceStudio.%0A%0AOrganization:%0ATeam size:%0AUse case:%0A';

function LicenseView() {
  const { t } = useTranslation();
  const WHY_ITEMS = [
    { icon: Shield, label: t('enterprise.benefit_ip') },
    { icon: Zap, label: t('enterprise.benefit_cost') },
    { icon: Headphones, label: t('enterprise.benefit_support') },
  ];
  return (
    <div className="flex flex-col gap-4">
      <header className="text-center">
        <Badge tone="neutral" size="sm">
          {t('enterprise.badge')}
        </Badge>
        <h2 className="relative mt-2 inline-block font-serif text-[1.8rem] font-normal leading-tight tracking-[-0.02em] text-[var(--chrome-fg)]">
          {t('enterprise.hero_title')}
          <span className="lp-hero__sweep" aria-hidden="true" />
        </h2>
        <p className="mx-auto mt-3 max-w-[680px] font-sans text-[0.78rem] leading-[1.5] text-[var(--chrome-fg-muted)]">
          {t('enterprise.hero_simple', {
            defaultValue:
              'VoiceStudio is free and open-source under the AGPL-3.0 — including for commercial and internal business use. You only need a commercial license to embed it in a closed-source product without AGPL’s copyleft obligations.',
          })}
        </p>
      </header>

      <section className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-2">
        {WHY_ITEMS.map(({ icon: Icon, label }) => (
          <Card
            key={label}
            className="flex-row items-center gap-2.5 rounded-md border-border bg-transparent p-3 shadow-none transition-colors hover:border-border-strong hover:bg-[var(--chrome-hover-bg)]"
          >
            <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-transparent bg-[color-mix(in_srgb,var(--color-brand)_10%,transparent)] text-[var(--color-brand)]">
              <Icon size={15} />
            </span>
            <div className="font-mono text-[0.68rem] font-semibold uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg)]">
              {label}
            </div>
          </Card>
        ))}
      </section>

      <section>
        <Card className="flex-row flex-wrap items-center justify-center gap-3 rounded-md border-border bg-[color-mix(in_srgb,#fe8019_5%,transparent)] p-4 text-center shadow-none">
          <Button
            variant="subtle"
            size="sm"
            leading={<Mail size={13} />}
            onClick={() => openExternal(LICENSE_MAILTO)}
            className="border-transparent bg-[color-mix(in_srgb,#fe8019_18%,transparent)] font-semibold text-[var(--chrome-fg)] hover:border-transparent hover:bg-[color-mix(in_srgb,#fe8019_28%,transparent)]"
          >
            {t('enterprise.request_quote')}
          </Button>
          <button
            type="button"
            onClick={() => openExternal(LICENSE_MAILTO)}
            title={LICENSE_EMAIL}
            className="font-mono text-[0.65rem] text-[var(--chrome-accent)] hover:underline"
          >
            {LICENSE_EMAIL}
          </button>
        </Card>
      </section>
    </div>
  );
}

export default function SupportPage({ onBack, initialView = 'support' }) {
  const { t } = useTranslation();
  const [view, setView] = useState(() => viewFromRoute(initialView));

  // App.jsx reuses this component across donate / enterprise / contact and
  // changes only the prop, so route changes must also move the active tab.
  useEffect(() => {
    setView(viewFromRoute(initialView));
  }, [initialView]);

  const tabItems = [
    { id: 'support', label: t('support.tab_support'), icon: Heart },
    { id: 'license', label: t('support.tab_license'), icon: Building2 },
    { id: 'contact', label: t('logs.contact', { defaultValue: 'Contact' }), icon: MessageCircle },
  ];
  const panelLabel = tabItems.find((item) => item.id === view)?.label;

  return (
    <div className="relative isolate flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--chrome-bg)] [container-type:inline-size] [container-name:support-shell]">
      {/* Aurora backdrop — shared with the Launchpad */}
      <div className="lp-aurora" aria-hidden="true">
        <span className="lp-aurora__blob lp-aurora__blob--pink" />
        <span className="lp-aurora__blob lp-aurora__blob--green" />
        <span className="lp-aurora__blob lp-aurora__blob--amber" />
      </div>

      <div className="relative z-[2] flex shrink-0 items-center justify-between gap-3 px-8 pt-3">
        <Button variant="subtle" size="sm" onClick={onBack} leading={<ArrowLeft size={14} />}>
          {t('donate.back')}
        </Button>
        <Tabs
          items={tabItems}
          value={view}
          onChange={setView}
          size="sm"
          aria-label={t('support.toggle_label')}
        />
        <span className="w-24 shrink-0" aria-hidden="true" />
      </div>

      <main
        id={`support-${view === 'license' ? 'license' : view === 'contact' ? 'contact' : 'give'}`}
        role="tabpanel"
        aria-label={panelLabel}
        className="relative z-[1] mx-auto flex min-h-0 w-full max-w-[900px] flex-1 flex-col overflow-y-auto px-6 py-8"
        key={view}
      >
        {view === 'support' ? (
          <SupportView />
        ) : view === 'license' ? (
          <LicenseView />
        ) : (
          <div className="flex flex-col gap-4">
            <header className="flex items-center justify-center gap-3 text-center">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-md border border-transparent bg-[color-mix(in_srgb,#d3869b_12%,transparent)]">
                <MessageCircle size={20} className="text-[#f3a5b6]" />
              </span>
              <h2 className="relative inline-block font-serif text-[1.7rem] font-normal leading-tight tracking-[-0.02em] text-[var(--chrome-fg)]">
                {t('contact.hero_title', { defaultValue: 'We\u2019d love to hear from you' })}
                <span className="lp-hero__sweep" aria-hidden="true" />
              </h2>
            </header>
            <ContactSections onSupport={() => setView('support')} />
          </div>
        )}
      </main>
    </div>
  );
}
