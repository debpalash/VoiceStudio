import { useTranslation } from 'react-i18next';
import { Link } from '@tanstack/react-router';
import {
  GaugeIcon,
  LanguagesIcon,
  LoaderCircleIcon,
  RotateCcwIcon,
  Settings2Icon,
  WandSparklesIcon,
  XIcon,
} from 'lucide-react';
import { PipelineFailure } from '@/components/pipeline-failure';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { LanguagePicker } from '@/features/clone/language-picker';
import type { TranslationEngine } from '@/features/settings/translation-settings';
import { describeError } from '@/lib/api/client';
import {
  LANGUAGE_VARIANTS,
  hasVariantChoices,
  languageTagName,
  variantChoice,
  withVariantChoices,
} from '@/lib/language-variants';
import { TRANSLATION_TARGET_LABELS, translationTargetCode } from '@/lib/translation-targets';
import { cn } from '@/lib/utils';
import { dialectLabel, dialectMatchesLang, dialectOptionsFor } from '@shared/api/dialects';
import { DUB_CARD, DubFieldRow, DubHint, DubStepHeading } from './dub-section';
import {
  setDubMultiTargets,
  setDubQuality,
  setDubTarget,
  setDubTranslationOptions,
  type DubSession,
} from './dub-session';
import { DubTranslationToggles } from './dub-translation-toggles';

// Targets include translate-and-speak-only languages (Kabyle, Uyghur).
const targetOptions = TRANSLATION_TARGET_LABELS;
// #3143: Brazilian and European Portuguese are separate target choices; each
// maps to the "Portuguese" track plus its dialect.
const targetChoices = withVariantChoices(targetOptions);
const DEFAULT_DIALECT = '__default__';
const SEGMENTS = 'grid gap-1 rounded-lg bg-muted/30 p-1';
const SEGMENT = 'h-auto min-h-8 min-w-0 whitespace-normal px-2 py-1.5 text-center leading-tight';

export interface DubTranslationPanelProps {
  session: DubSession;
  locked: boolean;
  provider?: TranslationEngine;
  agentLabel?: string;
  agentAvailable: boolean;
  engineQuality: Exclude<DubSession['quality'], 'agent'>;
  argos: {
    error: unknown;
    missing: Array<{ source_lang: string; target_lang: string }>;
    installing: boolean;
    retry: () => void;
    install: () => void;
  };
}

export function DubTranslationPanel({
  session,
  locked,
  provider,
  agentLabel,
  agentAvailable,
  engineQuality,
  argos,
}: DubTranslationPanelProps) {
  const { t, i18n } = useTranslation();
  const target = session.target;
  const code = translationTargetCode(target);
  const extra = (session.multiTargets || []).filter((item) => item.code !== code);
  const addable = targetOptions.filter(
    (language) => language !== target && !extra.some((item) => item.lang === language),
  );
  const dialects = code && !hasVariantChoices(code) ? dialectOptionsFor(code) : [];
  const agent = session.quality === 'agent';
  const agentDescription = agentLabel
    ? t('dub.agent_cli_desc', { agent: agentLabel })
    : t('dub.agent_quality_desc');
  const engineName = provider?.display_name || t('engineSidebar.translation');
  return (
    <section className={cn(DUB_CARD, 'space-y-3')}>
      <DubStepHeading step={2}>{t('dub.target_language')}</DubStepHeading>
      <div className="space-y-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <LanguagePicker
            ariaLabel={t('dub.target_language')}
            value={hasVariantChoices(target) ? variantChoice(target, session.dialect) : target}
            onValueChange={(choice) => {
              const variant = LANGUAGE_VARIANTS[choice];
              const language = variant?.base ?? choice;
              const selectedCode = translationTargetCode(language);
              setDubTarget(language, selectedCode);
              if (variant) setDubTranslationOptions({ dialect: variant.dialect });
              else if (!dialectMatchesLang(session.dialect, selectedCode))
                setDubTranslationOptions({ dialect: undefined });
              if (selectedCode)
                setDubMultiTargets(
                  (session.multiTargets || []).filter((item) => item.code !== selectedCode),
                );
            }}
            options={targetChoices}
            disabled={locked}
            className="min-w-0 flex-1 border border-border/50 bg-background/40"
          />
          {addable.length > 0 && (
            <LanguagePicker
              ariaLabel={t('dub.manage_languages')}
              value={t('dub.add_language')}
              options={addable}
              recentsScope="dub"
              disabled={locked}
              className="shrink-0 text-xs text-muted-foreground"
              onValueChange={(language) => {
                const itemCode = translationTargetCode(language);
                if (itemCode) setDubMultiTargets([...extra, { lang: language, code: itemCode }]);
              }}
            />
          )}
        </div>
        {extra.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label={t('dub.manage_languages')}>
            {extra.map((item) => (
              <li
                key={item.code}
                className="inline-flex h-7 max-w-full items-center gap-1 rounded-full bg-primary/10 pl-2.5 pr-0.5 text-xs font-medium text-primary"
              >
                <span className="truncate">{item.lang}</span>
                <button
                  type="button"
                  disabled={locked}
                  aria-label={`${t('common.delete')} ${item.lang}`}
                  className="flex size-6 shrink-0 items-center justify-center rounded-full hover:bg-primary/15 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                  onClick={() =>
                    setDubMultiTargets(extra.filter((other) => other.code !== item.code))
                  }
                >
                  <XIcon className="size-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
        {dialects.length > 0 && (
          <DubFieldRow label={t('dub.dialect_label')} hint={t('dub.dialect_title')}>
            <Select
              items={[
                { value: DEFAULT_DIALECT, label: t('dub.dialect_default') },
                ...dialects.map((dialect) => ({
                  value: dialect,
                  label: dialectLabel(dialect, i18n.language),
                })),
              ]}
              value={
                session.dialect && dialects.includes(session.dialect)
                  ? session.dialect
                  : DEFAULT_DIALECT
              }
              disabled={locked}
              onValueChange={(value) =>
                setDubTranslationOptions({
                  dialect: !value || value === DEFAULT_DIALECT ? undefined : String(value),
                })
              }
            >
              <SelectTrigger aria-label={t('dub.dialect_label')} className="max-w-[60%]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_DIALECT}>{t('dub.dialect_default')}</SelectItem>
                {dialects.map((dialect) => (
                  <SelectItem key={dialect} value={dialect}>
                    {dialectLabel(dialect, i18n.language)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </DubFieldRow>
        )}
      </div>
      <div className="space-y-2 border-t border-border/40 pt-2">
        <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <LanguagesIcon className="size-3.5" aria-hidden="true" />
          {t('about.translator')}
        </p>
        <div
          role="group"
          aria-label={t('about.translator')}
          className={cn(SEGMENTS, 'grid-cols-2')}
        >
          <Button
            size="sm"
            variant={agent ? 'secondary' : 'ghost'}
            aria-pressed={agent}
            disabled={locked || !agentAvailable}
            className={SEGMENT}
            title={`${t('dub.translate_with_agent')} · ${agentDescription}`}
            onClick={() => setDubQuality('agent')}
          >
            <WandSparklesIcon className="shrink-0" />
            {t('dub.translator_agent')}
          </Button>
          <Button
            size="sm"
            variant={!agent ? 'secondary' : 'ghost'}
            aria-pressed={!agent}
            disabled={locked || !provider}
            className={SEGMENT}
            title={engineName}
            onClick={() => setDubQuality(engineQuality)}
          >
            <LanguagesIcon className="shrink-0" />
            {engineName}
          </Button>
        </div>
        {agent && <DubHint>{agentDescription}</DubHint>}
      </div>
      {agent ? (
        <div className="space-y-2 border-t border-border/40 pt-2">
          <label htmlFor="dub-translation-instructions" className="text-xs font-medium">
            {t('dubStyle.label')}
          </label>
          <textarea
            dir="auto"
            id="dub-translation-instructions"
            aria-describedby="dub-translation-instructions-help"
            rows={3}
            maxLength={5000}
            value={session.translationInstructions || ''}
            disabled={locked}
            onChange={(event) =>
              setDubTranslationOptions({ translationInstructions: event.target.value })
            }
            className="w-full resize-y rounded-lg border border-input bg-background/40 px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          />
          <DubHint id="dub-translation-instructions-help">{t('dubStyle.help')}</DubHint>
        </div>
      ) : (
        <div className="space-y-2 border-t border-border/40 pt-2">
          <p
            className="flex items-center gap-2 text-xs font-medium text-muted-foreground"
            title={t('settings.translate_quality_desc')}
          >
            <GaugeIcon className="size-3.5" aria-hidden="true" />
            {t('settings.translate_quality')}
          </p>
          <div
            role="group"
            aria-label={t('settings.translate_quality')}
            aria-describedby="dub-translate-quality-help"
            className={cn(SEGMENTS, 'grid-cols-3')}
          >
            {(['fast', 'autofit', 'cinematic'] as const).map((quality) => (
              <Button
                key={quality}
                size="sm"
                variant={session.quality === quality ? 'secondary' : 'ghost'}
                aria-pressed={session.quality === quality}
                disabled={locked}
                className={SEGMENT}
                onClick={() => setDubQuality(quality)}
              >
                {t('settings.translate_' + quality)}
              </Button>
            ))}
          </div>
          <DubHint id="dub-translate-quality-help">{t('settings.translate_quality_desc')}</DubHint>
          <DubTranslationToggles session={session} locked={locked} provider={provider} />
        </div>
      )}
      {session.translationFallback && (
        <div role="status" className="space-y-2 text-xs text-muted-foreground">
          <p>{t('dub_workflow.cinematic_no_llm')}</p>
          <Link to="/settings/models/$family" params={{ family: 'llm' }}>
            {t('engineSidebar.llm')}
          </Link>
        </div>
      )}
      {/* #2752: the engine could not honour the chosen dialect. Without
          this the pick looked applied and the output looked simply wrong. */}
      {session.dialectIgnored && (
        <div role="status" className="space-y-2 text-xs text-muted-foreground">
          <p>
            {session.dialectUsed
              ? t('dub_workflow.dialect_used', {
                  variant: languageTagName(session.dialectUsed, i18n.language),
                })
              : t('dub_workflow.dialect_not_applied')}
          </p>
          <Link to="/settings/models/$family" params={{ family: 'llm' }}>
            {t('engineSidebar.llm')}
          </Link>
        </div>
      )}
      <Link
        to="/settings/models/$family"
        params={{ family: 'translation' }}
        className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
      >
        <Settings2Icon className="size-3.5 shrink-0" />
        <span className="truncate" title={engineName}>
          {engineName}
          {provider ? ` · ${t('modelMaintenance.' + provider.category)}` : ''}
        </span>
      </Link>
      {provider?.id === 'argos' && Boolean(argos.error) && (
        <PipelineFailure
          className="text-xs"
          fallback={describeError(argos.error)}
          action={
            <Button size="xs" variant="ghost" onClick={argos.retry}>
              <RotateCcwIcon />
              {t('backend.retry')}
            </Button>
          }
        />
      )}
      {provider?.id === 'argos' && argos.missing.length > 0 && (
        <PipelineFailure
          className="text-xs"
          fallback={`${t('modelMaintenance.install')} · ${provider.display_name} · ${argos.missing
            .map((pair) => `${pair.source_lang} → ${pair.target_lang}`)
            .join(', ')}`}
          action={
            <Button size="xs" variant="outline" disabled={argos.installing} onClick={argos.install}>
              {argos.installing ? <LoaderCircleIcon className="animate-spin" /> : <LanguagesIcon />}
              {t(argos.installing ? 'common.loading' : 'modelMaintenance.install')}
            </Button>
          }
        />
      )}
      {provider && !(provider.ready ?? provider.installed) && (
        <PipelineFailure
          className="text-xs"
          fallback={provider.availability_reason || t('modelSettings.unavailable')}
        />
      )}
    </section>
  );
}
