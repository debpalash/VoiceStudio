import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import {
  CheckIcon,
  ChevronRightIcon,
  CircleHelpIcon,
  CopyIcon,
  TriangleAlertIcon,
  UserRoundIcon,
  XIcon,
} from 'lucide-react';
import { ExternalLink } from '@/components/external-link';
import { apiJson } from '@/lib/api/client';
import { cn } from '@/lib/utils';
import { ModelLicenseIcon } from './model-license-icon';
import {
  modelLicenseCategory,
  modelLicenseCategoryKey,
  modelLicenseUrl,
  type ModelLicenceAcceptance,
  type ModelLicenceDetails,
  type ModelLicenceHistoryEntry,
  type ModelLicenseInfo,
} from './model-license-contract';

/** Solid surface: legal text must not sit on translucent glass. */
export const LICENCE_DIALOG_SURFACE = 'bg-popover! [backdrop-filter:none]!';

export const modelLicenceDetailsKey = (repoId: string) => ['model-licence', repoId] as const;

/** One source for both licence dialogs: disclosure, acceptance and history. */
export function useModelLicenceDetails(repoId: string, enabled = true) {
  return useQuery({
    queryKey: modelLicenceDetailsKey(repoId),
    queryFn: () =>
      apiJson<ModelLicenceDetails>(`/models/licenses/details/${encodeURI(repoId)}`),
    enabled: enabled && !!repoId,
    staleTime: 0,
  });
}

export function formatLicenceDate(value: string | null | undefined, locale: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  } catch {
    return date.toISOString();
  }
}

/** `v2:a1b2c3…9f0e` — long digests stay identifiable without wrapping lines. */
export function shortHash(value: string | null | undefined): string {
  if (!value) return '';
  const [prefix, digest] = value.includes(':') ? value.split(':', 2) : ['', value];
  const short = digest.length > 14 ? `${digest.slice(0, 6)}…${digest.slice(-4)}` : digest;
  return prefix ? `${prefix}:${short}` : short;
}

function CopyValue({ value }: { value: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex items-center gap-1">
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs" title={value}>
        {shortHash(value)}
      </code>
      <button
        type="button"
        className="rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={copied ? t('modelLicense.copied') : t('modelLicense.copy')}
        title={copied ? t('modelLicense.copied') : t('modelLicense.copy')}
        onClick={() => {
          void navigator.clipboard
            ?.writeText(value)
            .then(() => setCopied(true))
            .catch(() => {});
        }}
      >
        {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
      </button>
    </span>
  );
}

// ── Summary ────────────────────────────────────────────────────────────────

const categoryPanel: Record<string, string> = {
  commercial: 'border-success/40 bg-success/10',
  conditions: 'border-warning/40 bg-warning/10',
  noncommercial: 'border-destructive/40 bg-destructive/10',
  unknown: 'border-border bg-muted/50',
};

export function LicenceAcceptanceState({ acceptance }: { acceptance?: ModelLicenceAcceptance }) {
  const { t, i18n } = useTranslation();
  if (!acceptance) return null;
  const at = formatLicenceDate(acceptance.last_action?.at, i18n.language);
  const acceptedAt = formatLicenceDate(acceptance.accepted_at, i18n.language);
  const fields = (acceptance.changed_fields ?? []).map((field) =>
    t(`modelLicense.field.${field}`, { defaultValue: field }),
  );
  let text: string;
  let tone = 'text-muted-foreground';
  let Icon = CircleHelpIcon;
  switch (acceptance.state) {
    case 'not_required':
      text = t('modelLicense.stateNotRequired');
      break;
    case 'accepted':
      text = at ? t('modelLicense.stateAccepted', { date: at }) : t('modelLicense.stateAcceptedUndated');
      tone = 'text-success';
      Icon = CheckIcon;
      break;
    case 'withdrawn':
      text = at ? t('modelLicense.stateWithdrawn', { date: at }) : t('modelLicense.stateWithdrawnUndated');
      tone = 'text-warning';
      Icon = TriangleAlertIcon;
      break;
    case 'terms_updated':
      text = acceptedAt
        ? t('modelLicense.stateUpdated', { date: acceptedAt })
        : t('modelLicense.stateUpdatedUndated');
      tone = 'text-warning';
      Icon = TriangleAlertIcon;
      break;
    default:
      text = t('modelLicense.stateNotAccepted');
      tone = 'text-warning';
      Icon = TriangleAlertIcon;
  }
  return (
    <div role="status" className="space-y-1 rounded-md bg-background/80 p-2.5">
      <p className="flex items-start gap-2 font-medium text-foreground">
        <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
        <span>{text}</span>
      </p>
      {acceptance.state === 'accepted' && acceptance.last_action?.fingerprint && (
        <p className="flex flex-wrap items-center gap-1 ps-6 text-xs text-muted-foreground">
          {t('modelLicense.termsHash')}: <CopyValue value={acceptance.last_action.fingerprint} />
        </p>
      )}
      {fields.length > 0 && (
        <p className="ps-6 text-sm">{t('modelLicense.changedFields', { fields: fields.join(', ') })}</p>
      )}
    </div>
  );
}

export function LicenceSummary({
  info,
  acceptance,
}: {
  info?: ModelLicenseInfo;
  acceptance?: ModelLicenceAcceptance;
}) {
  const { t } = useTranslation();
  const category = modelLicenseCategory(info?.license_category ?? acceptance?.category);
  const reviewed = info?.review_status === 'cleared';
  return (
    <section
      aria-label={t('modelLicense.summary')}
      className={cn('space-y-3 rounded-lg border p-4', categoryPanel[category])}
    >
      <div className="flex items-start gap-3">
        <ModelLicenseIcon category={category} className="mt-0.5 size-7 shrink-0" />
        <div className="min-w-0 space-y-1">
          <p className="text-base font-semibold leading-snug">
            {t(modelLicenseCategoryKey(category))}
          </p>
          <p className="text-sm font-medium">
            {info?.license || acceptance?.license || t('common.unknown')}
          </p>
          <p className="text-xs text-muted-foreground">
            {t(reviewed ? 'modelLicense.reviewed' : 'modelLicense.notReviewed')} ·{' '}
            {t('modelLicense.categoryHint')}
          </p>
        </div>
      </div>
      <LicenceAcceptanceState acceptance={acceptance} />
    </section>
  );
}

// ── What the licence allows ───────────────────────────────────────────────

type Verdict = 'allowed' | 'conditions' | 'not_allowed' | 'not_verified' | 'yours';

const verdictStyle: Record<Verdict, { icon: ReactNode; tone: string; key: string }> = {
  allowed: { icon: <CheckIcon className="size-3.5" />, tone: 'border-success/40 text-success', key: 'chipAllowed' },
  conditions: {
    icon: <TriangleAlertIcon className="size-3.5" />,
    tone: 'border-warning/40 text-warning',
    key: 'chipConditions',
  },
  not_allowed: {
    icon: <XIcon className="size-3.5" />,
    tone: 'border-destructive/40 text-destructive',
    key: 'chipNotAllowed',
  },
  not_verified: {
    icon: <CircleHelpIcon className="size-3.5" />,
    tone: 'border-border text-muted-foreground',
    key: 'chipNotVerified',
  },
  yours: { icon: <UserRoundIcon className="size-3.5" />, tone: 'border-info/40 text-info', key: 'chipYours' },
};

const restricted = (value?: string) => value === 'restricted' || !!value?.startsWith('noncommercial');

export function licenceVerdicts(info?: ModelLicenseInfo): Record<string, Verdict> {
  const category = modelLicenseCategory(info?.license_category);
  const model: Verdict = restricted(info?.commercial_inference)
    ? 'not_allowed'
    : category === 'commercial'
      ? 'allowed'
      : category === 'conditions'
        ? 'conditions'
        : category === 'noncommercial'
          ? 'not_allowed'
          : 'not_verified';
  return {
    model,
    outputs: restricted(info?.commercial_outputs) || category === 'noncommercial' ? 'not_allowed' : 'not_verified',
    redistribution: 'not_verified',
    voice: 'yours',
  };
}

export function LicencePermissions({ info }: { info?: ModelLicenseInfo }) {
  const { t } = useTranslation();
  const verdicts = licenceVerdicts(info);
  const rows = [
    ['model', 'permModel', 'permModelHint'],
    ['outputs', 'permOutputs', 'permOutputsHint'],
    ['redistribution', 'permRedistribution', 'permRedistributionHint'],
    ['voice', 'permVoice', 'permVoiceHint'],
  ] as const;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{t('modelLicense.sectionAllows')}</h3>
      <dl className="divide-y divide-border rounded-lg border border-border">
        {rows.map(([id, label, hint]) => {
          const verdict = verdictStyle[verdicts[id]];
          return (
            <div key={id} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 p-3">
              <div className="min-w-0 flex-1 basis-56">
                <dt className="font-medium">{t(`modelLicense.${label}`)}</dt>
                <dd className="text-xs text-muted-foreground">{t(`modelLicense.${hint}`)}</dd>
              </div>
              <dd
                className={cn(
                  'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium',
                  verdict.tone,
                )}
              >
                {verdict.icon}
                {t(`modelLicense.${verdict.key}`)}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

// ── Sources ────────────────────────────────────────────────────────────────

export function LicenceSources({ info }: { info?: ModelLicenseInfo }) {
  const { t } = useTranslation();
  const evidence = modelLicenseUrl(info?.evidence_url);
  const source = modelLicenseUrl(info?.source_url);
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{t('modelLicense.sectionSource')}</h3>
      <dl className="grid gap-1 text-sm sm:grid-cols-[max-content_1fr] sm:gap-x-4">
        <dt className="text-muted-foreground">{t('modelLicense.rightsHolder')}</dt>
        <dd className="break-words">{info?.credit || t('common.unknown')}</dd>
      </dl>
      {(evidence || source) && (
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {evidence && <ExternalLink href={evidence}>{t('modelLicense.readLicence')}</ExternalLink>}
          {source && <ExternalLink href={source}>{t('modelLicense.modelPage')}</ExternalLink>}
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t('modelLicense.external')}</p>
    </section>
  );
}

// ── Collapsible sections ─────────────────────────────────────────────────

export function LicenceSection({
  title,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details open={defaultOpen} className="group/section rounded-lg border border-border">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg p-3 font-medium focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon
          aria-hidden="true"
          className="size-4 shrink-0 transition-transform group-open/section:rotate-90"
        />
        <span className="flex-1">{title}</span>
        {count !== undefined && (
          <span className="rounded-full bg-muted px-2 text-xs text-muted-foreground">{count}</span>
        )}
      </summary>
      <div className="space-y-3 border-t border-border p-3 text-sm">{children}</div>
    </details>
  );
}

export function LicenceVariants({ info }: { info?: ModelLicenseInfo }) {
  const { t } = useTranslation();
  const variants = info?.variants ?? [];
  if (!variants.length) return null;
  return (
    <LicenceSection title={t('modelLicense.sectionVariants')} count={variants.length}>
      <p className="text-xs text-muted-foreground">{t('modelLicense.variantsHint')}</p>
      <ul className="space-y-3">
        {variants.map((variant) => {
          const evidence = modelLicenseUrl(variant.evidence_url);
          return (
            <li key={variant.id} className="space-y-1 rounded-md bg-muted/40 p-3">
              <p className="flex items-center gap-1.5 font-medium">
                <ModelLicenseIcon category={modelLicenseCategory(variant.license_category)} />
                <span className="break-all">{variant.label}</span>
              </p>
              <p>{t(modelLicenseCategoryKey(variant.license_category))}</p>
              {variant.observations?.output_terms && <p>{variant.observations.output_terms}</p>}
              {variant.observations?.license_status && <p>{variant.observations.license_status}</p>}
              {evidence && <ExternalLink href={evidence}>{t('modelLicense.readLicence')}</ExternalLink>}
            </li>
          );
        })}
      </ul>
    </LicenceSection>
  );
}

export function LicenceNotes({ info }: { info?: ModelLicenseInfo }) {
  const { t } = useTranslation();
  if (!info?.notes) return null;
  return (
    <LicenceSection title={t('modelLicense.sectionNotes')}>
      <p className="text-xs text-muted-foreground">{t('modelLicense.notes')}</p>
      <p className="max-w-prose whitespace-pre-line leading-relaxed">{info.notes}</p>
    </LicenceSection>
  );
}

function HistoryList({ history }: { history: ModelLicenceHistoryEntry[] }) {
  const { t, i18n } = useTranslation();
  if (!history.length) return <p>{t('modelLicense.historyEmpty')}</p>;
  return (
    <ol className="space-y-1">
      {history.map((entry, index) => (
        <li key={`${entry.at}-${index}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="font-medium">
            {t(entry.action === 'withdrawn' ? 'modelLicense.historyWithdrawn' : 'modelLicense.historyAccepted')}
          </span>
          <span>{formatLicenceDate(entry.at, i18n.language) ?? t('modelLicense.dateNotRecorded')}</span>
          {entry.fingerprint && <CopyValue value={entry.fingerprint} />}
          {entry.app_version && (
            <span className="text-xs text-muted-foreground">
              {t('modelLicense.appVersion')} {entry.app_version}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

export function LicenceTechnical({
  info,
  acceptance,
  history,
}: {
  info?: ModelLicenseInfo;
  acceptance?: ModelLicenceAcceptance;
  history?: ModelLicenceHistoryEntry[];
}) {
  const { t, i18n } = useTranslation();
  const rows: [string, ReactNode][] = [
    ['modelVersion', info?.runtime_revision ? <CopyValue value={info.runtime_revision} /> : null],
    ['evidenceVersion', info?.evidence_revision ? <CopyValue value={info.evidence_revision} /> : null],
    ['checked', formatLicenceDate(info?.evidence_checked_at, i18n.language)],
    ['termsHash', acceptance?.fingerprint ? <CopyValue value={acceptance.fingerprint} /> : null],
    [
      'registry',
      info?.registry_version ? (
        <span className="inline-flex flex-wrap items-center gap-1">
          {info.registry_version}
          {info.registry_digest && <CopyValue value={info.registry_digest} />}
        </span>
      ) : null,
    ],
  ];
  return (
    <LicenceSection title={t('modelLicense.sectionTechnical')}>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
        {rows.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="text-muted-foreground">{t(`modelLicense.${key}`)}</dt>
            <dd>{value || t('common.unknown')}</dd>
          </div>
        ))}
      </dl>
      <div className="space-y-1">
        <h4 className="font-medium">{t('modelLicense.history')}</h4>
        <HistoryList history={history ?? []} />
      </div>
    </LicenceSection>
  );
}

/** The full licence view shared by the catalogue and the in-feature dialog. */
export function ModelLicenceView({
  details,
  infoFallback,
  extra,
}: {
  details?: ModelLicenceDetails;
  infoFallback?: ModelLicenseInfo;
  extra?: ReactNode;
}) {
  const info = details?.info ?? infoFallback;
  return (
    <div className="space-y-4">
      <LicenceSummary info={info} acceptance={details?.acceptance} />
      <LicencePermissions info={info} />
      <LicenceSources info={info} />
      <div className="space-y-2">
        <LicenceVariants info={info} />
        <LicenceNotes info={info} />
        <LicenceTechnical info={info} acceptance={details?.acceptance} history={details?.history} />
        {extra}
      </div>
    </div>
  );
}
