import { StoryStems } from './story-stems';
import { previewStoryLine } from './story-preview';
import { storyVoicesReady } from './story-inputs';
import { DEFAULT_SPLIT_MODE, splitStoryText, type SplitMode } from '@shared/utils/splitStoryText';
import { useEffect, useRef, useState } from 'react';
import { buildAutoCast } from '@shared/utils/autoCast';
import { SAMPLE_STORY_CAST, SAMPLE_STORY_LINES, SAMPLE_STORY_NAME } from '@shared/data/sampleStory';
import { useTranslation } from 'react-i18next';
import { BookOpenTextIcon, ChevronRightIcon, PlusIcon, SparklesIcon } from 'lucide-react';
import { StoryBlock } from './story-block';
import { Button } from '@/components/ui/button';
import { SplitPresetPicker } from './split-preset-picker';
import { PipelineFailure } from '@/components/pipeline-failure';
import { describeError } from '@/lib/api/client';
import { reorder } from '@shared/utils/storyReorder';
import type { Draft } from './longform-session';
interface Props {
  draft: Draft;
  profiles: { id: string; name: string }[];
  disabled: boolean;
  canSynthesize?: boolean;
  onChange: (patch: Partial<Draft>) => void;
  onBusy?: (busy: boolean) => void;
}

function insertStoryToken(text: string, caret: number, token: string) {
  const before = text.slice(0, caret);
  const after = text.slice(caret);
  const left = before && !/\s$/.test(before) ? before + ' ' : before;
  const right = after && !/^\s/.test(after) ? ' ' + after : after;
  return left + token + right;
}

function applyInlineVoice(text: string, start: number, end: number, profileId: string) {
  const before = text.slice(0, start);
  const selected = text.slice(start, end);
  const after = text.slice(end);
  const token = `[voice:${profileId}]`;
  return selected
    ? `${before}${token}${selected}[voice:default]${after}`
    : `${before}${token}${after}`;
}
export function StoryEditor({
  draft,
  profiles,
  disabled,
  canSynthesize = true,
  onChange,
  onBusy,
}: Props) {
  const { t } = useTranslation();
  const controller = useRef<AbortController | null>(null);
  const lineInputs = useRef(new Map<string, HTMLTextAreaElement>());
  const [preview, setPreview] = useState<{ id: string; url: string } | null>(null);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview.url);
    },
    [preview],
  );
  useEffect(() => {
    setPreview(null);
  }, [
    draft.lines,
    draft.cast,
    draft.voice,
    draft.language,
    draft.voiceCast,
    draft.overrides,
    draft.globalSpeed,
  ]);
  const audition = async (line: Draft['lines'][number]) => {
    if (disabled || !canSynthesize || controller.current) return;
    const current = new AbortController();
    controller.current = current;
    setPreviewing(line.id);
    setPreviewError(null);
    onBusy?.(true);
    try {
      const blob = await previewStoryLine(draft, line, current.signal, profiles);
      if (!current.signal.aborted) setPreview({ id: line.id, url: URL.createObjectURL(blob) });
    } catch (cause) {
      if (!current.signal.aborted) setPreviewError(describeError(cause));
    } finally {
      if (controller.current === current) {
        controller.current = null;
        setPreviewing(null);
        onBusy?.(false);
      }
    }
  };
  const script = draft.importText;
  const setScript = (importText: string) => onChange({ importText });
  const [preset, setPreset] = useState<SplitMode>(DEFAULT_SPLIT_MODE);
  const [charLimit, setCharLimit] = useState(500);
  const [inputOpen, setInputOpen] = useState(Boolean(script));
  useEffect(() => {
    if (script) setInputOpen(true);
  }, [script]);
  const [notice, setNotice] = useState('');
  const autoCast = () => {
    const result = buildAutoCast(script, draft.cast, profiles);
    if (!result.tracks.length) {
      setNotice(t('stories.autocastEmpty'));
      return;
    }
    onChange({
      cast: result.cast,
      lines: [
        ...draft.lines,
        ...result.tracks.map((line) => ({
          ...line,
          id: crypto.randomUUID(),
          profileId: null,
        })),
      ],
    });
    setScript('');
    setNotice(
      t('stories.autocastDone', {
        lines: result.tracks.length,
        count: result.speakers.length,
      }),
    );
  };
  const update = (id: string, patch: Partial<Draft['lines'][number]>) =>
    onChange({
      lines: draft.lines.map((line) => (line.id === id ? { ...line, ...patch } : line)),
    });
  const insertIntoLine = (line: Draft['lines'][number], token: string) => {
    const input = lineInputs.current.get(line.id);
    const caret = input?.selectionStart ?? line.text.length;
    const text = insertStoryToken(line.text, caret, token);
    update(line.id, { text });
    requestAnimationFrame(() => {
      const next = lineInputs.current.get(line.id);
      if (!next) return;
      const tokenStart = text.indexOf(token, Math.max(0, caret - 1));
      const nextCaret = tokenStart < 0 ? text.length : tokenStart + token.length;
      next.focus();
      next.setSelectionRange(nextCaret, nextCaret);
    });
  };
  const setInlineVoice = (line: Draft['lines'][number], profileId: string) => {
    const input = lineInputs.current.get(line.id);
    const start = input?.selectionStart ?? line.text.length;
    const end = input?.selectionEnd ?? start;
    const text = applyInlineVoice(line.text, start, end, profileId);
    update(line.id, { text });
    requestAnimationFrame(() => {
      const next = lineInputs.current.get(line.id);
      if (!next) return;
      const nextCaret = start + `[voice:${profileId}]`.length + (end - start);
      next.focus();
      next.setSelectionRange(nextCaret, nextCaret);
    });
  };
  const add = (text = '') =>
    onChange({
      lines: [...draft.lines, { id: crypto.randomUUID(), text, profileId: null }],
    });
  const loadSample = () =>
    onChange({
      title: SAMPLE_STORY_NAME,
      projectId: null,
      cast: SAMPLE_STORY_CAST.map((character, index) => ({
        id: character.id,
        name: character.name,
        profileId: profiles.length ? profiles[index % profiles.length].id : null,
      })),
      lines: SAMPLE_STORY_LINES.map((line) => ({
        id: crypto.randomUUID(),
        character: line.character,
        text: line.text,
        profileId: null,
      })),
    });
  return (
    // Deliberately not min-h-0: the page column is what scrolls. A root that could
    // shrink would let a tall script spill over the pinned generate controls.
    <div data-slot="story-editor" className="space-y-4 flex-1">
      {previewError && (
        <PipelineFailure fallback={previewError} onDismiss={() => setPreviewError(null)} />
      )}
      <details
        className="group rounded-xl border border-border/60 bg-muted/20 p-3"
        open={inputOpen}
        onToggle={(event) => setInputOpen(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium">
          <ChevronRightIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-90" />
          {t('stories.autocast')}
        </summary>
        <div className="mt-3 space-y-3">
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t('stories.autocastHint')}
          </p>
          <textarea
            dir="auto"
            aria-label={t('stories.autocast')}
            placeholder={t('stories.splitPlaceholder')}
            className="min-h-32 w-full rounded-lg border border-border/60 bg-background/40 p-3 text-sm outline-none focus-visible:ring-1 focus-visible:ring-ring"
            value={script}
            disabled={disabled}
            onChange={(e) => setScript(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={disabled || !script.trim()}
              onClick={autoCast}
            >
              {t('stories.autocast')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled || !script.trim()}
              onClick={() => {
                const parts = splitStoryText(script, preset, charLimit);
                onChange({
                  lines: [
                    ...draft.lines,
                    ...parts.map((text) => ({
                      id: crypto.randomUUID(),
                      text,
                      profileId: null,
                    })),
                  ],
                  importText: '',
                });
                setNotice(t('stories.lines', { count: parts.length }));
              }}
            >
              {t('stories.splitIntoTracks')}
            </Button>
            <SplitPresetPicker
              preset={preset}
              onPreset={setPreset}
              charLimit={charLimit}
              onCharLimit={setCharLimit}
              disabled={disabled}
            />
          </div>
          <p className="text-xs text-muted-foreground">{t('stories.splitModeHint')}</p>
          {notice && (
            <p role="status" className="text-xs text-muted-foreground">
              {notice}
            </p>
          )}
        </div>
      </details>

      {draft.lines.length === 0 && (
        <div className="flex min-h-72 flex-col items-center justify-center rounded-2xl border border-dashed border-border/60 px-6 text-center">
          <div className="mb-4 flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <BookOpenTextIcon className="size-5" />
          </div>
          <h3 className="text-sm font-semibold">{t('stories.newStory')}</h3>
          <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
            {t('stories.emptyText')}
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            <Button
              variant="secondary"
              disabled={disabled}
              title={t('audiobook.load_sample_hint')}
              onClick={loadSample}
            >
              <SparklesIcon />
              {t('audiobook.load_sample')}
            </Button>
            <Button variant="outline" disabled={disabled} onClick={() => add()}>
              <PlusIcon />
              {t('stories.addFirst')}
            </Button>
          </div>
        </div>
      )}

      {draft.lines.length > 0 && (
        <div data-slot="story-script" className="space-y-1">
          {draft.lines.map((line, index) => (
            <StoryBlock
              key={line.id}
              line={line}
              index={index}
              count={draft.lines.length}
              draft={draft}
              profiles={profiles}
              disabled={disabled}
              canPreview={canSynthesize && storyVoicesReady({ ...draft, lines: [line] }, profiles)}
              previewing={previewing === line.id}
              previewUrl={preview?.id === line.id ? preview.url : null}
              inputRef={(node) => {
                if (node) lineInputs.current.set(line.id, node);
                else lineInputs.current.delete(line.id);
              }}
              onUpdate={(patch) => update(line.id, patch)}
              onPreview={() => void audition(line)}
              onStopPreview={() => controller.current?.abort()}
              onInsert={(token) => insertIntoLine(line, token)}
              onInlineVoice={(profileId) => setInlineVoice(line, profileId)}
              onMove={(direction) =>
                onChange({
                  lines:
                    direction < 0
                      ? reorder(draft.lines, line.id, draft.lines[index - 1].id)
                      : reorder(draft.lines, draft.lines[index + 1].id, line.id),
                })
              }
              onRemove={() => onChange({ lines: draft.lines.filter((l) => l.id !== line.id) })}
            />
          ))}
        </div>
      )}
      {draft.lines.length > 0 && (
        <div className="flex gap-2">
          <Button variant="ghost" disabled={disabled} onClick={() => add()}>
            <PlusIcon />
            {t('stories.addLine')}
          </Button>
          <Button
            variant="ghost"
            disabled={disabled}
            onClick={() =>
              add(
                '# ' +
                  t('stories.chapterN', {
                    n: draft.lines.filter((line) => line.text.startsWith('# ')).length + 1,
                  }),
              )
            }
          >
            {t('stories.addChapter')}
          </Button>
        </div>
      )}
      <StoryStems
        draft={draft}
        profiles={profiles}
        disabled={disabled || !canSynthesize}
        onBusy={onBusy}
      />
    </div>
  );
}
