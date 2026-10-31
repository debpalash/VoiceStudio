import { FolderOpenIcon, LoaderCircleIcon, RotateCcwIcon, SearchIcon } from 'lucide-react';
import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { getBridge } from '@/components/bridge';
import { Button } from '@/components/ui/button';
import { ApiError, apiJson } from '@/lib/api/client';

const SUPPORTED = new Set(['omnivoice', 'mlx-audio']);
type Props = { family: 'tts' | 'asr'; engineId: string };
type Validation = { status: string; code: string; message?: string };
type Candidate = { name: string; path: string; model_name?: string | null; model_type?: string | null; checkpoint_type?: string | null; validation: Validation; authorization?: string };
type State = { path: string | null; model_name?: string | null; model_type?: string | null; checkpoint_type?: string | null; validation?: Validation; loading?: { status: string }; environment_override?: string | null; effective_override?: string | null };
type Discovery = { candidates: Candidate[]; limited?: boolean; partial?: boolean };
const validationKeys: Record<string, string> = {
  complete: 'modelSettings.localModelValidationComplete',
  incompatible_architecture: 'modelSettings.localModelValidationIncompatible',
  invalid_metadata: 'modelSettings.localModelValidationMetadata',
  missing_assets: 'modelSettings.localModelValidationAssets',
  invalid_files: 'modelSettings.localModelValidationInvalid',
};
const loadingKeys: Record<string, string> = {
  loaded: 'modelSettings.localModelLoadingLoaded',
  not_loaded: 'modelSettings.localModelLoadingNotLoaded',
  loading: 'modelSettings.localModelLoadingLoading',
  failed: 'modelSettings.localModelLoadingFailed',
};

/** Engine-keyed state prevents results/grants from surviving an engine switch. */
export function LocalModelPath(props: Props) {
  return <LocalModelPathControl key={`${props.family}/${props.engineId}`} {...props} />;
}

function LocalModelPathControl({ family, engineId }: Props) {
  const { t } = useTranslation();
  const bridge = getBridge();
  const client = useQueryClient();
  const [busy, setBusy] = useState<'search' | 'save' | 'reset' | null>(null);
  const locked = useRef(false);
  const [error, setError] = useState('');
  const [errorDetail, setErrorDetail] = useState('');
  const [notice, setNotice] = useState('');
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const supported = family === 'tts' && SUPPORTED.has(engineId);
  const endpoint = `/api/settings/engines/${family}/${engineId}/local-model`;
  const queryKey = ['local-model-path', family, engineId];
  const path = useQuery({
    queryKey,
    enabled: supported && Boolean(bridge?.files.chooseEngineModelDirectory),
    queryFn: ({ signal }) => apiJson<State>(endpoint, { signal }),
  });
  if (!supported || !bridge?.files.chooseEngineModelDirectory) return null;

  const run = async (operation: 'search' | 'save' | 'reset', action: () => Promise<void>) => {
    if (locked.current) return;
    locked.current = true;
    setBusy(operation); setError(''); setErrorDetail(''); setNotice('');
    try { await action(); }
    catch (cause) {
      setError(cause instanceof ApiError && cause.status === 409 ? 'modelSettings.localModelBusy' : 'modelSettings.localModelError');
      // Keep concrete missing files and format diagnostics; a generic translated
      // heading alone is not actionable. React escapes this technical detail.
      if (cause instanceof ApiError) setErrorDetail(cause.detail);
    } finally { locked.current = false; setBusy(null); }
  };
  const refresh = () => client.invalidateQueries({ queryKey });
  const save = async (authorization: string) => {
    // Tokens are minted by trusted native selection or backend discovery, never
    // from a renderer path. PUT consumes the exact grant and revalidates files.
    setDiscovery(null);
    await apiJson(endpoint, { method: 'PUT', body: JSON.stringify({ authorization }) });
    await refresh();
    setNotice('modelSettings.localModelSaved');
  };
  const choose = () => run('save', async () => {
    const selected = await bridge.files.chooseEngineModelDirectory!(engineId as 'omnivoice' | 'mlx-audio');
    if (!selected) { setNotice('modelSettings.localModelCancelled'); return; }
    await save(selected.authorization);
  });
  const discover = () => run('search', async () => {
    const selected = await bridge.files.chooseEngineModelDirectory!(engineId as 'omnivoice' | 'mlx-audio');
    if (!selected) { setNotice('modelSettings.localModelCancelled'); return; }
    setDiscovery(null);
    setDiscovery(await apiJson<Discovery>(`${endpoint}/discover`, {
      method: 'POST', body: JSON.stringify({ authorization: selected.authorization }),
    }));
  });
  const clear = () => run('reset', async () => {
    await apiJson(endpoint, { method: 'DELETE' });
    setDiscovery(null);
    await refresh();
    setNotice('modelSettings.localModelResetDone');
  });
  const validationText = (validation: Validation) => t(validationKeys[validation.code] ?? 'modelSettings.localModelValidationUnknown');
  return (
    <div aria-busy={Boolean(busy)} className="mt-2 flex min-w-0 flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground">{t('modelSettings.localModelLabel')}</span>
      {!path.isError && <code data-model-path={path.data?.path ?? ''} className="min-w-0 max-w-full whitespace-normal break-all" title={path.data?.path ?? undefined}>
        {path.isPending ? t('common.loading') : path.data?.path ?? t('modelSettings.localModelDefault')}
      </code>}
      {path.data?.model_name && <span className="break-all" data-model-name={path.data.model_name}>{path.data.model_name}</span>}
      {path.data?.model_type && <span className="break-all" data-model-type={path.data.model_type}>{path.data.model_type}{path.data.checkpoint_type ? ` / ${path.data.checkpoint_type}` : ''}</span>}
      <Button data-testid="local-model-choose" size="xs" variant="outline" disabled={Boolean(busy)} onClick={() => void choose()}>
        <FolderOpenIcon />{t('modelSettings.localModelChoose')}
      </Button>
      <Button data-testid="local-model-discover" size="xs" variant="outline" disabled={Boolean(busy)} onClick={() => void discover()}>
        <SearchIcon />{t('modelSettings.localModelSearch')}
      </Button>
      {path.data?.path && <Button size="xs" variant="ghost" disabled={Boolean(busy)} onClick={() => void clear()}>
        <RotateCcwIcon />{t('modelSettings.localModelReset')}
      </Button>}
      <span className="basis-full text-muted-foreground">{t('modelSettings.localModelHint')}</span>
      {path.data?.validation && path.data.validation.status !== 'unconfigured' && <span data-testid="local-model-validation" role={path.data.validation.status === 'invalid' ? 'alert' : 'status'} className="basis-full text-muted-foreground">
        {validationText(path.data.validation)}
      </span>}
      {path.data?.loading && <span role="status" data-testid="local-model-loading" className="basis-full text-muted-foreground">{t(loadingKeys[path.data.loading.status] ?? 'modelSettings.localModelLoadingUnknown')}</span>}
      {busy && <span role="status" className="flex basis-full items-center gap-2"><LoaderCircleIcon aria-hidden="true" className="size-3.5 animate-spin" />{t(busy === 'search' ? 'modelSettings.localModelSearching' : busy === 'save' ? 'modelSettings.localModelSaving' : 'modelSettings.localModelResetting')}</span>}
      {notice && <span role="status" className="basis-full">{t(notice)}</span>}
      {discovery && <>
        {(discovery.limited || discovery.partial) && <span role="status" className="basis-full">{t('modelSettings.localModelPartial')}</span>}
        {discovery.candidates.length === 0 && <span role="status" className="basis-full">{t('modelSettings.localModelEmpty')}</span>}
        {discovery.candidates.length > 0 && <div data-testid="local-model-candidates" className="max-h-64 min-w-0 basis-full space-y-2 overflow-y-auto overscroll-contain">
          {discovery.candidates.map((candidate) => <div key={candidate.path} data-testid="local-model-candidate" data-model-path={candidate.path} className="flex min-w-0 flex-wrap items-center gap-2 rounded border p-2">
            <code className="min-w-0 basis-full whitespace-normal break-all">{candidate.path}</code>
            <span className="break-all">{candidate.model_name ?? candidate.model_type ?? candidate.name}</span>
            <span role={candidate.validation.status === 'invalid' ? 'alert' : 'status'}>{validationText(candidate.validation)}</span>
            <Button size="xs" variant="outline" disabled={Boolean(busy) || candidate.validation.status !== 'valid' || !candidate.authorization} onClick={() => {
              if (candidate.validation.status === 'valid' && candidate.authorization) void run('save', () => save(candidate.authorization!));
            }}>{t('modelSettings.localModelUse')}</Button>
          </div>)}
        </div>}
      </>}
      {path.data?.environment_override && <span role="status" className="basis-full text-amber-600">
        {t('modelSettings.localModelEnvironmentOverride')}{' '}
        <code className="whitespace-normal break-all">{path.data.environment_override} = {path.data.effective_override}</code>
      </span>}
      {path.isError && <span role="alert" className="basis-full text-destructive">{t('modelSettings.localModelReadError')}</span>}
      {error && <span role="alert" className="basis-full break-words text-destructive">{t(error)}{errorDetail && <code className="mt-1 block whitespace-normal break-all">{errorDetail}</code>}</span>}
    </div>
  );
}
