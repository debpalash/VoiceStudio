import { AudioWaveformIcon, GitCompareArrowsIcon, WrenchIcon } from 'lucide-react';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { ConvertVoice } from './convert-voice';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { CompareVoices } from './compare-voices';
import { useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { SparklesIcon, TargetIcon, ActivityIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiJson, describeError } from '@/lib/api/client';
import { LanguagePicker } from '@/features/clone/language-picker';
import { LANG_CODES } from '@shared/utils/languages';
const options = LANG_CODES.map((item) => item.label);
const tools = [
  {
    id: 'direction',
    label: 'directorial_ai',
    description: 'directorial_desc',
    action: 'parse',
    icon: SparklesIcon,
  },
  {
    id: 'rate-fit',
    label: 'speech_rate_fit',
    description: 'speech_rate_desc',
    action: 'fit',
    icon: TargetIcon,
  },
  {
    id: 'probe',
    label: 'probe_file',
    description: 'probe_desc',
    action: 'probe',
    icon: ActivityIcon,
  },
] as const;
type Tool = (typeof tools)[number];
export function ToolsPage() {
  const { t } = useTranslation();
  const [comparing, setComparing] = useState(false);
  const [converting, setConverting] = useState(false);
  const [selected, setSelected] = useState<Tool>(tools[0]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('tools.title')}</h1>
      </WorkspaceHeader>
      <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t('tools.title')}
          icon={WrenchIcon}
          variant="navigation"
          className="space-y-1"
        >
          {tools.map((tool) => (
            <Button
              key={tool.id}
              className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
              variant={!converting && !comparing && selected.id === tool.id ? 'secondary' : 'ghost'}
              aria-pressed={!converting && !comparing && selected.id === tool.id}
              onClick={() => {
                setConverting(false);
                setComparing(false);
                setSelected(tool);
              }}
            >
              <tool.icon />
              {t('tools.' + tool.label)}
            </Button>
          ))}
          <Button
            className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
            variant={!converting && comparing ? 'secondary' : 'ghost'}
            aria-pressed={!converting && comparing}
            onClick={() => {
              setConverting(false);
              setComparing(true);
            }}
          >
            <GitCompareArrowsIcon />
            {t('compare.title')}
          </Button>
          <Button
            className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
            variant={converting ? 'secondary' : 'ghost'}
            aria-pressed={converting}
            onClick={() => setConverting(true)}
          >
            <AudioWaveformIcon />
            {t('convert.convert')}
          </Button>
        </SecondarySidebar>
        <section className="min-w-0 flex-1 overflow-y-auto px-6 py-8">
          {converting ? (
            <ConvertVoice />
          ) : comparing ? (
            <CompareVoices />
          ) : (
            <ToolForm key={selected.id} tool={selected} />
          )}
        </section>
      </div>
    </div>
  );
}
function ToolForm({ tool }: { tool: Tool }) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [slot, setSlot] = useState('2');
  const [language, setLanguage] = useState('English');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const valid =
    Boolean(text.trim()) &&
    (tool.id !== 'rate-fit' || (Number.isFinite(Number(slot)) && Number(slot) > 0));
  const run = async () => {
    if (request.current || !valid) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body =
        tool.id === 'probe'
          ? { path: text.trim() }
          : tool.id === 'direction'
            ? { text }
            : {
                text,
                slot_seconds: Number(slot),
                target_lang: LANG_CODES.find((item) => item.label === language)?.code,
              };
      const data = await apiJson<Record<string, unknown>>('/tools/' + tool.id, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!controller.signal.aborted) setResult(data);
    } catch (error) {
      if (!controller.signal.aborted) setError(describeError(error));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      request.current = null;
    }
  };
  return (
    <form
      className="mx-auto max-w-3xl space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        void run();
      }}
    >
      <div>
        <h2 className="text-lg font-medium">{t('tools.' + tool.label)}</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {t('tools.' + tool.description)}
        </p>
      </div>
      <label className="block space-y-2 text-sm">
        <span>
          {t(
            tool.id === 'probe'
              ? 'tools.absolute_path'
              : tool.id === 'direction'
                ? 'tools.direction'
                : 'tools.translated_line',
          )}
        </span>
        {tool.id === 'probe' ? (
          <Input
            disabled={busy}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setResult(null);
            }}
          />
        ) : (
          <textarea
            className="min-h-36 w-full resize-y rounded-lg border border-input bg-transparent p-3 leading-7 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            disabled={busy}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setResult(null);
            }}
          />
        )}
      </label>
      {tool.id === 'rate-fit' && (
        <div className="flex flex-wrap items-end gap-4">
          <label className="space-y-2 text-sm">
            <span>{t('tools.slot_seconds')}</span>
            <Input
              type="number"
              min="0.1"
              step="0.1"
              value={slot}
              disabled={busy}
              onChange={(event) => {
                setSlot(event.target.value);
                setResult(null);
              }}
            />
          </label>
          <div className="space-y-2 text-sm">
            <p>{t('tools.target_language')}</p>
            <LanguagePicker
              value={language}
              options={options}
              disabled={busy}
              onValueChange={(value) => {
                setLanguage(value);
                setResult(null);
              }}
            />
          </div>
        </div>
      )}
      <Button type="submit" disabled={busy || !valid}>
        {t(busy ? 'common.loading' : 'tools.' + tool.action)}
      </Button>
      {error && <PipelineFailure fallback={error} onDismiss={() => setError(null)} />}
      {tool.id === 'probe' && (
        <Link to="/settings/media" className="block text-sm text-primary hover:underline">
          {t('settings.audio_tools')}
        </Link>
      )}
      {result && (
        <div
          role="region"
          aria-label={t('tools.result')}
          className="space-y-4 rounded-xl border border-border/50 p-4"
        >
          {Boolean(result.error) && <PipelineFailure fallback={describeError(result.error)} />}
          {tool.id === 'probe' ? (
            <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs">
              {JSON.stringify(result, null, 2)}
            </pre>
          ) : (
            <>
              <dl className="space-y-3 text-sm">
                {(tool.id === 'direction'
                  ? [
                      ['method', 'method'],
                      ['instruct_prompt', 'tts_instruct'],
                      ['translate_hint', 'translate_hint'],
                      ['rate_bias', 'rate_bias'],
                    ]
                  : [
                      ['method', 'method'],
                      ['ratio', 'ratio'],
                      ['attempts', 'attempts'],
                      ['text', 'result'],
                    ]
                ).map(([key, label]) => (
                  <div key={key}>
                    <dt className="text-xs text-muted-foreground">{t('tools.' + label)}</dt>
                    <dd className="mt-1 whitespace-pre-wrap">
                      {result[key] == null ? '?' : String(result[key])}
                    </dd>
                  </div>
                ))}
              </dl>
              {tool.id === 'direction' &&
                ['tokens', 'taxonomy'].map((key) => (
                  <details key={key} className="text-xs">
                    <summary className="cursor-pointer">{t('tools.' + key)}</summary>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words">
                      {JSON.stringify(result[key], null, 2)}
                    </pre>
                  </details>
                ))}
            </>
          )}
        </div>
      )}
    </form>
  );
}
