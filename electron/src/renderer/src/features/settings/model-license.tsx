import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { ExternalLink } from '@/components/external-link';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { apiJson } from '@/lib/api/client';
import { cn } from '@/lib/utils';
import { ModelLicenseIcon } from './model-license-icon';
import { LicenceAcceptanceFooter, toAcceptable } from './model-licence-acceptance';
import {
  LICENCE_DIALOG_SURFACE,
  LicenceSection,
  ModelLicenceView,
  useModelLicenceDetails,
} from './model-licence-view';
import {
  canAcknowledgeModelPlan,
  modelLicenseCategory,
  modelLicenseCategoryKey,
  modelLicenseUrl,
  type ModelLicenceAcceptance,
  type ModelLicenseInfo,
  type ModelReviewPlan,
} from './model-license-contract';

const blockerKeys: Record<string, string> = {
  component_closure_incomplete: 'modelLicense.blockerComponents',
  artifact_manifest_unverified: 'modelLicense.blockerArtifacts',
  terms_unverified: 'modelLicense.blockerTerms',
  runtime_revision_unverified: 'modelLicense.blockerRuntime',
  evidence_revision_mismatch: 'modelLicense.blockerMismatch',
  upstream_access_unverified: 'modelLicense.blockerAccess',
  variant_provenance_unverified: 'modelLicense.blockerVariant',
  remote_review_unsupported: 'modelLicense.blockerRemote',
};

export function ModelLicense({
  repoId,
  label,
  info,
  acceptance,
  target,
}: {
  repoId: string;
  label: string;
  info?: ModelLicenseInfo;
  acceptance?: ModelLicenceAcceptance;
  target: string;
}) {
  const { t } = useTranslation();
  const acknowledgementId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const generation = useRef(0);
  const busyRef = useRef<'prepare' | 'commit' | null>(null);
  const request = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<'prepare' | 'commit' | null>(null);
  const [plan, setPlan] = useState<ModelReviewPlan | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [failed, setFailed] = useState(false);
  const [completed, setCompleted] = useState(false);
  const ready = canAcknowledgeModelPlan(plan, repoId, target, info?.registry_digest);

  // A refreshed registry/selection invalidates the preview and acknowledgement.
  // Closing during preparation also makes its eventual response unusable.
  useEffect(() => {
    generation.current += 1;
    request.current?.abort();
    busyRef.current = null;
    setBusy(null);
    setPlan(null);
    setAcknowledged(false);
    setFailed(false);
    setCompleted(false);
    return () => {
      generation.current += 1;
      request.current?.abort();
    };
  }, [repoId, target, info?.registry_digest]);

  const close = () => {
    if (busyRef.current === 'commit') return;
    generation.current += 1;
    request.current?.abort();
    busyRef.current = null;
    setBusy(null);
    setPlan(null);
    setAcknowledged(false);
    setFailed(false);
    setOpen(false);
    setCompleted(false);
  };

  const prepare = async () => {
    if (busyRef.current) return;
    const current = ++generation.current;
    busyRef.current = 'prepare';
    setBusy('prepare');
    setFailed(false);
    setAcknowledged(false);
    setPlan(null);
    request.current = new AbortController();
    try {
      const result = await apiJson<ModelReviewPlan>('/models/install/prepare', {
        method: 'POST',
        signal: request.current.signal,
        body: JSON.stringify({ repo_id: repoId, target }),
      });
      if (generation.current === current) setPlan(result);
    } catch {
      if (generation.current === current) setFailed(true);
    } finally {
      if (generation.current === current) {
        busyRef.current = null;
        setBusy(null);
      }
    }
  };

  const commit = async () => {
    if (busyRef.current || !ready || !acknowledged || !plan) return;
    const current = generation.current;
    busyRef.current = 'commit';
    setBusy('commit');
    setFailed(false);
    try {
      const result = await apiJson<{ status: string }>('/models/install/commit', {
        method: 'POST',
        body: JSON.stringify({
          plan_id: plan.plan_id,
          plan_digest: plan.plan_digest,
          acknowledged_document_ids: plan.documents.map((document) => document.id),
          acknowledged: true,
          target,
        }),
      });
      if (result.status !== 'verified') throw new Error('Unexpected reviewed-cache result');
      if (generation.current === current) {
        setPlan(null);
        setAcknowledged(false);
        setCompleted(true);
      }
    } catch {
      if (generation.current === current) {
        // A changed/expired plan must be prepared and acknowledged again.
        setFailed(true);
        setPlan(null);
        setAcknowledged(false);
      }
    } finally {
      if (generation.current === current) {
        busyRef.current = null;
        setBusy(null);
      }
    }
  };

  const details = useModelLicenceDetails(repoId, open);
  const currentInfo = details.data?.info ?? info;
  const currentAcceptance = details.data?.acceptance ?? acceptance;
  const sourceLink = (url: string | null | undefined, text: string) => {
    const href = modelLicenseUrl(url);
    return href ? <ExternalLink href={href}>{text}</ExternalLink> : null;
  };
  const category = t(modelLicenseCategoryKey(currentInfo?.license_category));
  const review = t(
    currentInfo?.review_status === 'cleared' ? 'modelLicense.reviewed' : 'modelLicense.notReviewed',
  );
  const summary = `${currentInfo?.license || t('common.unknown')} · ${category} · ${review}`;
  const blockers = [...new Set([...(currentInfo?.blockers ?? []), ...(plan?.blockers ?? [])])];

  // Opt-in reviewed install, kept intact but out of the way.
  const verifiedInstall = (
    <LicenceSection title={t('modelLicense.sectionVerified')} defaultOpen={!!plan}>
      <p className="max-w-prose">{t('modelLicense.preview')}</p>
      <p className="max-w-prose text-muted-foreground">{t('modelLicense.disclaimer')}</p>
      <p className="break-all text-xs text-muted-foreground">
        {repoId} · {t('common.backend')}: {target}
      </p>
      {blockers.length > 0 && (
        <div className="rounded-lg border border-warning/30 bg-warning/5 p-3">
          <h4 className="font-medium">{t('modelLicense.unresolved')}</h4>
          <ul className="list-disc space-y-1 ps-5">
            {blockers.map((blocker) => (
              <li key={blocker}>{t(blockerKeys[blocker] ?? 'modelLicense.unknown')}</li>
            ))}
          </ul>
        </div>
      )}
      {plan && (
        <div className="space-y-3">
          <p>
            {t('modelLicense.downloadSource')}: <span className="break-all">{plan.source}</span>
          </p>
          <h4 className="font-medium">{t('modelLicense.components')}</h4>
          {plan.components.map((component) => (
            <details key={component.id} className="rounded-lg border border-border/60 p-3">
              <summary className="cursor-pointer rounded break-all focus-visible:ring-2 focus-visible:ring-ring">
                {component.id}
              </summary>
              <p>
                {component.credit} · {component.license}
              </p>
              <p className="break-all font-mono text-xs">{component.revision || t('common.unknown')}</p>
              <ul className="space-y-1 text-xs">
                {component.artifacts.map((artifact) => (
                  <li key={artifact.path} className="break-all">
                    {artifact.path} · SHA-256: <span className="font-mono">{artifact.sha256}</span>
                  </li>
                ))}
              </ul>
            </details>
          ))}
          <h4 className="font-medium">{t('modelLicense.documents')}</h4>
          {plan.documents.map((document) => (
            <details key={document.id} className="rounded-lg border border-border/60 p-3">
              <summary className="cursor-pointer rounded focus-visible:ring-2 focus-visible:ring-ring">
                {document.title}
              </summary>
              <p className="break-all font-mono text-xs">SHA-256: {document.sha256}</p>
              <pre className="my-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 font-sans text-sm leading-relaxed">
                {document.text}
              </pre>
              {sourceLink(document.source_url, t('modelLicense.readLicence'))}
            </details>
          ))}
          <p role="status">{t(ready ? 'modelLicense.ready' : 'modelLicense.blocked')}</p>
          {ready && (
            <>
              <p>{plan.acknowledgement.text}</p>
              <p className="break-all text-xs text-muted-foreground">
                {plan.acknowledgement.prompt_id} · {plan.acknowledgement.prompt_version} ·{' '}
                {plan.plan_digest}
              </p>
              <label
                htmlFor={acknowledgementId}
                className="flex items-start gap-2 rounded-lg border border-border p-3"
              >
                <input
                  id={acknowledgementId}
                  type="checkbox"
                  checked={acknowledged}
                  disabled={busy === 'commit'}
                  onChange={(event) => setAcknowledged(event.currentTarget.checked)}
                  className="mt-0.5 size-4 shrink-0 accent-primary focus-visible:ring-2 focus-visible:ring-ring"
                />
                <span>{t('modelLicense.acknowledge')}</span>
              </label>
            </>
          )}
        </div>
      )}
      {failed && (
        <p role="alert" className="text-destructive">
          {t('modelLicense.failed')}
        </p>
      )}
      {completed && <p role="status">{t('modelLicense.downloaded')}</p>}
      {busy && <p role="status">{t('common.loading')}</p>}
      <div className="flex justify-end">
        {completed ? null : ready ? (
          <Button
            className="h-auto min-h-8"
            disabled={Boolean(busy) || !acknowledged}
            onClick={() => void commit()}
          >
            <span className="whitespace-normal">{t('modelLicense.commit')}</span>
          </Button>
        ) : (
          <Button
            variant="outline"
            className="h-auto min-h-8"
            disabled={Boolean(busy) || target !== 'local' || !currentInfo}
            onClick={() => void prepare()}
          >
            <span className="whitespace-normal">{t('modelLicense.prepare')}</span>
          </Button>
        )}
      </div>
    </LicenceSection>
  );

  return (
    <>
      <Button
        ref={trigger}
        size="sm"
        variant="outline"
        className="gap-1.5"
        onClick={() => setOpen(true)}
        title={summary}
        aria-label={`${t('modelLicense.label')}: ${summary}; ${label}`}
      >
        <span aria-hidden="true">{t('modelLicense.label')}:</span>
        <ModelLicenseIcon category={modelLicenseCategory(currentInfo?.license_category)} />
      </Button>
      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
        <DialogContent
          showCloseButton={false}
          finalFocus={trigger}
          className={cn(
            'flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl',
            LICENCE_DIALOG_SURFACE,
          )}
        >
          <DialogHeader className="border-b border-border p-4">
            <DialogTitle>
              {t('modelLicense.label')} · {label}
            </DialogTitle>
            <DialogDescription className="break-all">{repoId}</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <ModelLicenceView
              details={details.data}
              infoFallback={info}
              extra={verifiedInstall}
            />
            {details.isError && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {t('modelLicense.detailsFailed')}
              </p>
            )}
          </div>
          <LicenceAcceptanceFooter
            models={
              currentAcceptance ? [toAcceptable(label, currentAcceptance)] : []
            }
            onClose={close}
            closeDisabled={busy === 'commit'}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
