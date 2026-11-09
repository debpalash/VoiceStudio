import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { SaveIcon, TrashIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { blankLongformDraft, type Draft, type Mode } from './longform-session';
import { projectLibrary, type LongformProject } from './project-library';
import { describeError } from '@/lib/api/client';
export function ProjectSettings({
  mode,
  draft,
  disabled,
  bare = false,
  onChange,
  onBusy,
}: {
  mode: Mode;
  draft: Draft;
  disabled: boolean;
  /** Rendered as the Projects tab of the section pane instead of a disclosure. */
  bare?: boolean;
  onChange: (value: Partial<Draft>) => void;
  onBusy: (busy: boolean) => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const pending = useRef(false);
  const dirtyName = useRef(false);
  const [name, setName] = useState(draft.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirmation, setConfirmation] = useState<{
    action: 'open' | 'delete';
    project: LongformProject;
  } | null>(null);
  const query = useQuery({
    queryKey: ['longform-projects'],
    queryFn: projectLibrary.list,
  });
  useEffect(() => {
    const saved = query.data?.find((p) => p.id === draft.projectId);
    if (saved && !dirtyName.current) setName(saved.name);
  }, [query.data, draft.projectId]);
  const locked = disabled || busy || !query.data;
  const run = async (work: () => Promise<void>) => {
    if (locked || pending.current) return;
    pending.current = true;
    setBusy(true);
    onBusy(true);
    setError(null);
    setSaved(false);
    try {
      await work();
      await client.invalidateQueries({ queryKey: ['longform-projects'] });
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      pending.current = false;
      setBusy(false);
      onBusy(false);
    }
  };
  const save = (copy: boolean) =>
    run(async () => {
      const project = await projectLibrary.save(name, mode, draft, copy ? null : draft.projectId);
      onChange({ projectId: project.id });
      setSaved(true);
    });
  const apply = () =>
    run(async () => {
      if (!confirmation) return;
      if (confirmation.action === 'delete') {
        await projectLibrary.remove(confirmation.project.id);
        if (draft.projectId === confirmation.project.id) onChange({ projectId: null });
      } else {
        // Re-read after confirmation so an outdated list never opens a deleted record.
        const project = (await projectLibrary.list()).find((p) => p.id === confirmation.project.id);
        if (!project) throw new Error('Project no longer exists');
        onChange({
          ...blankLongformDraft(),
          ...structuredClone(project.draft),
          projectId: project.id,
        });
        dirtyName.current = false;
        setName(project.name);
      }
      setConfirmation(null);
    });
  const Wrapper = bare ? 'div' : 'details';
  return (
    <Wrapper className="space-y-3">
      {!bare && (
        <summary className="cursor-pointer text-sm font-medium">{t('stories.projects')}</summary>
      )}
      <label className="block space-y-1 text-xs">
        {t('stories.projectName')}
        <Input
          value={name}
          disabled={locked}
          onChange={(e) => {
            dirtyName.current = true;
            setName(e.target.value);
            setSaved(false);
          }}
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={locked || !name.trim()} onClick={() => void save(false)}>
          <SaveIcon />
          {t('common.save')}
        </Button>
        {draft.projectId && (
          <Button
            size="sm"
            variant="ghost"
            disabled={locked || !name.trim()}
            onClick={() => void save(true)}
          >
            {t('projectActions.save_copy')}
          </Button>
        )}
      </div>
      {saved && (
        <p role="status" className="text-xs text-muted-foreground">
          {t('app.toast_project_saved')}
        </p>
      )}
      {(error || query.isError) && (
        <div role="alert" className="text-xs text-destructive">
          {error || describeError(query.error)}
          <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      )}
      {query.isPending && (
        <p role="status" className="text-xs">
          {t('common.loading')}
        </p>
      )}
      <div className={bare ? 'space-y-1' : 'max-h-64 space-y-1 overflow-y-auto'}>
        {query.data
          ?.filter((project) => project.mode === mode)
          .map((project) => (
            <div key={project.id}>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  className="min-w-0 flex-1 justify-start truncate"
                  variant={draft.projectId === project.id ? 'secondary' : 'ghost'}
                  disabled={locked}
                  onClick={() => setConfirmation({ action: 'open', project })}
                >
                  {project.name}
                </Button>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  disabled={locked}
                  aria-label={t('common.delete') + ' ' + project.name}
                  onClick={() => setConfirmation({ action: 'delete', project })}
                >
                  <TrashIcon />
                </Button>
              </div>
              {confirmation?.project.id === project.id && (
                <div className="space-y-2 rounded-md bg-muted/40 p-2 text-xs">
                  <p>
                    {t(
                      confirmation.action === 'delete'
                        ? 'stories.deleteProjectConfirm'
                        : 'projectActions.open',
                      { name: project.name },
                    )}
                  </p>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant={confirmation.action === 'delete' ? 'destructive' : 'default'}
                      disabled={locked}
                      onClick={() => void apply()}
                    >
                      {t('common.confirm')}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => setConfirmation(null)}
                    >
                      {t('common.cancel')}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
      </div>
    </Wrapper>
  );
}
