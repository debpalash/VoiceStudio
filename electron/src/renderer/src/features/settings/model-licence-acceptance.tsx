import { useEffect, useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { platformFamily, type PlatformFamily } from '@/components/bridge';
import { ApiError, apiJson } from '@/lib/api/client';
import { cn } from '@/lib/utils';
import {
  MODEL_LICENCE_REQUIRED_EVENT,
  modelLicenseCategory,
  type ModelLicenceAcceptance,
  type ModelLicenceDetails,
  type ModelLicenceRequirement,
} from './model-license-contract';
import { ModelLicenseIcon } from './model-license-icon';
import {
  LICENCE_DIALOG_SURFACE,
  LicenceSection,
  ModelLicenceView,
  modelLicenceDetailsKey,
} from './model-licence-view';

/**
 * Button row per platform convention: macOS puts the default action rightmost
 * with Cancel beside it, Windows (Fluent) uses equal-width buttons with the
 * primary action first, Linux (GNOME/KDE) right-aligns Cancel then the action.
 * Rendered below the dialog's scroll region so it is always visible.
 */
function footerActionsClass(platform: PlatformFamily) {
  const base =
    'flex shrink-0 flex-col-reverse gap-2 border-t border-border px-8 py-4 sm:flex-row sm:items-center ' +
    // Fluent dialogs put the actions on their own tinted command area.
    (platform === 'windows' ? 'bg-muted' : 'bg-popover');
  if (platform === 'windows') return cn(base, 'sm:flex-row-reverse [&>button]:sm:flex-1');
  if (platform === 'mac') return cn(base, 'sm:justify-end [&>button]:sm:min-w-24');
  return cn(base, 'sm:flex-wrap sm:justify-end');
}

/** A model the footer can accept: what the user is shown is what gets recorded. */
export interface AcceptableModel {
  repo_id: string;
  label: string;
  fingerprint: string;
  required: boolean;
  accepted: boolean;
  category?: string;
  /** False while the terms failed or have not loaded: acceptance stays disabled. */
  termsLoaded?: boolean;
}

export function toAcceptable(
  label: string,
  acceptance: Pick<ModelLicenceAcceptance, 'repo_id' | 'fingerprint' | 'required' | 'accepted' | 'category'>,
): AcceptableModel {
  return {
    repo_id: acceptance.repo_id,
    label,
    fingerprint: acceptance.fingerprint,
    required: acceptance.required,
    accepted: acceptance.accepted,
    category: acceptance.category,
  };
}

async function refreshLicenceQueries(client: ReturnType<typeof useQueryClient>, repoIds: string[]) {
  await Promise.all([
    client.invalidateQueries({ queryKey: ['model-catalogue'] }),
    ...repoIds.map((repoId) => client.invalidateQueries({ queryKey: modelLicenceDetailsKey(repoId) })),
  ]);
}

/**
 * Use-time licence acceptance. VoiceStudio is not the licensor and cannot grant
 * model access, so the user confirms, separately for every model, that they hold
 * the rights its licence needs. Each acceptance records the exact terms
 * fingerprint the user was shown, with a timestamp, on the backend.
 */
export function LicenceAcceptanceFooter({
  models,
  onAccepted,
  onClose,
  closeDisabled,
  children,
}: {
  children?: ReactNode;
  models: AcceptableModel[];
  onAccepted?: () => void;
  onClose: () => void;
  closeDisabled?: boolean;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const baseId = useId();
  const platform = platformFamily();
  const pending = models.filter((m) => m.required && !m.accepted);
  const accepted = models.filter((m) => m.required && m.accepted);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<'failed' | 'changed' | null>(null);
  const key = models.map((m) => `${m.repo_id}@${m.fingerprint}@${m.accepted}`).join('|');

  useEffect(() => {
    setChecked({});
    setError(null);
  }, [key]);

  const allChecked = pending.length > 0 && pending.every((m) => checked[m.repo_id] && m.termsLoaded !== false);

  const accept = async () => {
    if (!allChecked || busy) return;
    setBusy(true);
    setError(null);
    try {
      for (const model of pending) {
        await apiJson<ModelLicenceAcceptance>('/models/licenses/accept', {
          method: 'POST',
          body: JSON.stringify({ repo_id: model.repo_id, fingerprint: model.fingerprint, accepted: true }),
        });
      }
      onAccepted?.();
    } catch (err) {
      const changed =
        err instanceof ApiError && err.status === 409 && JSON.stringify(err.payload ?? '').includes('terms_changed');
      setError(changed ? 'changed' : 'failed');
    } finally {
      setBusy(false);
      await refreshLicenceQueries(client, models.map((m) => m.repo_id));
    }
  };

  const revoke = async (repoId: string) => {
    setBusy(true);
    setError(null);
    try {
      await apiJson('/models/licenses/revoke', { method: 'POST', body: JSON.stringify({ repo_id: repoId }) });
    } catch {
      setError('failed');
    } finally {
      setBusy(false);
      await refreshLicenceQueries(client, [repoId]);
    }
  };

  return (
    <>
    <div className="min-h-0 flex-1 overflow-y-auto">
    {children}
    <div
      data-licence-acceptance
      data-platform={platform}
      className="space-y-4 border-t border-border px-8 py-5"
    >
      {pending.length > 0 && (
        <>
          <p className="text-sm leading-relaxed text-foreground/85">{t('modelLicense.acceptNotice')}</p>
          <ul className="divide-y divide-border/70 overflow-hidden rounded-lg border border-border" aria-live="polite">
            {pending.map((model, index) => {
              const id = `${baseId}-${index}`;
              const checkedModel = !!checked[model.repo_id];
              return (
                <li key={model.repo_id}>
                  <label
                    htmlFor={id}
                    className={cn(
                      'flex cursor-pointer items-start gap-3 px-3 py-2.5 text-sm transition-colors motion-reduce:transition-none',
                      checkedModel ? 'bg-primary/[0.06]' : 'hover:bg-muted/50',
                    )}
                  >
                    <input
                      id={id}
                      type="checkbox"
                      checked={checkedModel}
                      disabled={busy || model.termsLoaded === false}
                      onChange={(event) => {
                        const value = event.currentTarget.checked;
                        setChecked((prev) => ({ ...prev, [model.repo_id]: value }));
                      }}
                      className={cn(
                        'mt-0.5 size-4 shrink-0 accent-primary focus-visible:ring-2 focus-visible:ring-ring',
                        platform === 'windows' && 'rounded-[4px]',
                      )}
                    />
                    <span className="min-w-0 flex-1 space-y-0.5">
                      <span aria-hidden="true" className="flex items-center gap-1.5 font-medium text-foreground">
                        <ModelLicenseIcon category={modelLicenseCategory(model.category)} className="size-3.5 shrink-0" />
                        <span data-model-label={model.label} className="break-all after:content-[attr(data-model-label)]" />
                      </span>
                      <span className="block leading-snug text-foreground/80">
                        {t('modelLicense.acceptCheckbox', { model: model.label })}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {t(error === 'changed' ? 'modelLicense.acceptChanged' : 'modelLicense.acceptFailed')}
        </p>
      )}
    </div>
    </div>
      <div data-licence-actions className={footerActionsClass(platform)}>
        {accepted.map((model) => (
          <Button
            key={model.repo_id}
            variant="outline"
            className="h-auto min-h-8 sm:me-auto"
            disabled={busy}
            onClick={() => void revoke(model.repo_id)}
          >
            <span className="whitespace-normal">
              {models.length > 1
                ? t('modelLicense.revokeFor', { model: model.label })
                : t('modelLicense.revoke')}
            </span>
          </Button>
        ))}
        <Button variant={platform === 'linux' ? 'ghost' : 'outline'} disabled={busy || closeDisabled} onClick={onClose}>
          {t('common.close')}
        </Button>
        {pending.length > 0 && (
          <Button className={platform === 'linux' ? 'min-w-40' : undefined} disabled={!allChecked || busy} onClick={() => void accept()}>
            {pending.length > 1 ? t('modelLicense.acceptAll', { count: pending.length }) : t('modelLicense.accept')}
          </Button>
        )}
      </div>
    </>
  );
}

/** App-level dialog for a `model_licence_required` error from any feature. */
export function ModelLicenceGate() {
  const { t } = useTranslation();
  const platform = platformFamily();
  const [models, setModels] = useState<ModelLicenceRequirement[] | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const onRequired = (event: Event) => {
      const detail = (event as CustomEvent<ModelLicenceRequirement[]>).detail;
      if (Array.isArray(detail) && detail.length) {
        setModels(detail);
        setDone(false);
      }
    };
    window.addEventListener(MODEL_LICENCE_REQUIRED_EVENT, onRequired);
    return () => window.removeEventListener(MODEL_LICENCE_REQUIRED_EVENT, onRequired);
  }, []);

  const queries = useQueries({
    queries: (models ?? []).map((model) => ({
      queryKey: modelLicenceDetailsKey(model.repo_id),
      queryFn: () =>
        apiJson<ModelLicenceDetails>(`/models/licenses/details/${encodeURI(model.repo_id)}`),
      staleTime: 0,
    })),
  });

  const close = () => {
    setModels(null);
    setDone(false);
  };

  // The live details carry the current fingerprint; the error payload is the
  // fallback until they load (or if they fail).
  const acceptable: AcceptableModel[] = (models ?? []).map((model, index) => {
    const acceptance = queries[index]?.data?.acceptance;
    return {
      ...toAcceptable(model.repo_id, {
        repo_id: model.repo_id,
        fingerprint: acceptance?.fingerprint ?? model.fingerprint,
        required: acceptance?.required ?? true,
        accepted: acceptance?.accepted ?? false,
        category: acceptance?.category ?? model.category,
      }),
      // Accept only terms the user could actually read.
      termsLoaded: queries[index]?.isSuccess === true,
    };
  });

  return (
    <Dialog open={!!models} onOpenChange={(open) => !open && close()}>
      <DialogContent
        showCloseButton={false}
        className={cn(
          // Tall and wide: the licence text is the content, so give it the room.
          'flex h-[min(48rem,calc(100dvh-2rem))] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl',
          platform === 'mac' ? 'rounded-xl' : 'rounded-lg',
          LICENCE_DIALOG_SURFACE,
        )}
        data-platform={platform}
      >
        <DialogHeader className={cn('px-8 pt-6 pb-4', platform === 'mac' && 'items-center text-center')}>
          <div className={cn('flex gap-3', platform === 'mac' ? 'flex-col items-center' : 'items-start')}>
            <ModelLicenseIcon category="conditions" className={platform === 'mac' ? 'size-8' : 'mt-1 size-6 shrink-0'} />
            <div className="min-w-0 space-y-1">
              <DialogTitle className={platform === 'windows' ? 'text-xl font-semibold' : 'text-base font-semibold'}>
                {t(done ? 'modelLicense.acceptedTitle' : 'modelLicense.requiredTitle')}
              </DialogTitle>
              <DialogDescription className="leading-snug">{t('modelLicense.gateIntro')}</DialogDescription>
            </div>
          </div>
        </DialogHeader>
        {models ? (
          <LicenceAcceptanceFooter models={acceptable} onAccepted={() => setDone(true)} onClose={close}>
        <div className="space-y-3 px-8 pb-4">
          {done && (
            <p role="status" className="rounded-lg border border-success/40 bg-success/10 p-3 text-sm">
              {t('modelLicense.retryHint')}
            </p>
          )}
          {(models ?? []).map((model, index) => {
            const query = queries[index];
            const view = (
              <ModelLicenceView
                details={query?.data}
                infoFallback={undefined}
              />
            );
            const body = query?.isError ? (
              <p className="text-sm text-destructive">{t('modelLicense.detailsFailed')}</p>
            ) : query?.isPending ? (
              <p role="status" className="text-sm">
                {t('common.loading')}
              </p>
            ) : (
              view
            );
            return models && models.length > 1 ? (
              <LicenceSection
                key={model.repo_id}
                title={`${index + 1}. ${model.repo_id}`}
                defaultOpen={index === 0}
              >
                {body}
              </LicenceSection>
            ) : (
              <div key={model.repo_id} className="space-y-2">
                <h3 className="break-all font-semibold">{model.repo_id}</h3>
                {body}
              </div>
            );
          })}
        </div>
          </LicenceAcceptanceFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
