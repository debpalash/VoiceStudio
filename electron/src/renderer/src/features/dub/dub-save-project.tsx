import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { SaveIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiJson, describeError } from '@/lib/api/client';
import { useLongformSession } from '../longform/longform-session';
import { projectPayload } from '../projects/project-format';
import { attachDubProject, useDubSession } from './dub-session';

/**
 * Save the open dub as a project, beside the dub's saved projects. It lives in
 * Dubbing (not the Projects library) because it saves the work on this page.
 */
export function DubSaveProject() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const session = useDubSession();
  const longform = useLongformSession();
  const current = session.project?.name || session.filename;
  // Follows the project's name (a rename from Projects) until the user types their own.
  const [draft, setDraft] = useState<{ base: string; value: string } | null>(null);
  const name = draft && draft.base === current ? draft.value : current;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const pending = useRef(false);
  if (!session.jobId) return null;
  const locked =
    busy ||
    !!longform.active ||
    !!session.recovery ||
    !['idle', 'editing', 'done'].includes(session.phase);
  const save = async (copy: boolean) => {
    if (pending.current || locked || !name.trim()) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const snapshot = session;
      const payload = projectPayload(snapshot, name);
      const id = copy ? undefined : snapshot.project?.id;
      const result = await apiJson<{ id: string }>(
        '/projects' + (id ? '/' + encodeURIComponent(id) : ''),
        { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) },
      );
      attachDubProject(snapshot, { ...payload, id: result.id });
      setDraft(null);
      setSaved(true);
      await client.invalidateQueries({ queryKey: ['projects'] });
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  return (
    <form
      data-slot="dub-save-project"
      className="shrink-0 space-y-2 border-b border-border/45 p-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        void save(false);
      }}
    >
      <label htmlFor="project-name" className="text-xs font-medium">
        {t('sidebar.save_project')}
      </label>
      <div className="flex gap-1.5">
        <Input
          id="project-name"
          value={name}
          onChange={(event) => setDraft({ base: current, value: event.target.value })}
          disabled={locked}
          className="h-8 min-w-0 flex-1 text-xs"
        />
        <Button type="submit" size="sm" disabled={locked || !name.trim()}>
          <SaveIcon />
          {t('common.save')}
        </Button>
      </div>
      {session.project && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="w-full"
          disabled={locked || !name.trim()}
          onClick={() => void save(true)}
        >
          {t('sidebar.save_new_project')}
        </Button>
      )}
      {saved && (
        <p role="status" className="text-xs text-muted-foreground">
          {t('app.toast_project_saved')}
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </form>
  );
}
