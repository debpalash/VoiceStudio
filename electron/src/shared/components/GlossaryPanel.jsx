import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Trash2, BookOpen, Sparkles, Check, ChevronUp, X, Pencil } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Panel, Button, Input, Badge } from '../ui';
import './GlossaryPanel.css';
import {
  listGlossary,
  addGlossaryTerm,
  updateGlossaryTerm,
  deleteGlossaryTerm,
  clearGlossary,
  autoExtractGlossary,
} from '../api/glossary';

export default function GlossaryPanel({
  projectId,
  sourceLang = 'en',
  targetLang,
  segments = [],
  onChange,
  onClose,
}) {
  const { t } = useTranslation();
  const [terms, setTerms] = useState([]);
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [draft, setDraft] = useState({ source: '', target: '', note: '' });

  const pushChange = useCallback(
    (next) => {
      onChange?.(next);
    },
    [onChange],
  );

  const reload = useCallback(async () => {
    if (!projectId) {
      setTerms([]);
      pushChange([]);
      return;
    }
    setLoading(true);
    try {
      const rows = await listGlossary(projectId);
      setTerms(rows);
      pushChange(rows);
    } catch (e) {
      toast.error(t('glossary.load_error', { message: e.message }));
    } finally {
      setLoading(false);
    }
  }, [projectId, pushChange, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  const onAdd = async () => {
    if (!projectId) {
      toast.error(t('glossary.needs_project'));
      return;
    }
    if (!draft.source.trim() || !draft.target.trim()) return;
    try {
      const row = await addGlossaryTerm(projectId, draft);
      const next = [...terms, row];
      setTerms(next);
      pushChange(next);
      setDraft({ source: '', target: '', note: '' });
    } catch (e) {
      toast.error(t('glossary.add_error', { message: e.message }));
    }
  };

  const onUpdate = async (id, patch) => {
    try {
      const row = await updateGlossaryTerm(projectId, id, patch);
      const next = terms.map((term) => (term.id === id ? row : term));
      setTerms(next);
      pushChange(next);
    } catch (e) {
      toast.error(t('glossary.update_error', { message: e.message }));
    }
  };

  const onDelete = async (id) => {
    try {
      await deleteGlossaryTerm(projectId, id);
      const next = terms.filter((term) => term.id !== id);
      setTerms(next);
      pushChange(next);
    } catch (e) {
      toast.error(t('glossary.delete_error', { message: e.message }));
    }
  };

  const onClearAuto = async () => {
    if (!confirm(t('glossary.clear_auto_confirm'))) return;
    try {
      await clearGlossary(projectId, true);
      await reload();
    } catch (e) {
      toast.error(t('glossary.clear_error', { message: e.message }));
    }
  };

  const onAutoExtract = async () => {
    if (!targetLang) {
      toast.error(t('glossary.pick_target'));
      return;
    }
    if (!segments.length) {
      toast.error(t('glossary.no_segments'));
      return;
    }
    setExtracting(true);
    try {
      const res = await autoExtractGlossary(projectId, {
        sourceLang,
        targetLang,
        segments: segments.map((s) => ({ text: s.text_original || s.text })),
      });
      setTerms(res.terms);
      pushChange(res.terms);
      if (res.inserted === 0) {
        toast(t('glossary.auto_empty'), { icon: 'ℹ️' });
      } else {
        toast.success(t('glossary.auto_added', { count: res.inserted }));
      }
    } catch (e) {
      toast.error(t('glossary.auto_error', { message: e.message }));
    } finally {
      setExtracting(false);
    }
  };

  const autoCount = terms.filter((term) => term.auto).length;
  const manualCount = terms.length - autoCount;

  return (
    <Panel
      variant="flat"
      padding="sm"
      className="glossary-panel"
      title={
        <>
          <BookOpen size={15} aria-hidden="true" /> {t('glossary.title')}
          <span className="ml-[var(--space-3)] text-[length:var(--text-xs)] font-medium text-[var(--color-fg-subtle)]">
            {t('glossary.count', { count: terms.length })}
            {autoCount > 0 && <> · {t('glossary.auto_count', { count: autoCount })}</>}
          </span>
        </>
      }
      actions={
        <>
          <Button
            variant="subtle"
            size="sm"
            leading={<Sparkles size={11} />}
            onClick={onAutoExtract}
            loading={extracting}
            disabled={!projectId || !targetLang || !segments.length}
            title={t('glossary.auto_title')}
          >
            {t('glossary.auto_btn')}
          </Button>
          {autoCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onClearAuto}
              title={t('glossary.clear_auto_title')}
            >
              {t('glossary.clear_auto_btn')}
            </Button>
          )}
          {onClose && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              title={t('glossary.close')}
              aria-label={t('glossary.close')}
            >
              <ChevronUp size={14} />
            </Button>
          )}
        </>
      }
    >
      <div className="glossary-content">
        {!projectId ? (
          <div className="p-[var(--space-5)] text-center text-[length:var(--text-md)] text-[var(--color-fg-subtle)]">
            {t('glossary.empty_save')}
          </div>
        ) : (
          <>
            <p className="glossary-purpose">{t('dub.glossary_title')}</p>
            {terms.length === 0 && (
              <p className="glossary-empty" role="status">
                {loading ? t('common.loading') : t('glossary.no_terms')}
              </p>
            )}
            {terms.length > 0 && (
              <div className="glossary-table-scroll">
                <table className="w-full border-collapse text-[length:var(--text-sm)] [&_td]:border-b [&_td]:border-b-transparent [&_td]:px-[6px] [&_td]:py-[3px] [&_td]:text-left [&_td]:align-middle [&_th]:border-b [&_th]:border-b-transparent [&_th]:px-[6px] [&_th]:py-[3px] [&_th]:text-left [&_th]:align-middle [&_th]:text-[length:var(--text-xs)] [&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-[0.04em] [&_th]:text-[var(--color-fg-subtle)]">
                  <thead>
                    <tr>
                      <th>{t('glossary.source')}</th>
                      <th>{t('glossary.target')}</th>
                      <th>{t('glossary.note')}</th>
                      <th
                        className="w-[60px]"
                        aria-label={`${t('glossary.auto_badge')} / ${t('glossary.manual_badge')}`}
                      ></th>
                      <th className="w-[26px]" aria-label={t('common.delete')}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {terms.map((term) => (
                      <GlossaryRow
                        key={term.id}
                        term={term}
                        onUpdate={(patch) => onUpdate(term.id, patch)}
                        onDelete={() => onDelete(term.id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <form
              className="glossary-add-form"
              onSubmit={(event) => {
                event.preventDefault();
                void onAdd();
              }}
            >
              <label>
                <span>{t('glossary.source')}</span>
                <Input
                  size="sm"
                  placeholder={t('glossary.source_placeholder', { lang: sourceLang })}
                  value={draft.source}
                  onChange={(e) => setDraft({ ...draft, source: e.target.value })}
                />
              </label>
              <label>
                <span>{t('glossary.target')}</span>
                <Input
                  size="sm"
                  placeholder={t('glossary.target_placeholder', { lang: targetLang || '—' })}
                  value={draft.target}
                  onChange={(e) => setDraft({ ...draft, target: e.target.value })}
                />
              </label>
              <label className="glossary-add-note">
                <span>{t('glossary.note_placeholder')}</span>
                <Input
                  size="sm"
                  value={draft.note}
                  onChange={(e) => setDraft({ ...draft, note: e.target.value })}
                />
              </label>
              <Button
                type="submit"
                variant="subtle"
                size="sm"
                leading={<Plus size={14} />}
                disabled={!draft.source.trim() || !draft.target.trim()}
              >
                {t('glossary.add_term')}
              </Button>
            </form>
            {manualCount > 0 && targetLang && (
              <div className="mt-[var(--space-3)] px-[var(--space-3)] py-[2px] text-[length:var(--text-2xs)] italic text-[var(--color-fg-subtle)]">
                {t('glossary.hint')}
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}

function GlossaryRow({ term, onUpdate, onDelete }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [local, setLocal] = useState({
    source: term.source,
    target: term.target,
    note: term.note || '',
  });

  useEffect(() => {
    setLocal({ source: term.source, target: term.target, note: term.note || '' });
  }, [term.source, term.target, term.note]);

  if (editing) {
    const save = () => {
      onUpdate(local);
      setEditing(false);
    };
    return (
      <tr>
        <td>
          <Input
            size="sm"
            aria-label={t('glossary.source')}
            value={local.source}
            onChange={(e) => setLocal({ ...local, source: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter') save();
              if (e.key === 'Escape') setEditing(false);
            }}
            autoFocus
          />
        </td>
        <td>
          <Input
            size="sm"
            aria-label={t('glossary.target')}
            value={local.target}
            onChange={(e) => setLocal({ ...local, target: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter') save();
              if (e.key === 'Escape') setEditing(false);
            }}
          />
        </td>
        <td>
          <Input
            size="sm"
            aria-label={t('glossary.note')}
            value={local.note}
            onChange={(e) => setLocal({ ...local, note: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter') save();
              if (e.key === 'Escape') setEditing(false);
            }}
          />
        </td>
        <td />
        <td className="flex justify-end gap-[var(--space-1)]">
          <Button variant="subtle" iconSize="sm" onClick={save} title={t('common.save')}>
            <Check size={10} />
          </Button>
          <Button
            variant="ghost"
            iconSize="sm"
            onClick={() => setEditing(false)}
            title={t('common.cancel')}
          >
            <X size={10} />
          </Button>
        </td>
      </tr>
    );
  }

  return (
    <tr onDoubleClick={() => setEditing(true)}>
      <td className="font-medium text-[var(--color-fg)]">{term.source}</td>
      <td className="font-medium text-[var(--color-brand)]">{term.target}</td>
      <td className="text-[length:var(--text-xs)] italic text-[var(--color-fg-subtle)]">
        {term.note}
      </td>
      <td>
        {term.auto ? (
          <Badge tone="violet" size="xs">
            {t('glossary.auto_badge')}
          </Badge>
        ) : (
          <Badge tone="success" size="xs">
            {t('glossary.manual_badge')}
          </Badge>
        )}
      </td>
      <td className="flex justify-end gap-[var(--space-1)]">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setEditing(true)}
          aria-label={t('clone.edit')}
          title={t('clone.edit')}
        >
          <Pencil size={12} />
        </Button>
        <Button variant="danger" iconSize="sm" onClick={onDelete} title={t('common.delete')}>
          <Trash2 size={10} />
        </Button>
      </td>
    </tr>
  );
}
