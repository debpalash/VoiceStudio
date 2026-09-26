import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { FolderOpenIcon } from 'lucide-react';
import type { ProfileUsage } from '@shared/api/types';
import { apiJson, describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { PipelineFailure } from '@/components/pipeline-failure';
import { openDubProject, useDubSession } from '../dub/dub-session';
import { useLongformSession } from '../longform/longform-session';
import type { DubProject } from '../projects/project-format';

export function ProfileUsagePanel({ id }: { id: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const session = useDubSession();
  const longform = useLongformSession();
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const usage = useQuery({
    queryKey: ['profile-usage', id],
    queryFn: ({ signal }) =>
      apiJson<ProfileUsage>(`/profiles/${encodeURIComponent(id)}/usage`, { signal }),
  });
  const locked =
    busy ||
    Boolean(longform.active || session.recovery) ||
    !['idle', 'editing', 'done'].includes(session.phase);
  const open = async (projectId: string) => {
    if (locked) return;
    setBusy(true);
    setFailed(null);
    try {
      const project = await apiJson<DubProject>('/projects/' + encodeURIComponent(projectId));
      if (!openDubProject(project)) throw new Error('Project is busy');
      await navigate({ to: '/dub' });
    } catch (error) {
      setFailed(describeError(error));
    } finally {
      setBusy(false);
    }
  };
  if (usage.isPending)
    return (
      <p role="status" className="text-xs text-muted-foreground">
        {t('preferences.loading')}
      </p>
    );
  if (usage.isError)
    return (
      <PipelineFailure
        fallback={describeError(usage.error)}
        action={
          <Button type="button" variant="ghost" size="sm" onClick={() => void usage.refetch()}>
            {t('backend.retry')}
          </Button>
        }
      />
    );
  if (!usage.data.synth_total && !usage.data.projects.length) return null;
  return (
    <section className="space-y-3 border-t border-border/50 pt-4">
      <h3 className="text-sm font-medium">{t('voice_profile.used_title')}</h3>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{t('voice_profile.synth_clips', { count: usage.data.synth_total })}</span>
        <span>{t('voice_profile.projects_count', { count: usage.data.projects.length })}</span>
        <span>
          {t('voice_profile.dubbed_segments', { count: usage.data.project_total_segments })}
        </span>
      </div>
      <ul className="max-h-60 space-y-1 overflow-y-auto">
        {usage.data.projects.map((project) => (
          <li key={project.project_id}>
            <Button
              type="button"
              variant="ghost"
              disabled={locked}
              className="h-auto w-full justify-start py-2"
              onClick={() => setPending(project.project_id)}
            >
              <FolderOpenIcon />
              <span className="min-w-0 flex-1 truncate text-left">{project.project_name}</span>
              <span className="text-xs tabular-nums text-muted-foreground">
                {project.segment_count}
              </span>
            </Button>
            {pending === project.project_id && (
              <div className="space-y-2 rounded-md bg-muted/40 p-3 text-xs">
                <p>{t('projectActions.open', { name: project.project_name })}</p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled={locked}
                    onClick={() => void open(project.project_id)}
                  >
                    {t('common.confirm')}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setPending(null)}
                  >
                    {t('common.cancel')}
                  </Button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
      {failed && (
        <PipelineFailure fallback={failed} onDismiss={() => setFailed(null)} className="text-xs" />
      )}
    </section>
  );
}
