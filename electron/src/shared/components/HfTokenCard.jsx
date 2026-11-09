import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';
import { openExternal } from '../api/external';
import { apiJson, apiPost } from '../api/client';
import { Button, Input } from '../ui';

/**
 * Compact one-line Hugging Face token card, pinned next to the wizard's
 * Continue action. A token buys authenticated downloads, higher rate limits
 * and access to gated models.
 *
 * The card asks the token resolver what is already configured before it
 * offers anything: with no token it shows the paste form, with an active one
 * it shows where the token comes from and keeps the form hidden. Overwriting
 * an active token takes an explicit click on "Replace…", and the form then
 * carries a warning saying so.
 */

const SOURCE_IDS = ['app', 'env', 'hf-cli'];
const SOURCE_LABELS = {
  app: ['settings.hf_source_app_label', 'VoiceStudio (encrypted, recommended)'],
  env: ['settings.hf_source_env_label', 'Environment variable'],
  'hf-cli': ['settings.hf_source_cli_label', 'HuggingFace CLI'],
};

// The resolver answers with exactly one row per source. Anything else means
// we cannot tell what is configured, so it counts as a failed read.
function readSourceRows(data) {
  const rows = data?.sources;
  if (!Array.isArray(rows) || rows.length !== SOURCE_IDS.length) return null;
  const seen = new Set();
  for (const row of rows) {
    if (!row || typeof row.set !== 'boolean' || !SOURCE_IDS.includes(row.source)) return null;
    seen.add(row.source);
  }
  return seen.size === SOURCE_IDS.length ? rows : null;
}

export default function HfTokenCard({ className = '' }) {
  const { t } = useTranslation();
  const [hfToken, setHfToken] = useState('');
  const [hfState, setHfState] = useState('idle'); // idle | saving | saved | error

  const [sources, setSources] = useState(null); // null until the resolver answers
  const [stateFailed, setStateFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // 'keep' shows an already-active token as is; 'overwrite' opens the form over it.
  const [intent, setIntent] = useState('keep');

  useEffect(() => {
    let live = true;
    apiJson('/system/hf-token/state')
      .then((data) => {
        if (!live) return;
        const rows = readSourceRows(data);
        if (rows) setSources(rows);
        else setStateFailed(true);
      })
      .catch(() => {
        if (live) setStateFailed(true);
      });
    return () => {
      live = false;
    };
  }, [attempt]);

  const retryStateCheck = () => {
    setStateFailed(false);
    setSources(null);
    setAttempt((n) => n + 1);
  };

  const activeRow = sources?.find((row) => row.set) ?? null;

  const overwriting = Boolean(activeRow) && intent === 'overwrite';

  const backOutOfOverwrite = () => {
    setIntent('keep');
    setHfToken('');
    setHfState('idle');
  };

  const saveHfToken = async () => {
    const value = hfToken.trim();
    if (!value || hfState === 'saving' || !sources || stateFailed) return;
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

  if (stateFailed) {
    return (
      <div
        className={cn(
          'flex flex-wrap items-center gap-2 rounded-md border border-transparent bg-danger/[0.08] px-3 py-2 text-sm',
          className,
        )}
      >
        <span role="alert" className="font-semibold text-danger">
          {t('common.error', 'Something went wrong')}
        </span>
        <Button variant="ghost" size="sm" onClick={retryStateCheck}>
          {t('bootstrap.retry', 'Retry')}
        </Button>
      </div>
    );
  }

  if (!sources) {
    return (
      <div
        className={cn(
          'flex flex-wrap items-center gap-2 rounded-md border border-transparent bg-primary/[0.07] px-3 py-2 text-sm',
          className,
        )}
      >
        <Zap size={16} className="shrink-0 text-primary" aria-hidden="true" />
        <span className="text-muted-foreground">{t('firstrun.checking', 'checking…')}</span>
      </div>
    );
  }

  if (activeRow && !overwriting) {
    const [labelKey, labelFallback] = SOURCE_LABELS[activeRow.source] ?? [];
    return (
      <div
        className={cn(
          'flex flex-wrap items-center gap-2 rounded-md border border-transparent bg-success/[0.09] px-3 py-2 text-sm',
          className,
        )}
      >
        <span className="inline-flex items-center gap-1.5 font-semibold text-success">
          <Check size={14} aria-hidden="true" />
          {t('firstrun.hf_token_active_using', {
            defaultValue: 'Hugging Face token found in {{source}} — {{masked}}',
            source: labelKey ? t(labelKey, labelFallback) : activeRow.source,
            masked: activeRow.masked || '',
          })}
        </span>
        <button
          type="button"
          className="cursor-pointer appearance-none whitespace-nowrap border-0 bg-transparent p-0 text-[0.76rem] text-primary underline hover:no-underline"
          onClick={() => setIntent('overwrite')}
        >
          {t('firstrun.hf_token_replace', 'Replace…')}
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
      {overwriting ? (
        <span className="font-semibold">
          {t(
            'firstrun.hf_token_replace_warning',
            'This replaces the token saved for this app. It does not revoke the old token.',
          )}
        </span>
      ) : (
        <span className="font-semibold max-[560px]:hidden">
          {t(
            'firstrun.hf_token_inline_prompt',
            'Speed up downloads with a free Hugging Face token',
          )}
        </span>
      )}
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
      {overwriting && (
        <Button
          variant="ghost"
          size="sm"
          disabled={hfState === 'saving'}
          onClick={backOutOfOverwrite}
        >
          {t('common.cancel', 'Cancel')}
        </Button>
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
