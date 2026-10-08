import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  ArrowUpRightIcon,
  BlocksIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  GlobeIcon,
  HeartIcon,
  PlusIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { runRendererTask } from '@/lib/global-error-recovery';
import { SponsorInquiry } from './sponsor-inquiry';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import palashAvatar from '../../../../../public/social/palash.png';
import studioAvatar from '../../../../../public/social/voicestudio.jpg';
import kofiLogo from '../../../../../public/social/kofi.svg';
import paypalLogo from '../../../../../public/social/paypal.svg';
import githubLogo from '../../../../../public/social/github.svg';
import discordLogo from '../../../../../public/social/discord.svg';
import { getBridge } from '@/components/bridge';
import { DISCORD_URL, REPO_URL, X_URL } from '@shared/utils/contactLinks';
import { KOFI_URL, PAYPAL_URL } from '@shared/utils/donateLinks';
import './sponsor-footer.css';

// GitHub snapshot checked 2026-09-25. Installer downloads sum .AppImage,
// .deb, .dmg, .exe, .msi and .pkg assets for Electron releases v0.5.3–v0.5.6.
// Views are repository traffic for 2026-09-10 through 2026-09-23.
const SPONSOR_REACH = {
  downloads: '22,678',
  views: '207,236',
  stars: '35,336',
} as const;

/** Lives in the content column, so it never covers the editor or its sidebar. */
export function SponsorFooter() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [inquiryOpen, setInquiryOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed)
    return (
      <div className="flex h-6 shrink-0 justify-end border-t border-border/50 bg-sidebar px-2">
        <button
          type="button"
          aria-label={t('homeUi.expandFooter')}
          title={t('homeUi.expandFooter')}
          aria-expanded={false}
          onClick={() => setCollapsed(false)}
          className="flex w-8 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          <ChevronUpIcon className="size-3.5" aria-hidden="true" />
        </button>
      </div>
    );

  return (
    <div className="sponsor-footer-host">
      <footer aria-label={t('integrationCatalog.title')} className="sponsor-strip">
        <button
          type="button"
          className="sponsor-footer-action"
          onClick={() =>
            runRendererTask('Open integrations', () => navigate({ to: '/integrations' }))
          }
        >
          <BlocksIcon aria-hidden="true" className="size-4" />
          <span>{t('integrationCatalog.title')}</span>
        </button>
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                className="sponsor-footer-action sponsor-footer-action--brand"
                onClick={() => setInquiryOpen(true)}
              />
            }
          >
            <PlusIcon aria-hidden="true" className="size-4" />
            <span>{t('sponsorSlot.footer_brand')}</span>
          </TooltipTrigger>
          <TooltipContent
            surface="theme"
            side="top"
            sideOffset={8}
            className="sponsor-footer-tooltip"
          >
            <strong>{t('sponsorSlot.footer_promo')}</strong>
            <span>{t('sponsorSlot.partner_subtitle')}</span>
            <dl className="sponsor-footer-stats">
              <div>
                <dt>{t('sponsorSlot.footer_downloads')}</dt>
                <dd>{SPONSOR_REACH.downloads}</dd>
              </div>
              <div>
                <dt>{t('sponsorSlot.footer_views')}</dt>
                <dd>{SPONSOR_REACH.views}</dd>
              </div>
              <div>
                <dt>{t('sponsorSlot.footer_stars')}</dt>
                <dd>{SPONSOR_REACH.stars}</dd>
              </div>
            </dl>
            <small>{t('sponsorSlot.footer_stats_note')}</small>
          </TooltipContent>
        </Tooltip>
        <div className="sponsor-footer-links">
          {[
            { label: t('contact.website'), href: 'https://voicestudio.sh', Icon: GlobeIcon },
            { label: t('support.join_discord'), href: DISCORD_URL, logo: discordLogo },
          ].map(
            ({ label, href, Icon, logo }) => (
              <Tooltip key={href}>
                <TooltipTrigger
                  render={
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="sponsor-footer-pro"
                      aria-label={label}
                      onClick={(event) => {
                        const bridge = getBridge();
                        if (!bridge) return;
                        event.preventDefault();
                        runRendererTask('Open project link', () => bridge.files.openExternal(href));
                      }}
                    />
                  }
                >
                  {Icon ? (
                    <Icon aria-hidden="true" className="size-4" />
                  ) : (
                    <img src={logo} alt="" aria-hidden="true" className="size-4" />
                  )}
                </TooltipTrigger>
                <TooltipContent surface="theme" side="top">
                  {label}
                </TooltipContent>
              </Tooltip>
            ),
          )}
          <Popover>
            <PopoverTrigger
              openOnHover
              delay={180}
              closeDelay={250}
              render={
                <button
                  type="button"
                  className="sponsor-footer-pro"
                  aria-label={t('contact.follow_cta')}
                />
              }
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="size-3.5" aria-hidden="true">
                <path d="M18.9 2H22l-6.8 7.8L23.2 22h-6.3L12 14.6 5.5 22H2.3l8.2-9.4L.8 2h6.5l5.1 6.8L18.9 2Zm-1.1 18h1.7L6.4 3.9H4.6L17.8 20Z" />
              </svg>
            </PopoverTrigger>
            <PopoverContent
              side="top"
              align="end"
              className="sponsor-follow-popup"
              aria-label={t('contact.follow_cta')}
            >
              <p className="sponsor-follow-heading sponsor-follow-heading--warm">
                <HeartIcon aria-hidden="true" className="size-3.5 shrink-0" />
                <span>{t('contact.support_follow_title')}</span>
              </p>
              <div className="sponsor-follow-list">
                {[
                  { name: 'Palash', handle: 'idebpalash', href: X_URL, avatar: palashAvatar },
                  {
                    name: 'VoiceStudio',
                    handle: 'voicestudiosh',
                    href: 'https://x.com/voicestudiosh',
                    avatar: studioAvatar,
                  },
                ].map(({ name, handle, href, avatar }) => (
                  <a
                    key={handle}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="sponsor-follow-card"
                    data-profile={handle}
                    aria-label={`${t('contact.follow_cta')} · @${handle}`}
                    onClick={(event) => {
                      const bridge = getBridge();
                      if (!bridge) return;
                      event.preventDefault();
                      runRendererTask('Open X profile', () => bridge.files.openExternal(href));
                    }}
                  >
                    <img
                      src={avatar}
                      alt=""
                      width={56}
                      height={56}
                      className="sponsor-follow-avatar"
                    />
                    <span className="sponsor-follow-cta">{t('contact.follow_cta')}</span>
                    <span className="sponsor-follow-identity">
                      <span className="block text-sm font-semibold">{name}</span>
                      <span className="block text-xs text-muted-foreground">@{handle}</span>
                    </span>
                  </a>
                ))}
              </div>
            </PopoverContent>
          </Popover>
          <Popover>
            <PopoverTrigger
              openOnHover
              delay={180}
              closeDelay={250}
              render={
                <button
                  type="button"
                  className="sponsor-footer-pro sponsor-footer-donate"
                  aria-label={t('donate.title')}
                />
              }
            >
              <HeartIcon aria-hidden="true" className="size-4" />
            </PopoverTrigger>
            <PopoverContent
              side="top"
              align="end"
              className="sponsor-support-popup"
              aria-label={t('donate.title')}
            >
              <p className="sponsor-follow-heading sponsor-follow-heading--warm">
                <HeartIcon aria-hidden="true" className="size-3.5 shrink-0" />
                <span>{t('donate.hero_title')}</span>
              </p>
              <div className="sponsor-support-list">
                {[
                  { label: 'Ko-fi', href: KOFI_URL, logo: kofiLogo, brand: 'kofi' },
                  { label: 'PayPal', href: PAYPAL_URL, logo: paypalLogo, brand: 'paypal' },
                  {
                    label: t('support.star_github'),
                    href: REPO_URL,
                    logo: githubLogo,
                    brand: 'github',
                  },
                ].map(({ label, href, logo, brand }) => (
                  <a
                    key={href}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="sponsor-support-link"
                    onClick={(event) => {
                      const bridge = getBridge();
                      if (!bridge) return;
                      event.preventDefault();
                      runRendererTask('Open support link', () => bridge.files.openExternal(href));
                    }}
                  >
                    <span className="sponsor-support-icon" data-brand={brand}>
                      <img
                        src={logo}
                        alt=""
                        width={21}
                        height={21}
                        className="sponsor-brand-logo"
                      />
                    </span>
                    <span className="min-w-0 flex-1">{label}</span>
                    <ArrowUpRightIcon
                      aria-hidden="true"
                      className="size-3.5 text-muted-foreground"
                    />
                  </a>
                ))}
              </div>
              <button
                type="button"
                className="sponsor-support-link sponsor-support-all"
                onClick={() =>
                  runRendererTask('Open donations', () => navigate({ to: '/settings/support' }))
                }
              >
                <HeartIcon aria-hidden="true" className="size-4" />
                <span className="min-w-0 flex-1">{t('donate.title')}</span>
                <ArrowUpRightIcon aria-hidden="true" className="size-3.5" />
              </button>
            </PopoverContent>
          </Popover>
          <Tooltip>
            <TooltipTrigger
              render={
                <button
                  type="button"
                  className="sponsor-footer-pro"
                  aria-label={t('homeUi.collapseFooter')}
                  aria-expanded={true}
                  onClick={() => setCollapsed(true)}
                />
              }
            >
              <ChevronDownIcon aria-hidden="true" className="size-4" />
            </TooltipTrigger>
            <TooltipContent surface="theme" side="top">
              {t('homeUi.collapseFooter')}
            </TooltipContent>
          </Tooltip>
        </div>
        <SponsorInquiry open={inquiryOpen} onOpenChange={setInquiryOpen} />
      </footer>
    </div>
  );
}
