import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { apiFetch, apiJson, describeError } from '@/lib/api/client';
import { saveLocalFile } from '@/lib/local-export';
import { projectLibrary, type LongformProject } from '../longform/project-library';
import { importedLongformDraft, type ProjectBundleResult } from './project-handoff-format';

interface InboxEntry {
  id: string;
  title: string;
  kind: string;
  incoming: boolean;
}
export interface HandoffSelection {
  id: string;
  kind: 'dub' | 'stories' | 'audiobook';
  name: string;
}

export function ProjectHandoff({
  selection,
  disabled,
}: {
  selection?: HandoffSelection;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [permission, setPermission] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const pending = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const inbox = useQuery({
    queryKey: ['project-handoff'],
    queryFn: ({ signal }) => apiJson<InboxEntry[]>('/project-bundles', { signal }),
    enabled: open,
  });
  const act = async (operation: () => Promise<void>) => {
    if (pending.current || disabled) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await operation();
      await Promise.all(
        ['projects', 'longform-projects', 'profiles', 'project-handoff'].map((key) =>
          client.invalidateQueries({ queryKey: [key] }),
        ),
      );
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  const install = async (result: ProjectBundleResult) => {
    if (result.kind === 'dub') {
      if (!result.projectId) throw new Error(t('mobile.errors.invalid_response'));
    } else {
      const draft = importedLongformDraft(result);
      await projectLibrary.save(result.title, result.kind, draft);
    }
    setSuccess(result.title);
  };
  const exportSelected = async () => {
    if (!selection) return;
    let project: LongformProject | undefined;
    if (selection.kind !== 'dub') {
      project = (await projectLibrary.list()).find((item) => item.id === selection.id);
      if (!project) throw new Error(t('mobile.errors.file'));
    }
    const response = await apiFetch('/project-bundles/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind: selection.kind,
        project_id: selection.kind === 'dub' ? selection.id : undefined,
        project,
      }),
    });
    await saveLocalFile(
      await response.blob(),
      selection.name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_') + '.vsproject',
    );
  };
  return (
    <>
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => setOpen(true)}>
        {t('mobile.handoff.title')}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(value) => {
          if (!pending.current) setOpen(value);
        }}
      >
        <DialogContent className="max-h-[85dvh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t('mobile.handoff.title')}</DialogTitle>
            <DialogDescription>{t('mobile.handoff.hint')}</DialogDescription>
          </DialogHeader>
          {selection && (
            <Button disabled={busy || disabled} onClick={() => void act(exportSelected)}>
              {t('mobile.handoff.export')} · {selection.name}
            </Button>
          )}
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1 accent-primary"
              checked={permission}
              disabled={busy}
              onChange={(event) => setPermission(event.target.checked)}
            />
            {t('mobile.handoff.permission')}
          </label>
          <input
            ref={fileInput}
            type="file"
            accept=".vsproject,application/zip"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (!file) return;
              void act(async () => {
                if (file.size > 1024 * 1024 * 1024 + 8 * 1024 * 1024)
                  throw new Error(t('mobile.errors.file'));
                await install(
                  await apiJson<ProjectBundleResult>(
                    '/project-bundles/import?allow_voices=' + permission,
                    { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file },
                  ),
                );
              });
            }}
          />
          <Button
            variant="secondary"
            disabled={busy || disabled}
            onClick={() => fileInput.current?.click()}
          >
            {t('mobile.handoff.import')}
          </Button>
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">{t('mobile.handoff.inbox')}</h3>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy || inbox.isFetching}
              onClick={() => void inbox.refetch()}
            >
              {t('common.refresh')}
            </Button>
          </div>
          {inbox.isPending && <p role="status">{t('common.loading')}</p>}
          {(inbox.data ?? [])
            .filter((item) => item.incoming)
            .map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-2 border-t py-2">
                <span className="min-w-0 truncate">{item.title}</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy || disabled}
                  onClick={() =>
                    void act(async () =>
                      install(
                        await apiJson<ProjectBundleResult>(
                          `/project-bundles/${encodeURIComponent(item.id)}/document`,
                        ),
                      ),
                    )
                  }
                >
                  {t('mobile.handoff.import')}
                </Button>
              </div>
            ))}
          {(error || inbox.error) && (
            <p role="alert" className="text-sm text-destructive">
              {error ?? describeError(inbox.error)}
            </p>
          )}
          {success && (
            <p role="status" className="text-sm">
              {t('mobile.handoff.imported')} · {success}
            </p>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
