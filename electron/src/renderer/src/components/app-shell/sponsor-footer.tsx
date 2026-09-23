import { runRendererTask } from '@/lib/global-error-recovery';
import { VOICE_AI_DIRECTORY } from '../../../../../../frontend/src/config/voice-ai-directory';
import './sponsor-footer.css';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { SponsorInquiry } from './sponsor-inquiry';
import {
  ArrowUpRightIcon,
  BlocksIcon,
  ChevronRightIcon,
  CircleIcon,
  SearchIcon,
  GemIcon,
  PlusIcon,
  TriangleIcon,
  XIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { SPONSORS, SPONSOR_TIERS } from '../../../../../../frontend/src/config/sponsors';
import {
  getIntegrationBySlug,
  integrationSlug,
} from '../../../../../../frontend/src/config/integration-catalog';

const linkClass =
  'flex min-h-9 shrink-0 items-center gap-2 rounded-lg px-2.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-primary motion-safe:transition-colors';

/** Lives in the content column, so it never covers the editor or its sidebar. */
export function SponsorFooter() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const toggleRef = useRef<HTMLButtonElement>(null);
  const logoScrollerRef = useRef<HTMLDivElement>(null);
  const edgeScrollRef = useRef(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      const scroller = logoScrollerRef.current;
      if (scroller && edgeScrollRef.current) scroller.scrollLeft += edgeScrollRef.current;
    }, 16);
    return () => window.clearInterval(timer);
  }, []);
  const entries = [
    ...SPONSORS.map((sponsor) => ({ ...sponsor, featured: true })),
    ...VOICE_AI_DIRECTORY.filter(
      (example) => !SPONSORS.some((sponsor) => sponsor.url === example.url),
    ).map((example) => ({ ...example, tier: '', featured: false })),
  ];
  const visibleSponsors = entries.filter((sponsor) =>
    `${sponsor.name} ${sponsor.tier} ${sponsor.url}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  // Logos open the in-app integration page, never an outside link; the page
  // itself offers the vendor's website for anyone who wants it.
  const openIntegration = (name: string) => {
    const slug = integrationSlug(name);
    runRendererTask('Open integration', () =>
      navigate(
        getIntegrationBySlug(slug)
          ? { to: '/integrations/$slug', params: { slug } }
          : { to: '/integrations' },
      ),
    );
  };
  const collapse = () => {
    setExpanded(false);
    toggleRef.current?.focus();
  };
  const [inquiryOpen, setInquiryOpen] = useState(false);
  return (
    <div className="sponsor-footer-host">
      {expanded && (
        <section
          id="sponsor-catalog"
          aria-label={t('integrationCatalog.title')}
          className="sponsor-catalog"
          onKeyDown={(event) => {
            if (event.key === 'Escape' && !inquiryOpen) {
              event.stopPropagation();
              collapse();
            }
          }}
        >
          <header className="sponsor-catalog-header">
            <div>
              <h2>{t('integrationCatalog.title')}</h2>
              <p>{t('integrationCatalog.description')}</p>
              {VOICE_AI_DIRECTORY.length > 0 && <p>{t('directoryExamples.notice')}</p>}
            </div>
            <button
              type="button"
              className={linkClass}
              onClick={collapse}
              aria-label={t('common.close')}
            >
              <XIcon aria-hidden="true" className="size-4" />
            </button>
          </header>
          <label className="sponsor-catalog-search">
            <SearchIcon aria-hidden="true" className="size-4" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label={t('common.search')}
              placeholder={t('common.search')}
            />
          </label>
          <div className="sponsor-catalog-grid">
            {visibleSponsors.map((sponsor) => (
              <button
                type="button"
                key={sponsor.url}
                className="sponsor-catalog-card"
                onClick={() => openIntegration(sponsor.name)}
              >
                <div className="sponsor-catalog-card-top">
                  <img
                    src={sponsor.logoUrl}
                    alt=""
                    loading="lazy"
                    onError={(event) => {
                      event.currentTarget.style.display = 'none';
                    }}
                  />
                  <ChevronRightIcon aria-hidden="true" className="size-4" />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3>{sponsor.name}</h3>
                  <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                    {t(
                      sponsor.featured
                        ? 'integrationCatalog.featured'
                        : 'directoryExamples.example',
                    )}
                  </span>
                </div>
                {SPONSOR_TIERS.includes(sponsor.tier) && (
                  <span>{t('support.sponsors_tier_' + sponsor.tier)}</span>
                )}
                <p>{sponsor.url}</p>
              </button>
            ))}
            {entries.length > 0 && visibleSponsors.length === 0 && (
              <p role="status" className="text-sm text-muted-foreground">
                {t('common.no_matches')}
              </p>
            )}
            <button
              type="button"
              className="sponsor-catalog-card sponsor-catalog-book"
              onClick={() => setInquiryOpen(true)}
            >
              <div className="sponsor-catalog-card-top">
                <GemIcon aria-hidden="true" className="size-7" />
                <PlusIcon aria-hidden="true" className="size-5" />
              </div>
              <h3>{t('support.sponsors_empty_desc')}</h3>
              <p>{t('sponsorSlot.preview_detail')}</p>
              <span>{t('sponsorSlot.book')}</span>
            </button>
          </div>
        </section>
      )}
      <footer aria-label={t('integrationCatalog.title')} className="sponsor-strip">
        <button
          ref={toggleRef}
          type="button"
          className="sponsor-catalog-toggle"
          aria-label={t('integrationCatalog.title')}
          onClick={() =>
            runRendererTask('Open integrations', () => navigate({ to: '/integrations' }))
          }
          title={t('integrationCatalog.title')}
        >
          <BlocksIcon aria-hidden="true" className="size-4" />
        </button>
        <div
          ref={logoScrollerRef}
          className="sponsor-strip-logos"
          onMouseMove={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            const edge = Math.min(72, bounds.width * 0.18);
            edgeScrollRef.current =
              event.clientX < bounds.left + edge ? -5 : event.clientX > bounds.right - edge ? 5 : 0;
          }}
          onMouseLeave={() => {
            edgeScrollRef.current = 0;
          }}
        >
          {entries.map((sponsor) => (
            <Tooltip key={sponsor.url}>
              <TooltipTrigger
                render={
                  <button
                    type="button"
                    className="sponsor-logo-tile"
                    aria-label={t('support.sponsors_logo_aria', { name: sponsor.name })}
                    onClick={() => openIntegration(sponsor.name)}
                  />
                }
              >
                <img
                  src={sponsor.logoUrl}
                  alt=""
                  loading="lazy"
                  className="h-5 max-w-24 object-contain"
                  onError={(event) => {
                    event.currentTarget.style.display = 'none';
                  }}
                />
                <span>{sponsor.name}</span>
              </TooltipTrigger>
              <TooltipContent
                surface="theme"
                side="top"
                sideOffset={8}
                showArrow={false}
                className="sponsor-logo-tooltip w-[min(50vw,320px)] max-w-[min(50vw,320px)] min-h-[124px] flex-col items-start justify-between gap-2 break-words p-3"
              >
                <span className="sponsor-tooltip-heading">
                  <img src={sponsor.logoUrl} alt="" loading="lazy" />
                  <span className="font-medium text-foreground">{sponsor.name}</span>
                </span>
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                  {t(
                    sponsor.featured ? 'integrationCatalog.featured' : 'directoryExamples.example',
                  )}
                </span>
                {SPONSOR_TIERS.includes(sponsor.tier) && (
                  <span className="text-xs text-muted-foreground">
                    {t('support.sponsors_tier_' + sponsor.tier)}
                  </span>
                )}
                <span className="text-xs leading-relaxed text-muted-foreground">
                  {t('integrationCatalog.description')}
                </span>
                <span className="flex max-w-full items-center gap-1 text-xs text-primary">
                  <span className="break-all">{sponsor.url}</span>
                  <ChevronRightIcon aria-hidden="true" className="size-3 shrink-0" />
                </span>
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                onClick={() => setInquiryOpen(true)}
                className="sponsor-book-tile sponsor-book-tile--combined"
                aria-label={t('sponsorSlot.book')}
              />
            }
          >
            <span aria-hidden="true" className="sponsor-book-mark">
              <CircleIcon />
              <span className="sponsor-book-question">?</span>
            </span>
            <span className="sponsor-book-copy">
              <strong>{t('sponsorSlot.footer_brand')}</strong>
              <small>{t('sponsorSlot.footer_book')}</small>
            </span>
            <PlusIcon aria-hidden="true" className="sponsor-book-plus" />
          </TooltipTrigger>
          <TooltipContent
            surface="theme"
            side="top"
            sideOffset={8}
            showArrow={false}
            className="sponsor-book-tooltip w-[min(72vw,340px)] max-w-[min(72vw,340px)] flex-col items-stretch gap-2.5 p-3"
          >
            <span className="sponsor-book-tooltip-eyebrow">
              <TriangleIcon aria-hidden="true" />
              <span>{t('sponsorSlot.partner')}</span>
            </span>
            <strong className="sponsor-book-tooltip-title">{t('sponsorSlot.title')}</strong>
            <span className="sponsor-book-tooltip-lead">{t('sponsorSlot.description')}</span>
            <span className="sponsor-book-tooltip-detail">{t('support.sponsors_perk')}</span>
            <span className="sponsor-book-tooltip-detail">{t('sponsorSlot.preview_detail')}</span>
            <button
              type="button"
              className="sponsor-book-tooltip-cta"
              onClick={() => setInquiryOpen(true)}
            >
              {t('sponsorSlot.footer_book')}
              <ArrowUpRightIcon aria-hidden="true" />
            </button>
          </TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                aria-label={t('supportPlans.remove')}
                className={linkClass + ' justify-center px-2'}
                onClick={() =>
                  runRendererTask('Open support', () =>
                    navigate({ to: '/settings/support', search: { compare: true } }),
                  )
                }
              />
            }
          >
            <XIcon aria-hidden="true" className="size-3.5" />
          </TooltipTrigger>
          <TooltipContent surface="theme" side="top">
            {t('supportPlans.remove')}
          </TooltipContent>
        </Tooltip>
        <SponsorInquiry open={inquiryOpen} onOpenChange={setInquiryOpen} />
      </footer>
    </div>
  );
}
