import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Wrench, Sparkles, Activity, Target } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Panel, Button, Input, Field, Textarea, Badge } from '../ui';
import { apiPost } from '../api/client';

/**
 * ToolsPage — Phase 4.6 surface. Standalone utilities independent of the
 * dub pipeline. Today: directorial-AI parser, speech-rate fitter, probe.
 */
export default function ToolsPage({ onBack }) {
  const { t } = useTranslation();
  return (
    <div className="tools-page flex flex-1 flex-col gap-[var(--space-5)] min-h-0 overflow-y-auto px-[var(--space-6)] py-[var(--space-5)]">
      <div className="tools-page__bar flex shrink-0 items-center gap-[var(--space-4)]">
        {onBack && (
          <Button variant="ghost" size="sm" onClick={onBack}>
            {t('common.back')}
          </Button>
        )}
        <h1>
          <Wrench size={15} /> {t('tools.title')}
        </h1>
      </div>

      <div className="tools-page__grid grid grid-cols-[repeat(auto-fit,minmax(380px,1fr))] gap-[var(--space-5)]">
        <DirectorTool />
        <RateFitTool />
        <ProbeTool />
      </div>
    </div>
  );
}

function DirectorTool() {
  const { t } = useTranslation();
  const [text, setText] = useState('make this feel urgent and surprised');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      setResult(await apiPost('/tools/direction', { text }));
    } catch (e) {
      toast.error(t('tools.parse_failed', { message: e.message }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      variant="flat"
      padding="md"
      className="tools-card"
      title={
        <>
          <Sparkles size={13} /> {t('tools.directorial_ai')}
        </>
      }
    >
      <p className="tools-card__desc m-0 mb-[var(--space-4)] text-[var(--text-base)] leading-[1.5] text-fg-muted">
        {t('tools.directorial_desc')}
      </p>
      <Field label={t('tools.direction')}>
        <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <Button variant="primary" size="sm" loading={busy} onClick={run}>
        {t('tools.parse')}
      </Button>
      {result && (
        <div className="tools-card__out mt-[var(--space-4)] rounded-sm [border:1px_solid_var(--color-border)] bg-bg-elev-2 p-[var(--space-4)] text-[var(--text-sm)]">
          <div className="tools-card__out-row mb-[var(--space-2)]">
            <strong>{t('tools.method')}</strong>{' '}
            <Badge tone={result.method === 'llm' ? 'violet' : 'neutral'}>{result.method}</Badge>
            {result.error && (
              <Badge tone="warn" size="xs">
                {result.error}
              </Badge>
            )}
          </div>
          <div className="tools-card__out-row mb-[var(--space-2)]">
            <strong>{t('tools.tts_instruct')}</strong> <code>{result.instruct_prompt || '—'}</code>
          </div>
          <div className="tools-card__out-row mb-[var(--space-2)]">
            <strong>{t('tools.translate_hint')}</strong> <em>{result.translate_hint || '—'}</em>
          </div>
          <div className="tools-card__out-row mb-[var(--space-2)]">
            <strong>{t('tools.rate_bias')}</strong> <code>{result.rate_bias?.toFixed?.(2)}</code>
          </div>
          <details>
            <summary>{t('tools.tokens')}</summary>
            <pre>{JSON.stringify(result.tokens, null, 2)}</pre>
          </details>
          <details>
            <summary>{t('tools.taxonomy')}</summary>
            <pre>{JSON.stringify(result.taxonomy, null, 2)}</pre>
          </details>
        </div>
      )}
    </Panel>
  );
}

function RateFitTool() {
  const { t } = useTranslation();
  const [text, setText] = useState('Tatsächlich das Spiel zu verändern.');
  const [slot, setSlot] = useState('2.0');
  const [lang, setLang] = useState('de');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      setResult(
        await apiPost('/tools/rate-fit', {
          text,
          slot_seconds: Number(slot),
          target_lang: lang,
        }),
      );
    } catch (e) {
      toast.error(t('tools.fit_failed', { message: e.message }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      variant="flat"
      padding="md"
      className="tools-card"
      title={
        <>
          <Target size={13} /> {t('tools.speech_rate_fit')}
        </>
      }
    >
      <p className="tools-card__desc m-0 mb-[var(--space-4)] text-[var(--text-base)] leading-[1.5] text-fg-muted">
        {t('tools.speech_rate_desc')}
      </p>
      <Field label={t('tools.translated_line')}>
        <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <div className="tools-card__row mb-[var(--space-4)] grid grid-cols-[1fr_1fr] gap-[var(--space-4)]">
        <Field label={t('tools.slot_seconds')}>
          <Input size="sm" value={slot} onChange={(e) => setSlot(e.target.value)} />
        </Field>
        <Field label={t('tools.target_language')}>
          <Input size="sm" value={lang} onChange={(e) => setLang(e.target.value)} />
        </Field>
      </div>
      <Button variant="primary" size="sm" loading={busy} onClick={run}>
        {t('tools.fit')}
      </Button>
      {result && (
        <div className="tools-card__out mt-[var(--space-4)] rounded-sm [border:1px_solid_var(--color-border)] bg-bg-elev-2 p-[var(--space-4)] text-[var(--text-sm)]">
          <div className="tools-card__out-row mb-[var(--space-2)]">
            <strong>{t('tools.ratio')}</strong> <code>{result.rate_ratio?.toFixed?.(2)}</code>{' '}
            {result.error && (
              <Badge tone="warn" size="xs">
                {result.error}
              </Badge>
            )}
          </div>
          <div className="tools-card__out-row mb-[var(--space-2)]">
            <strong>{t('tools.attempts')}</strong> <code>{result.attempts}</code>
          </div>
          <div className="tools-card__out-row mb-[var(--space-2)]">
            <strong>{t('tools.result')}</strong>
          </div>
          <div className="tools-card__result mt-[var(--space-2)] rounded-sm [border:1px_solid_rgba(211,134,155,0.25)] bg-[rgba(211,134,155,0.08)] p-[var(--space-3)] text-[var(--text-md)] text-fg">
            {result.text}
          </div>
        </div>
      )}
    </Panel>
  );
}

function ProbeTool() {
  const { t } = useTranslation();
  const [path, setPath] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      setResult(await apiPost('/tools/probe', { path }));
    } catch (e) {
      toast.error(t('tools.probe_failed', { message: e.message }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      variant="flat"
      padding="md"
      className="tools-card"
      title={
        <>
          <Activity size={13} /> {t('tools.probe_file')}
        </>
      }
    >
      <p className="tools-card__desc m-0 mb-[var(--space-4)] text-[var(--text-base)] leading-[1.5] text-fg-muted">
        {t('tools.probe_desc')}
      </p>
      <Field label={t('tools.absolute_path')}>
        <Input
          size="sm"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder={t('tools.probe_placeholder')}
        />
      </Field>
      <Button variant="primary" size="sm" loading={busy} onClick={run} disabled={!path.trim()}>
        {t('tools.probe')}
      </Button>
      {result && (
        <details
          open
          className="tools-card__out mt-[var(--space-4)] rounded-sm [border:1px_solid_var(--color-border)] bg-bg-elev-2 p-[var(--space-4)] text-[var(--text-sm)]"
        >
          <summary>{t('tools.result')}</summary>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </details>
      )}
    </Panel>
  );
}
