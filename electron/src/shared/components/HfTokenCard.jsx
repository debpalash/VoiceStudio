import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';
import { openExternal } from '../api/external';
import { apiJson, apiPost } from '../api/client';
import { Button, Input } from '../ui';

/**
 * HfTokenCard — a compact, single-line Hugging Face token input that lives in
 * the wizard's pinned action area, right by the "Waiting for required models…"
 * / Continue button. A free token gives authenticated downloads (faster,
 * higher rate limits, fewer stalls) and unlocks gated models (pyannote
 * diarization). Persisted via the same encrypted-app-token endpoint Settings uses, so
 * it survives restarts.
 *
 * Before pitching a token, it checks the resolver state (same endpoint the
 * Settings → API Keys panel uses, `ApiKeysPanel.jsx`) so a user who already
 * has a locally configured token — from the app store, an env var, or `huggingface-cli
 * login` — sees that instead of a blind "add a token" prompt (#FR-006).
 * Replacing an already-active token is gated behind an explicit "Replace…"
 * click rather than being one blind paste-and-Save away, since Save persists
 * in the app store and the selected local Hub token file.
 *
 * @param {string=} className extra class on the root (e.g. layout pinning).
 */
export default function HfTokenCard({ className = '' }) {
  const { t } = useTranslation();
  const [hfToken, setHfToken] = useState('');
  const [hfState, setHfState] = useState('idle'); // idle | saving | saved | error

  // Resolver state (mirrors ApiKeysPanel's `state`/`refresh` pair): null while
  // the first GET is in flight, 'error' when it fails — either way we never
  // want to flash a false "you have no token" pitch before we actually know.
  const [tokenState, setTokenState] = useState(null);
  const [checkFailed, setCheckFailed] = useState(false);
  const [checkAttempt, setCheckAttempt] = useState(0);
  // Explicit gate: revealing the paste-a-token form when a token is already
  // active requires this deliberate click, so Save can never blind-clobber a
  // working token.
  const [replacing, setReplacing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson('/system/hf-token/state');
        if (
          !Array.isArray(data?.sources) ||
          data.sources.length !== 3 ||
          !['app', 'env', 'hf-cli'].every((source) =>
            data.sources.some((row) => row?.source === source && typeof row.set === 'boolean'),
          )
        )
          throw new Error('Invalid token state');
        if (!cancelled) setTokenState(data);
      } catch {
        if (!cancelled) setCheckFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [checkAttempt]);

  const saveHfToken = async () => {
    const value = hfToken.trim();
    if (!value || hfState === 'saving' || tokenState == null || checkFailed) return;
    setHfState('saving');
    try {
      await apiPost('/api/settings/hf-token', { token: value });
      setHfState('saved');
      setHfToken('');
    } catch {
      setHfState('error');
    }
  };

  if (hfState === 'saved') {
    return (
      <div
        className={cn(
          'flex flex-wrap items-center gap-2 rounded-md border border-transparent bg-success/[0.09] px-3 py-2 text-sm',
          className,
        )}
      >
        <span className="inline-flex items-center gap-1.5 font-semibold text-success">
          <Check size={14} aria-hidden="true" />
          {t('firstrun.hf_token_saved', 'Hugging Face token saved')}
        </span>
      </div>
    );
  }

  if (checkFailed) {
    return (
      <div
        className={cn(
          'flex flex-wrap items-center gap-2 rounded-md bg-danger/10 px-3 py-2 text-sm',
          className,
        )}
      >
        <span role="alert" className="text-danger">
          {t('common.error', 'Something went wrong')}
        </span>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setCheckFailed(false);
            setTokenState(null);
            setCheckAttempt((attempt) => attempt + 1);
          }}
        >
          {t('bootstrap.retry', 'Retry')}
        </Button>
      </div>
    );
  }

  // Still checking — never flash the "add a token" pitch before we know
  // whether one is already active.
  if (tokenState == null) {
    return (
      <div
        className={cn(
          'flex items-center gap-2 rounded-md border border-transparent bg-primary/[0.07] px-3 py-2 text-sm text-fg-muted',
          className,
        )}
      >
        <Zap size={16} className="shrink-0 text-primary" aria-hidden="true" />
        {t('firstrun.checking', 'checking…')}
      </div>
    );
  }

  const SOURCE_LABELS = {
    app: t('settings.hf_source_app_label', {
      defaultValue: 'VoiceStudio (encrypted, recommended)',
    }),
    env: t('settings.hf_source_env_label', {
      defaultValue: 'Environment variable',
    }),
    'hf-cli': t('settings.hf_source_cli_label', {
      defaultValue: 'HuggingFace CLI',
    }),
  };
  const activeRow = tokenState?.sources?.find((row) => row.set);

  // A token is already configured locally and the user hasn't asked to replace
  // it — show the satisfied state, not the pitch (#FR-006).
  if (activeRow && !replacing) {
    return (
      <div
        className={cn(
          'flex flex-wrap items-center gap-2 rounded-md border border-transparent bg-success/[0.09] px-3 py-2 text-sm',
          className,
        )}
      >
        <Check size={16} className="shrink-0 text-success" aria-hidden="true" />
        <span className="font-semibold text-success">
          {t('firstrun.hf_token_active_using', {
            source: SOURCE_LABELS[activeRow.source] || activeRow.source,
            masked: activeRow.masked || '',
            defaultValue: 'Hugging Face token found in {{source}} — {{masked}}',
          })}
        </span>
        <button
          type="button"
          className="cursor-pointer appearance-none whitespace-nowrap border-0 bg-transparent p-0 text-[0.76rem] text-primary underline hover:no-underline"
          onClick={() => setReplacing(true)}
        >
          {t('firstrun.hf_token_replace', 'Replace token…')}
        </button>
      </div>
    );
  }

  // A successful state read found no token, or replacement was explicit.
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-2 rounded-md border border-transparent bg-primary/[0.07] px-3 py-2 text-sm',
        className,
      )}
    >
      <Zap size={16} className="shrink-0 text-primary" aria-hidden="true" />
      <span className={cn('font-semibold', !(replacing && activeRow) && 'max-[560px]:hidden')}>
        {replacing && activeRow
          ? t(
              'firstrun.hf_token_replace_warning',
              'This replaces the token saved for this app. It does not revoke the old token.',
            )
          : t(
              'firstrun.hf_token_inline_prompt',
              'Speed up downloads with a free Hugging Face token',
            )}
      </span>
      <Input
        size="sm"
        className="min-w-[130px] flex-1 basis-[180px]"
        type="password"
        placeholder={t('firstrun.hf_token_inline_ph', 'Paste hf_… token (optional)')}
        value={hfToken}
        autoComplete="off"
        disabled={hfState === 'saving'}
        onChange={(e) => {
          if (hfState === 'saving') return;
          setHfToken(e.target.value);
          if (hfState !== 'idle') setHfState('idle');
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') saveHfToken();
        }}
        aria-label={t(
          'firstrun.hf_token_card_title',
          'Add a free Hugging Face token for faster downloads',
        )}
      />
      <Button
        variant="primary"
        size="sm"
        disabled={!hfToken.trim() || hfState === 'saving'}
        onClick={saveHfToken}
      >
        {hfState === 'saving'
          ? t('firstrun.hf_token_saving', 'saving…')
          : t('firstrun.hf_token_save', 'Save')}
      </Button>
      {replacing && activeRow && (
        <button
          type="button"
          className="cursor-pointer appearance-none whitespace-nowrap border-0 bg-transparent p-0 text-[0.76rem] text-fg-muted underline hover:no-underline"
          disabled={hfState === 'saving'}
          onClick={() => {
            setReplacing(false);
            setHfToken('');
            setHfState('idle');
          }}
        >
          {t('common.cancel', 'Cancel')}
        </button>
      )}
      <button
        type="button"
        className="cursor-pointer appearance-none whitespace-nowrap border-0 bg-transparent p-0 text-[0.76rem] text-primary underline hover:no-underline"
        onClick={() => openExternal('https://huggingface.co/settings/tokens')}
      >
        {t('firstrun.hf_token_get_short', 'Get one free →')}
      </button>
      {hfState === 'error' && (
        <span className="basis-full text-[0.76rem] text-danger">
          {t(
            'firstrun.hf_token_error',
            'Could not save the token — try again or set it later in Settings → Credentials.',
          )}
        </span>
      )}
    </div>
  );
}
