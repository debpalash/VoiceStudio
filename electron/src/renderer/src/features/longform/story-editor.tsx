import { StoryStems } from './story-stems';
import { WaveformPlayer } from '@/components/waveform-player';
import { previewStoryLine } from './story-preview';
import { storyVoicesReady } from './story-inputs';
import {
  DEFAULT_SPLIT_MODE,
  SPLIT_MODES,
  splitStoryText,
  type SplitMode,
} from '../../../../../../frontend/src/utils/splitStoryText';
import { useEffect, useRef, useState } from 'react';
import { buildAutoCast } from '../../../../../../frontend/src/utils/autoCast';
import {
  SAMPLE_STORY_CAST,
  SAMPLE_STORY_LINES,
  SAMPLE_STORY_NAME,
} from '../../../../../../frontend/src/data/sampleStory';
import { useTranslation } from 'react-i18next';
import {
  ArrowDownIcon,
  ArrowUpIcon,
  BookOpenTextIcon,
  ChevronRightIcon,
  CircleCheckIcon,
  CircleQuestionMarkIcon,
  LaughIcon,
  AnnoyedIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  TrashIcon,
  UsersIcon,
  WindIcon,
  ZapIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { PipelineFailure } from '@/components/pipeline-failure';
import { describeError } from '@/lib/api/client';
import { reorder } from '../../../../../../frontend/src/utils/storyReorder';
import type { Draft } from './longform-session';
interface Props {
  draft: Draft;
  profiles: { id: string; name: string }[];
  disabled: boolean;
  canSynthesize?: boolean;
  onChange: (patch: Partial<Draft>) => void;
  onBusy?: (busy: boolean) => void;
}

const STORY_TONES = [
  { tag: '[laughter]', icon: LaughIcon, key: 'laugh' },
  { tag: '[sigh]', icon: WindIcon, key: 'sigh' },
  { tag: '[question-en]', icon: CircleQuestionMarkIcon, key: 'question' },
  { tag: '[surprise-wa]', icon: ZapIcon, key: 'surprise' },
  { tag: '[confirmation-en]', icon: CircleCheckIcon, key: 'confirm' },
  { tag: '[dissatisfaction-hnn]', icon: AnnoyedIcon, key: 'dissatisfaction' },
] as const;

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
export function StoryCast({ draft, profiles, disabled, onChange }: Props) {
  const { t } = useTranslation();
  return (
    <details className="space-y-3">
      <summary className="cursor-pointer text-sm font-medium">{t('stories.castTitle')}</summary>
      {draft.cast.map((character) => (
        <div key={character.id} className="space-y-2 border-b border-border/40 pb-3">
          <div className="flex gap-1">
            <Input
              aria-label={t('stories.characterName')}
              value={character.name}
              disabled={disabled}
              onChange={(e) =>
                onChange({
                  cast: draft.cast.map((c) =>
                    c.id === character.id ? { ...c, name: e.target.value } : c,
                  ),
                })
              }
            />
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t('stories.removeCharacter')}
              disabled={disabled}
              onClick={() =>
                onChange({
                  cast: draft.cast.filter((c) => c.id !== character.id),
                  lines: draft.lines.map((line) =>
                    line.character === character.id ? { ...line, character: undefined } : line,
                  ),
                })
              }
            >
              <TrashIcon />
            </Button>
          </div>
          <details>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              {profiles.find((p) => p.id === character.profileId)?.name ||
                t('stories.defaultVoice')}
            </summary>
            <div className="max-h-40 overflow-y-auto">
              {[{ id: null, name: t('stories.defaultVoice') }, ...profiles].map((profile) => (
                <Button
                  key={profile.id || 'default'}
                  variant={character.profileId === profile.id ? 'secondary' : 'ghost'}
                  size="sm"
                  className="w-full justify-start"
                  disabled={disabled}
                  onClick={() =>
                    onChange({
                      cast: draft.cast.map((c) =>
                        c.id === character.id ? { ...c, profileId: profile.id } : c,
                      ),
                    })
                  }
                >
                  {profile.name}
                </Button>
              ))}
            </div>
          </details>
        </div>
      ))}
      <Button
        variant="ghost"
        size="sm"
        disabled={disabled}
        onClick={() =>
          onChange({
            cast: [
              ...draft.cast,
              {
                id: crypto.randomUUID(),
                name: t('stories.character'),
                profileId: null,
              },
            ],
          })
        }
      >
        <PlusIcon />
        {t('stories.addCharacter')}
      </Button>
    </details>
  );
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
  const [splitMode, setSplitMode] = useState<SplitMode>(DEFAULT_SPLIT_MODE);
  // Electron's Sentences preset keeps its established 500-char ceiling.
  const [maximum, setMaximum] = useState(500);
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
        voices: result.speakers.length,
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
    // No `min-h-0` here: the page column is the scroll container, and a
    // shrinkable flex item let a long script paint over the generation
    // progress panel and the footer instead of pushing them down.
    <div data-slot="story-editor" className="flex-1 space-y-4">
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
                const parts = splitStoryText(script, splitMode, maximum);
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
            <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
              {t('stories.splitMode')}
              <Select
                items={SPLIT_MODES.map((mode) => ({
                  value: mode,
                  label: t(`stories.split_${mode}`),
                }))}
                value={splitMode}
                disabled={disabled}
                onValueChange={(value) => setSplitMode(value as SplitMode)}
              >
                <SelectTrigger aria-label={t('stories.splitMode')} className="h-8 min-w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  {SPLIT_MODES.map((mode) => (
                    <SelectItem key={mode} value={mode}>
                      {t(`stories.split_${mode}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            {splitMode === 'sentences' && (
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                {t('stories.maxChars')}
                <Input
                  type="number"
                  min="40"
                  max="2000"
                  step="10"
                  className="h-8 w-20"
                  disabled={disabled}
                  value={maximum}
                  onChange={(e) =>
                    setMaximum(Math.max(40, Math.min(2000, Number(e.target.value) || 40)))
                  }
                />
              </label>
            )}
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

      {draft.lines.map((line, index) => (
        <div key={line.id} className="space-y-3 rounded-xl border border-border/60 bg-muted/15 p-4">
          <div className="flex items-start gap-3">
            <textarea
              ref={(node) => {
                if (node) lineInputs.current.set(line.id, node);
                else lineInputs.current.delete(line.id);
              }}
              aria-label={t('stories.linePlaceholder')}
              className="min-h-24 min-w-0 flex-1 resize-y bg-transparent text-sm leading-6 outline-none"
              value={line.text}
              disabled={disabled}
              onChange={(e) => update(line.id, { text: e.target.value })}
            />
            <div className="flex flex-col">
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('stories.preview')}
                disabled={
                  disabled ||
                  !canSynthesize ||
                  !storyVoicesReady({ ...draft, lines: [line] }, profiles)
                }
                onClick={() => void audition(line)}
              >
                <PlayIcon />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('stories.insertPause')}
                title={t('stories.insertPause')}
                disabled={disabled}
                onClick={() => insertIntoLine(line, '[pause 0.5s]')}
              >
                <PauseIcon />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('stories.moveUp')}
                disabled={disabled || index === 0}
                onClick={() =>
                  onChange({
                    lines: reorder(draft.lines, line.id, draft.lines[index - 1].id),
                  })
                }
              >
                <ArrowUpIcon />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('stories.moveDown')}
                disabled={disabled || index === draft.lines.length - 1}
                onClick={() =>
                  onChange({
                    lines: reorder(draft.lines, draft.lines[index + 1].id, line.id),
                  })
                }
              >
                <ArrowDownIcon />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('stories.removeLine')}
                disabled={disabled}
                onClick={() =>
                  onChange({
                    lines: draft.lines.filter((l) => l.id !== line.id),
                  })
                }
              >
                <TrashIcon />
              </Button>
            </div>
          </div>
          {previewing === line.id && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span role="status">{t('common.loading')}</span>
              <Button size="sm" variant="ghost" onClick={() => controller.current?.abort()}>
                {t('common.stop')}
              </Button>
            </div>
          )}
          {preview?.id === line.id && (
            <WaveformPlayer showWaveform={false} src={preview.url} source="story-line-preview" />
          )}
          <details>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              {draft.cast.find((c) => c.id === line.character)?.name || t('stories.defaultVoice')}
            </summary>
            <div className="mt-2 flex flex-wrap gap-1">
              {[{ id: '', name: t('stories.defaultVoice') }, ...draft.cast].map((character) => (
                <Button
                  key={character.id}
                  size="sm"
                  variant={line.character === character.id ? 'secondary' : 'ghost'}
                  disabled={disabled}
                  onClick={() => update(line.id, { character: character.id || undefined })}
                >
                  {character.name}
                </Button>
              ))}
            </div>
          </details>
          <details>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              {t('stories.voice')} ?{' '}
              {profiles.find((p) => p.id === line.profileId)?.name || t('stories.defaultVoice')}
            </summary>
            <div className="mt-2 flex flex-wrap gap-1">
              {[{ id: null, name: t('stories.defaultVoice') }, ...profiles].map((profile) => (
                <Button
                  key={profile.id || 'default'}
                  size="sm"
                  variant={line.profileId === profile.id ? 'secondary' : 'ghost'}
                  disabled={disabled}
                  onClick={() => update(line.id, { profileId: profile.id })}
                >
                  {profile.name}
                </Button>
              ))}
            </div>
          </details>
          <details>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <UsersIcon className="size-3.5" />
                {t('stories.inlineVoice')}
              </span>
            </summary>
            <p className="mt-2 text-xs text-muted-foreground">{t('stories.inlineVoiceHint')}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {profiles.map((profile) => (
                <Button
                  key={profile.id}
                  size="xs"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => setInlineVoice(line, profile.id)}
                >
                  {profile.name}
                </Button>
              ))}
              <Button
                size="xs"
                variant="ghost"
                disabled={disabled}
                onClick={() => setInlineVoice(line, 'default')}
              >
                {t('stories.resetInlineVoice')}
              </Button>
            </div>
          </details>
          <details>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <SlidersHorizontalIcon className="size-3.5" />
                {t('stories.tune')} · {(line.speed ?? draft.globalSpeed).toFixed(2)}×
              </span>
            </summary>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {STORY_TONES.map((tone) => {
                const Icon = tone.icon;
                return (
                  <Button
                    key={tone.tag}
                    size="xs"
                    variant="outline"
                    disabled={disabled}
                    title={tone.tag}
                    onClick={() => insertIntoLine(line, tone.tag)}
                  >
                    <Icon />
                    {t(`stories.tones.${tone.key}`)}
                  </Button>
                );
              })}
            </div>
            <input
              className="mt-3 w-full accent-primary"
              aria-label={t('stories.speed')}
              type="range"
              min="0.5"
              max="2"
              step="0.05"
              value={line.speed ?? draft.globalSpeed}
              disabled={disabled}
              onChange={(e) => update(line.id, { speed: Number(e.target.value) })}
            />
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => update(line.id, { speed: null })}
            >
              {t('stories.reset')}
            </Button>
          </details>
        </div>
      ))}
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
