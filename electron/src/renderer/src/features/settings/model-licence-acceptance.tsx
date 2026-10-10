import { useEffect, useId, useState } from 'react';
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
import { ApiError, apiJson } from '@/lib/api/client';
import { cn } from '@/lib/utils';
import {
  MODEL_LICENCE_REQUIRED_EVENT,
  type ModelLicenceAcceptance,
  type ModelLicenceDetails,
  type ModelLicenceRequirement,
} from './model-license-contract';
import {
  LICENCE_DIALOG_SURFACE,
  LicenceSection,
  ModelLicenceView,
  modelLicenceDetailsKey,
} from './model-licence-view';

/** A model the footer can accept: what the user is shown is what gets recorded. */
export interface AcceptableModel {
  repo_id: string;
  label: string;
  fingerprint: string;
  required: boolean;
  accepted: boolean;
}

export function toAcceptable(
  label: string,
  acceptance: Pick<ModelLicenceAcceptance, 'repo_id' | 'fingerprint' | 'required' | 'accepted'>,
): AcceptableModel {
  return {
    repo_id: acceptance.repo_id,
    label,
    fingerprint: acceptance.fingerprint,
    required: acceptance.required,
    accepted: acceptance.accepted,
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
}: {
  models: AcceptableModel[];
  onAccepted?: () => void;
  onClose: () => void;
  closeDisabled?: boolean;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const baseId = useId();
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

  const allChecked = pending.length > 0 && pending.every((m) => checked[m.repo_id]);

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
    <div className="max-h-[45dvh] shrink-0 space-y-3 overflow-y-auto border-t border-border bg-popover p-4">
      {pending.length > 0 && (
        <>
          <p className="max-w-prose text-sm leading-relaxed">{t('modelLicense.acceptNotice')}</p>
          <ul className="space-y-2">
            {pending.map((model, index) => {
              const id = `${baseId}-${index}`;
              return (
                <li key={model.repo_id}>
                  <label
                    htmlFor={id}
                    className={cn(
                      'flex items-start gap-2 rounded-lg border p-3 text-sm',
                      checked[model.repo_id] ? 'border-primary/60 bg-primary/5' : 'border-border',
                    )}
                  >
                    <input
                      id={id}
                      type="checkbox"
                      checked={!!checked[model.repo_id]}
                      disabled={busy}
                      onChange={(event) => {
                        const value = event.currentTarget.checked;
                        setChecked((prev) => ({ ...prev, [model.repo_id]: value }));
                      }}
                      className="mt-0.5 size-4 shrink-0 accent-primary focus-visible:ring-2 focus-visible:ring-ring"
                    />
                    <span>{t('modelLicense.acceptCheckbox', { model: model.label })}</span>
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
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
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
        <Button variant="ghost" disabled={busy || closeDisabled} onClick={onClose}>
          {t('common.close')}
        </Button>
        {pending.length > 0 && (
          <Button disabled={!allChecked || busy} onClick={() => void accept()}>
            {pending.length > 1 ? t('modelLicense.acceptAll', { count: pending.length }) : t('modelLicense.accept')}
          </Button>
        )}
      </div>
    </div>
  );
}

/** App-level dialog for a `model_licence_required` error from any feature. */
export function ModelLicenceGate() {
  const { t } = useTranslation();
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
    return toAcceptable(model.repo_id, {
      repo_id: model.repo_id,
      fingerprint: acceptance?.fingerprint ?? model.fingerprint,
      required: acceptance?.required ?? true,
      accepted: acceptance?.accepted ?? false,
    });
  });

  return (
    <Dialog open={!!models} onOpenChange={(open) => !open && close()}>
      <DialogContent
        showCloseButton={false}
        className={cn(
          'flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl',
          LICENCE_DIALOG_SURFACE,
        )}
      >
        <DialogHeader className="border-b border-border p-4">
          <DialogTitle>{t('modelLicense.requiredTitle')}</DialogTitle>
          <DialogDescription>{t('modelLicense.gateIntro')}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
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
        {models && (
          <LicenceAcceptanceFooter models={acceptable} onAccepted={() => setDone(true)} onClose={close} />
        )}
      </DialogContent>
    </Dialog>
  );
}
