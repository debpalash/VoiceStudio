import { useCallback, useEffect, useState } from 'react';
import {
  BookOpenIcon,
  CheckIcon,
  LoaderCircleIcon,
  PencilIcon,
  PlusIcon,
  SparklesIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiJson, describeError } from '@/lib/api/client';
import type { DubSegment } from './dub-session';

interface GlossaryTerm {
  id: string;
  source: string;
  target: string;
  note?: string;
  auto?: boolean;
}

interface GlossaryPanelProps {
  projectId: string;
  sourceLang: string;
  targetLang: string;
  segments: DubSegment[];
  disabled?: boolean;
  onCountChange: (count: number) => void;
  onClose: () => void;
}

const emptyDraft = { source: '', target: '', note: '' };

export function GlossaryPanel({
  projectId,
  sourceLang,
  targetLang,
  segments,
  disabled,
  onCountChange,
  onClose,
}: GlossaryPanelProps) {
  const { t } = useTranslation();
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [draft, setDraft] = useState(emptyDraft);
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState(emptyDraft);
  const [loading, setLoading] = useState(true);
  const [extracting, setExtracting] = useState(false);

  const publish = useCallback(
    (next: GlossaryTerm[]) => {
      setTerms(next);
      onCountChange(next.length);
    },
    [onCountChange],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void apiJson<GlossaryTerm[]>(`/glossary/${encodeURIComponent(projectId)}`, {
      signal: controller.signal,
    })
      .then((next) => {
        if (!controller.signal.aborted) publish(next);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          toast.error(t('glossary.load_error', { message: describeError(error) }));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, publish, t]);

  const addTerm = async () => {
    if (!draft.source.trim() || !draft.target.trim()) return;
    try {
      const term = await apiJson<GlossaryTerm>(`/glossary/${encodeURIComponent(projectId)}`, {
        method: 'POST',
        body: JSON.stringify(draft),
      });
      publish([...terms, term]);
      setDraft(emptyDraft);
    } catch (error) {
      toast.error(t('glossary.add_error', { message: describeError(error) }));
    }
  };

  const saveTerm = async (id: string) => {
    if (!editDraft.source.trim() || !editDraft.target.trim()) return;
    try {
      const term = await apiJson<GlossaryTerm>(
        `/glossary/${encodeURIComponent(projectId)}/${encodeURIComponent(id)}`,
        { method: 'PUT', body: JSON.stringify(editDraft) },
      );
      publish(terms.map((item) => (item.id === id ? term : item)));
      setEditing(null);
    } catch (error) {
      toast.error(t('glossary.update_error', { message: describeError(error) }));
    }
  };

  const deleteTerm = async (id: string) => {
    try {
      await apiJson(`/glossary/${encodeURIComponent(projectId)}/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      publish(terms.filter((item) => item.id !== id));
    } catch (error) {
      toast.error(t('glossary.delete_error', { message: describeError(error) }));
    }
  };

  const autoExtract = async () => {
    setExtracting(true);
    try {
      const result = await apiJson<{ inserted: number; terms: GlossaryTerm[] }>(
        `/glossary/${encodeURIComponent(projectId)}/auto-extract`,
        {
          method: 'POST',
          body: JSON.stringify({
            source_lang: sourceLang || 'auto',
            target_lang: targetLang,
            segments: segments.map((segment) => ({
              id: segment.id,
              text: segment.text_original || segment.text,
            })),
          }),
        },
      );
      publish(result.terms || []);
      toast.success(
        result.inserted
          ? t('glossary.auto_added', { count: result.inserted })
          : t('glossary.auto_empty'),
      );
    } catch (error) {
      toast.error(t('glossary.auto_error', { message: describeError(error) }));
    } finally {
      setExtracting(false);
    }
  };

  return (
    <section className="rounded-xl border border-primary/20 bg-primary/[0.035] shadow-sm">
      <div className="flex items-start gap-3 border-b border-border/50 px-4 py-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <BookOpenIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            {t('glossary.title')}
            <span className="font-normal tabular-nums text-muted-foreground">
              {t('glossary.count', { count: terms.length })}
            </span>
          </h2>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
            {t('dub.glossary_title')}
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || extracting || !segments.length || !targetLang}
          aria-busy={extracting}
          title={t('glossary.auto_title')}
          onClick={() => void autoExtract()}
        >
          {extracting ? <LoaderCircleIcon className="animate-spin" /> : <SparklesIcon />}
          {t('glossary.auto_btn')}
        </Button>
        <Button size="icon-xs" variant="ghost" aria-label={t('glossary.close')} onClick={onClose}>
          <XIcon />
        </Button>
      </div>
      <div className="space-y-3 p-4">
        {loading && (
          <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
            <LoaderCircleIcon className="size-3.5 animate-spin" />
            {t('common.loading')}
          </p>
        )}
        {!loading && !terms.length && (
          <p role="status" className="text-xs text-muted-foreground">
            {t('glossary.no_terms')}
          </p>
        )}
        {terms.length > 0 && (
          <div className="divide-y divide-border/40 overflow-hidden rounded-lg border border-border/60 bg-background/30">
            {terms.map((term) =>
              editing === term.id ? (
                <form
                  key={term.id}
                  className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-2 p-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void saveTerm(term.id);
                  }}
                >
                  <Input
                    autoFocus
                    aria-label={t('glossary.source')}
                    value={editDraft.source}
                    onChange={(event) => setEditDraft({ ...editDraft, source: event.target.value })}
                  />
                  <Input
                    aria-label={t('glossary.target')}
                    value={editDraft.target}
                    onChange={(event) => setEditDraft({ ...editDraft, target: event.target.value })}
                  />
                  <Input
                    aria-label={t('glossary.note')}
                    value={editDraft.note}
                    onChange={(event) => setEditDraft({ ...editDraft, note: event.target.value })}
                  />
                  <div className="flex items-center gap-1">
                    <Button size="icon-xs" type="submit" aria-label={t('common.save')}>
                      <CheckIcon />
                    </Button>
                    <Button
                      size="icon-xs"
                      type="button"
                      variant="ghost"
                      aria-label={t('common.cancel')}
                      onClick={() => setEditing(null)}
                    >
                      <XIcon />
                    </Button>
                  </div>
                </form>
              ) : (
                <div
                  key={term.id}
                  className="grid grid-cols-[minmax(0,1fr)_1rem_minmax(0,1fr)_minmax(0,.8fr)_auto] items-center gap-2 px-3 py-2 text-xs"
                >
                  <span className="truncate font-medium">{term.source}</span>
                  <span className="text-center text-muted-foreground">→</span>
                  <span className="truncate font-medium text-primary">{term.target}</span>
                  <span className="truncate text-muted-foreground">
                    {term.note || (term.auto ? t('glossary.auto_badge') : '')}
                  </span>
                  <div className="flex gap-1">
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      disabled={disabled}
                      aria-label={t('clone.edit')}
                      onClick={() => {
                        setEditing(term.id);
                        setEditDraft({
                          source: term.source,
                          target: term.target,
                          note: term.note || '',
                        });
                      }}
                    >
                      <PencilIcon />
                    </Button>
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      disabled={disabled}
                      aria-label={t('common.delete')}
                      onClick={() => void deleteTerm(term.id)}
                    >
                      <Trash2Icon />
                    </Button>
                  </div>
                </div>
              ),
            )}
          </div>
        )}
        <form
          className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void addTerm();
          }}
        >
          <Input
            aria-label={t('glossary.source')}
            placeholder={t('glossary.source_placeholder', { lang: sourceLang || 'auto' })}
            value={draft.source}
            disabled={disabled}
            onChange={(event) => setDraft({ ...draft, source: event.target.value })}
          />
          <Input
            aria-label={t('glossary.target')}
            placeholder={t('glossary.target_placeholder', { lang: targetLang })}
            value={draft.target}
            disabled={disabled}
            onChange={(event) => setDraft({ ...draft, target: event.target.value })}
          />
          <Input
            aria-label={t('glossary.note')}
            placeholder={t('glossary.note_placeholder')}
            value={draft.note}
            disabled={disabled}
            onChange={(event) => setDraft({ ...draft, note: event.target.value })}
          />
          <Button
            type="submit"
            variant="outline"
            disabled={disabled || !draft.source.trim() || !draft.target.trim()}
          >
            <PlusIcon />
            {t('glossary.add_term')}
          </Button>
        </form>
        <p className="text-xs text-muted-foreground">{t('glossary.hint')}</p>
      </div>
    </section>
  );
}
