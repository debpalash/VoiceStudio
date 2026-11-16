import { dubCueIssues } from './dub-issue-state';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LoaderCircleIcon, RefreshCwIcon, SparklesIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { apiJson, describeError, isAbortError } from '@/lib/api/client';
import { hasCompleteTranslation } from '@shared/utils/multiLang';
import type { DubSession } from './dub-session';
import { dubGenerationRequest, type DubGenerationOptions } from './dub-generation-request';

export interface DubGenerationPlan {
  total: number;
  synthesize: number;
  reuse: number;
  silence: number;
  forced: number;
  issues?: Array<{ segment_id: string; code: string }>;
}
type TargetPlan = { language: string; needsTranslation: boolean; plan?: DubGenerationPlan };

export function DubGenerationReview(props: Parameters<typeof OpenDubGenerationReview>[0]) {
  return props.open ? <OpenDubGenerationReview {...props} /> : null;
}

function OpenDubGenerationReview({
  open,
  session,
  targets,
  options,
  onClose,
  onConfirm,
  onFix,
  inline = false,
}: {
  inline?: boolean;
  open: boolean;
  session: DubSession;
  targets: Array<{ lang: string; code: string }>;
  options: DubGenerationOptions;
  onClose: () => void;
  onConfirm: () => void;
  onFix?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const encoded = JSON.stringify(
    targets.map((target) => {
      const needsTranslation =
        targets.length > 1 &&
        (!(session.quality !== 'agent' || Boolean(session.agentCli)) ||
          !hasCompleteTranslation(session.segments, target.code));
      const state =
        targets.length > 1
          ? {
              ...session,
              target: target.lang,
              segments: session.segments.map((segment) => ({
                ...segment,
                text: segment.translations?.[target.code] || segment.text,
              })),
            }
          : session;
      return {
        language: target.lang,
        needsTranslation,
        body: dubGenerationRequest(state, target.lang, target.code, options),
      };
    }),
  );
  const jobId = session.jobId;
  const [result, setResult] = useState<{ key: string; plans: TargetPlan[]; error?: string } | null>(
    null,
  );
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!open || !jobId) return;
    const controller = new AbortController();
    void (async () => {
      const rows = JSON.parse(encoded) as Array<{
        language: string;
        needsTranslation: boolean;
        body: unknown;
      }>;
      const plans: TargetPlan[] = [];
      for (const row of rows) {
        const plan = row.needsTranslation
          ? undefined
          : await apiJson<DubGenerationPlan>('/dub/plan/' + encodeURIComponent(jobId), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              signal: controller.signal,
              body: JSON.stringify(row.body),
            });
        plans.push({ language: row.language, needsTranslation: row.needsTranslation, plan });
      }
      if (!controller.signal.aborted) setResult({ key: encoded, plans });
    })().catch((error) => {
      if (!isAbortError(error) && !controller.signal.aborted)
        setResult({ key: encoded, plans: [], error: describeError(error) });
    });
    return () => controller.abort();
  }, [open, jobId, encoded, revision]);
  const current = result?.key === encoded ? result : null;
  const pending = !current;
  const localIssues = dubCueIssues(session);
  const serverIssues = current?.plans.flatMap((row) => row.plan?.issues || []) || [];
  const blocked = localIssues.length > 0 || serverIssues.length > 0;
  const issueId = localIssues[0]?.segment.id || serverIssues[0]?.segment_id;
  const force = Boolean(options.regenOnly?.length);
  const totalNew = useMemo(
    () => current?.plans.reduce((sum, row) => sum + (row.plan?.synthesize || 0), 0) || 0,
    [current],
  );
  const mixOnly =
    !blocked &&
    !pending &&
    !current?.error &&
    current?.plans.every((row) => row.plan) &&
    totalNew === 0;
  const actionLabel = pending ? t('dub.plan_checking') : current?.plans.some(row => row.needsTranslation) ? t('dub.generate_needed') : mixOnly
    ? t('dub.assemble_track')
    : force
      ? t('dub.regen_changed', { count: totalNew })
      : t(
          current?.plans.some((row) => (row.plan?.reuse || 0) > 0)
            ? 'dub.flow_continue_count'
            : 'dub.flow_generate_count',
          { amount: totalNew },
        );
  const content = (
    <div className="space-y-3">
      <div className="space-y-1">
        <h2 className="text-sm font-medium">
          {t(mixOnly ? 'dub.assemble_track' : 'dub.guide_audio')}
        </h2>
        <p className="text-xs text-muted-foreground">
          {t(force ? 'dub.plan_forced' : 'dub.plan_needed')}
        </p>
        {session.narration?.enabled && (
          <p className="text-xs text-muted-foreground">{t('dub.narration_plan')}</p>
        )}
      </div>
      {blocked && (
        <div role="alert" className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
          <p>{t('dub.issues_hint')}</p>
          {onFix && issueId && (
            <Button size="sm" variant="outline" onClick={() => onFix(issueId)}>
              {t('dub.issue_locate')}
            </Button>
          )}
        </div>
      )}
      {pending ? (
        <p role="status" className="flex items-center gap-2 text-sm">
          <LoaderCircleIcon className="size-4 animate-spin" />
          {t('dub.plan_checking')}
        </p>
      ) : current.error ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>{current.error}</p>
          <Button
            variant="outline"
            onClick={() => {
              setResult(null);
              setRevision((value) => value + 1);
            }}
          >
            {t('common.retry')}
          </Button>
        </div>
      ) : (
        <div className="max-h-[45vh] space-y-3 overflow-auto">
          {current.plans.map((row) => (
            <section key={row.language} className="rounded-lg border border-border/50 p-3">
              <p className="mb-2 text-sm font-medium">{row.language}</p>
              {row.needsTranslation ? (
                <p className="text-xs text-muted-foreground">{t('dub.plan_translation_first')}</p>
              ) : (
                row.plan && (
                  <dl className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <dt className="flex items-center gap-1 text-muted-foreground">
                        <SparklesIcon className="size-3" />
                        {t('dub.plan_new')}
                      </dt>
                      <dd className="text-2xl font-semibold tabular-nums">{row.plan.synthesize}</dd>
                    </div>
                    <div>
                      <dt className="flex items-center gap-1 text-muted-foreground">
                        <RefreshCwIcon className="size-3" />
                        {t('dub.plan_reuse')}
                      </dt>
                      <dd className="text-2xl font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                        {row.plan.reuse}
                      </dd>
                    </div>
                    {row.plan.silence > 0 && (
                      <div className="col-span-2 text-xs text-muted-foreground">
                        {t('dub.plan_silence', { amount: row.plan.silence })}
                      </div>
                    )}
                  </dl>
                )
              )}
            </section>
          ))}
          {!blocked && !totalNew && current.plans.every((row) => row.plan) && (
            <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
              {t('dub.plan_mix_only')}
            </p>
          )}
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t('dub.plan_mix_hint')}</p>
      {options.preview && session.quality !== 'agent' && (
        <p className="text-xs text-muted-foreground">{t('dub.plan_preview_hint')}</p>
      )}
      {session.quality === 'agent' && (
        <p className="text-xs text-muted-foreground">{t('dub.plan_agent_hint')}</p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        {!inline && (
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
        )}
        <Button disabled={pending || Boolean(current?.error) || blocked} onClick={onConfirm}>
          {actionLabel}
        </Button>
      </div>
    </div>
  );
  if (inline)
    return (
      <section data-slot="dub-generation-plan" className="shrink-0 border-b bg-primary/5 px-4 py-3">
        {content}
      </section>
    );
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose();
      }}
    >
      <DialogContent className="sm:max-w-lg" showCloseButton={false}>
        <DialogHeader className="sr-only">
          <DialogTitle>{t('dub.guide_audio')}</DialogTitle>
          <DialogDescription>{t('dub.plan_mix_hint')}</DialogDescription>
        </DialogHeader>
        {content}
      </DialogContent>
    </Dialog>
  );
}
