import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  AudioLinesIcon,
  DownloadIcon,
  GaugeIcon,
  LibraryIcon,
  PlayIcon,
  RefreshCwIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { generateClone } from '@/lib/api/generate';
import { apiFetch, audioUrl, describeError } from '@/lib/api/client';
import type { GenerateResult, Profile } from '@/lib/api/types';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { DEFAULT_CLONE_SETTINGS } from '@/lib/store/clone-settings';
import { saveExport } from '@/lib/export-history';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { useEngines } from '@/hooks/use-engines';
import { useProfiles } from '@/hooks/use-profiles';
import { WaveformPlayer } from '@/components/waveform-player';
import { cn } from '@/lib/utils';

type JobType = 'Station ID' | 'Stinger' | 'Sweeper' | 'Promo' | 'News';
type StylePreset = 'Natural' | 'Punchy' | 'Energetic' | 'News';
type Pace = 'Normal' | 'Fast' | 'Slow';
type TakeId = 'A' | 'B' | 'C';

interface RadioTake {
  id: TakeId;
  result: GenerateResult;
  objectUrl: string;
}

const JOB_TYPES: JobType[] = ['Station ID', 'Stinger', 'Sweeper', 'Promo', 'News'];
const STYLE_PRESETS: StylePreset[] = ['Natural', 'Punchy', 'Energetic', 'News'];
const DURATIONS = [3, 5, 7, 10, 15, 30] as const;
const TAKE_IDS: TakeId[] = ['A', 'B', 'C'];

const QUICK_INSERTS = [
  { label: 'NAS FM', text: 'ناس إف إم' },
  { label: '98.7', text: '98.7' },
  { label: 'Slogan', text: 'ناس تسمع ناس' },
  { label: 'Pause', text: ' [pause 350ms] ' },
] as const;

const STYLE_PROMPTS: Record<StylePreset, string> = {
  Natural: 'natural Iraqi radio delivery, confident, clean, conversational',
  Punchy: 'punchy Iraqi radio imaging delivery, compact, confident, high impact',
  Energetic: 'energetic Iraqi radio promo delivery, bright, confident, fast-moving',
  News: 'neutral Iraqi broadcast news delivery, clear, controlled, authoritative',
};

const PACE_SPEED: Record<Pace, number> = {
  Normal: 1,
  Fast: 1.08,
  Slow: 0.92,
};

function profileRank(profile: Profile): number {
  const name = profile.name.toLowerCase();
  if (name.includes('nas') && name.includes('news')) return 0;
  if (name.includes('nas')) return 1;
  return 2;
}

function formatSeconds(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return value < 10 ? value.toFixed(1) + 's' : Math.round(value) + 's';
}

function safeFilePart(value: string): string {
  return value
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
}

export function RadioStudioPage() {
  const navigate = useNavigate();
  const backend = useBackendStatus();
  const engines = useEngines();
  const profilesQuery = useProfiles();
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [jobType, setJobType] = useState<JobType>('Stinger');
  const [stylePreset, setStylePreset] = useState<StylePreset>('Punchy');
  const [pace, setPace] = useState<Pace>('Normal');
  const [energy, setEnergy] = useState(72);
  const [duration, setDuration] = useState<number>(5);
  const [script, setScript] = useState('');
  const [takes, setTakes] = useState<RadioTake[]>([]);
  const [selectedTakeId, setSelectedTakeId] = useState<TakeId>('A');
  const [isGenerating, setIsGenerating] = useState(false);
  const [generationLabel, setGenerationLabel] = useState('Ready');
  const abortRef = useRef<AbortController | null>(null);
  const playbackRef = useRef<HTMLAudioElement | null>(null);

  const voices = useMemo(
    () =>
      (profilesQuery.data ?? [])
        .filter((profile) => profile.kind === 'clone' && Boolean(profile.ref_audio_path))
        .slice()
        .sort((a, b) => profileRank(a) - profileRank(b) || a.name.localeCompare(b.name)),
    [profilesQuery.data],
  );

  const selectedProfile =
    voices.find((profile) => profile.id === selectedProfileId) ?? voices[0] ?? null;

  const selectedTake = takes.find((take) => take.id === selectedTakeId) ?? takes[0] ?? null;

  useEffect(() => {
    if (!selectedProfileId && voices[0]) setSelectedProfileId(voices[0].id);
  }, [selectedProfileId, voices]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      playbackRef.current?.pause();
      takes.forEach((take) => URL.revokeObjectURL(take.objectUrl));
    },
    [takes],
  );

  useEffect(() => {
    if (jobType !== 'News') return;
    setStylePreset('News');
    setPace('Normal');
    setEnergy(52);
    const news = voices.find((profile) => profile.name.toLowerCase().includes('news'));
    if (news) setSelectedProfileId(news.id);
  }, [jobType, voices]);

  const charCount = script.length;
  const estimatedSeconds = Math.max(1.2, charCount / 13.5);
  const engineLabel =
    backend.stage === 'ready'
      ? engines.activeTts?.display_name ?? 'Voice Engine Ready'
      : backend.stage === 'error'
        ? 'Engine Error'
        : 'Starting Engine';

  const previewProfile = async (profile: Profile) => {
    try {
      playbackRef.current?.pause();
      const response = await apiFetch('/profiles/' + encodeURIComponent(profile.id) + '/audio');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      playbackRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      audio.onerror = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch (error) {
      toast.error(describeError(error));
    }
  };

  const playTake = async (take: RadioTake) => {
    try {
      playbackRef.current?.pause();
      const audio = new Audio(take.objectUrl);
      playbackRef.current = audio;
      await audio.play();
    } catch (error) {
      toast.error(describeError(error));
    }
  };

  const buildInstruct = (profile: Profile, style: StylePreset): string => {
    const vocabulary = engines.activeTts?.instruct_vocabulary ?? 'tags';
    if (vocabulary === 'freeform') {
      return [profile.instruct?.trim(), STYLE_PROMPTS[style]].filter(Boolean).join(', ');
    }
    return profile.instruct ?? '';
  };

  const generateThree = async (reason = 'Generated', styleOverride?: StylePreset) => {
    if (!selectedProfile) {
      toast.error('Add or select a saved voice first.');
      return;
    }
    if (!script.trim()) {
      toast.error('Enter the radio script first.');
      return;
    }
    if (!engines.activeTtsReady) {
      toast.error('The selected TTS engine is not ready.');
      return;
    }
    const release = acquireSynthesis();
    if (!release) {
      toast.info('Another synthesis is already running.');
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setIsGenerating(true);
    setGenerationLabel('Preparing takes');

    const previous = takes;
    const next: RadioTake[] = [];
    const effectiveStyle = styleOverride ?? stylePreset;
    const baseSeed = selectedProfile.seed ?? ((Date.now() & 0x7fffffff) || 1);

    try {
      for (let index = 0; index < TAKE_IDS.length; index += 1) {
        const id = TAKE_IDS[index];
        setGenerationLabel('Rendering Take ' + id + ' · ' + (index + 1) + '/3');
        const seed = (baseSeed + index * 7919) & 0x7fffffff;
        const result = await generateClone(
          {
            text: script.trim(),
            language:
              selectedProfile.language && selectedProfile.language !== 'Auto'
                ? selectedProfile.language
                : 'Arabic',
            profileId: selectedProfile.id,
            instruct: buildInstruct(selectedProfile, effectiveStyle),
            instructVocabulary: engines.activeTts?.instruct_vocabulary,
            seed,
            steps: Math.max(DEFAULT_CLONE_SETTINGS.steps, 24),
            wavBits: 24,
            effectPreset: 'broadcast',
            cfg: 1.45 + (energy / 100) * 1.05,
            speed: PACE_SPEED[pace],
            tShift: DEFAULT_CLONE_SETTINGS.tShift,
            posTemp: DEFAULT_CLONE_SETTINGS.posTemp,
            classTemp: DEFAULT_CLONE_SETTINGS.classTemp,
            layerPenalty: DEFAULT_CLONE_SETTINGS.layerPenalty,
            denoise: true,
            postprocess: true,
            duration: String(duration),
          },
          { signal: controller.signal },
        );
        next.push({ id, result, objectUrl: URL.createObjectURL(result.blob) });
      }
      previous.forEach((take) => URL.revokeObjectURL(take.objectUrl));
      setTakes(next);
      setSelectedTakeId('A');
      setGenerationLabel(reason);
      toast.success(reason + ' · 3 takes ready');
    } catch (error) {
      next.forEach((take) => URL.revokeObjectURL(take.objectUrl));
      if (!controller.signal.aborted) toast.error(describeError(error));
      setGenerationLabel(controller.signal.aborted ? 'Cancelled' : 'Generation failed');
    } finally {
      abortRef.current = null;
      setIsGenerating(false);
      release();
    }
  };

  const exportSelected = async () => {
    if (!selectedTake) {
      toast.info('Generate a take first.');
      return;
    }
    const stem = [
      'NAS',
      safeFilePart(jobType),
      safeFilePart(selectedProfile?.name ?? 'voice'),
      selectedTake.id,
    ]
      .filter(Boolean)
      .join('_');
    try {
      const source = selectedTake.result.audioPath
        ? audioUrl(selectedTake.result.audioPath)
        : selectedTake.objectUrl;
      const saved = await saveExport(source, stem + '.wav');
      if (saved && !saved.canceled) toast.success('WAV exported');
    } catch (error) {
      toast.error(describeError(error));
    }
  };

  const changeStyleAndGenerate = () => {
    const index = STYLE_PRESETS.indexOf(stylePreset);
    const next = STYLE_PRESETS[(index + 1) % STYLE_PRESETS.length];
    setStylePreset(next);
    void generateThree('Style changed', next);
  };

  const insertQuickText = (text: string) => {
    setScript((current) => {
      if (!current.trim()) return text;
      return current.endsWith(' ') ? current + text : current + ' ' + text;
    });
  };

  return (
    <div
      className="h-full min-h-0 overflow-hidden bg-[#07131f] text-[#f5f7fb]"
      dir="ltr"
      data-testid="nas-radio-studio"
    >
      <div className="grid h-full min-h-0 grid-rows-[70px_minmax(0,1fr)] gap-3 p-4">
        <header className="flex items-center justify-between rounded-2xl border border-white/8 bg-[linear-gradient(180deg,rgba(18,38,58,.96),rgba(10,25,39,.96))] px-5 shadow-2xl shadow-black/20">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-14 place-items-center rounded-xl bg-[linear-gradient(145deg,#ff2d37,#b9000a)] text-sm font-black text-white shadow-lg shadow-red-950/30">
              NAS
            </div>
            <div className="text-left leading-tight">
              <div className="text-lg font-bold tracking-tight">NAS VoiceStudio</div>
              <div className="mt-1 text-[11px] text-[#8ea0b5]">Radio Voice Production · 98.7 FM</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex h-9 items-center gap-2 rounded-full border border-white/8 bg-white/[.025] px-3 text-xs text-[#dbe6f2]">
              <span
                className={cn(
                  'h-2 w-2 rounded-full',
                  backend.stage === 'ready' && engines.activeTtsReady
                    ? 'bg-emerald-400 shadow-[0_0_0_4px_rgba(52,211,153,.09)]'
                    : 'bg-amber-400',
                )}
              />
              {isGenerating ? generationLabel : engineLabel}
            </div>
            <button
              type="button"
              className="grid h-9 w-9 place-items-center rounded-xl border border-white/8 bg-white/[.025] text-[#aebfd0] transition hover:bg-white/[.06] hover:text-white"
              onClick={() => navigate({ to: '/settings' })}
              aria-label="Settings"
            >
              <SettingsIcon className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="grid min-h-0 grid-cols-[330px_minmax(720px,1.55fr)_minmax(520px,.95fr)] gap-3">
          <section className="grid min-h-0 grid-rows-[54px_minmax(0,1fr)] overflow-hidden rounded-[18px] border border-white/8 bg-[linear-gradient(180deg,rgba(17,36,56,.96),rgba(11,27,42,.98))] shadow-2xl shadow-black/20">
            <PanelHeader step="1" title="Voice" subtitle="Fixed station voices" />
            <div className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-3 overflow-hidden p-3.5" dir="rtl">
              <div className="text-[10px] font-semibold tracking-[.16em] text-[#65788e]">NAS VOICES</div>
              <div className="grid content-start gap-2 overflow-hidden">
                {voices.length ? (
                  voices.slice(0, 6).map((profile) => {
                    const active = profile.id === selectedProfile?.id;
                    return (
                      <div
                        key={profile.id}
                        className={cn(
                          'grid min-h-[70px] grid-cols-[1fr_40px] items-center gap-2 rounded-[14px] border px-3 text-right transition',
                          active
                            ? 'border-red-500/55 bg-[linear-gradient(90deg,rgba(227,6,19,.12),rgba(255,255,255,.025))] shadow-[inset_-3px_0_0_#e30613]'
                            : 'border-white/8 bg-white/[.02] hover:border-white/15 hover:bg-white/[.045]',
                        )}
                      >
                        <button
                          type="button"
                          className="grid min-w-0 gap-1 text-right"
                          onClick={() => setSelectedProfileId(profile.id)}
                        >
                          <span className="truncate text-sm font-bold">{profile.name}</span>
                          <span className="text-[11px] text-[#8ea0b5]">
                            {profile.name.toLowerCase().includes('news') ? 'News' : 'Imaging'} · Fixed
                          </span>
                        </button>
                        <button
                          type="button"
                          className="grid h-9 w-9 place-items-center rounded-xl border border-white/8 bg-white/[.035] text-[#dbe6f2] hover:border-red-500/40 hover:bg-red-500/10"
                          onClick={() => void previewProfile(profile)}
                          aria-label={'Preview ' + profile.name}
                        >
                          <PlayIcon className="h-3.5 w-3.5 fill-current" />
                        </button>
                      </div>
                    );
                  })
                ) : (
                  <div className="grid min-h-40 place-items-center rounded-2xl border border-dashed border-white/10 bg-white/[.02] px-5 text-center">
                    <div>
                      <AudioLinesIcon className="mx-auto mb-3 h-6 w-6 text-[#65788e]" />
                      <div className="text-sm font-semibold">No saved voices yet</div>
                      <div className="mt-1 text-xs leading-5 text-[#8ea0b5]">
                        Add an Iraqi reference voice first, then return here.
                      </div>
                    </div>
                  </div>
                )}
              </div>
              <button
                type="button"
                className="flex h-10 items-center justify-center gap-2 rounded-xl border border-dashed border-white/12 bg-white/[.02] text-xs font-semibold text-[#c6d2df] hover:bg-white/[.05]"
                onClick={() => navigate({ to: '/personas' })}
              >
                <LibraryIcon className="h-4 w-4" />
                Voice Library
              </button>
            </div>
          </section>

          <section className="grid min-h-0 grid-rows-[54px_minmax(0,1fr)] overflow-hidden rounded-[18px] border border-white/8 bg-[linear-gradient(180deg,rgba(17,36,56,.96),rgba(11,27,42,.98))] shadow-2xl shadow-black/20">
            <PanelHeader step="2" title="Script & Style" subtitle="Write · direct · generate" />
            <div className="grid min-h-0 grid-rows-[auto_auto_minmax(0,1fr)_auto_auto_auto] gap-2.5 overflow-hidden p-3.5" dir="rtl">
              <div className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 text-[11px] text-[#8ea0b5]">Type</span>
                <div className="flex min-w-0 items-center gap-1.5">
                  {JOB_TYPES.map((item) => (
                    <Chip key={item} active={jobType === item} onClick={() => setJobType(item)}>
                      {item}
                    </Chip>
                  ))}
                </div>
              </div>

              <div className="flex min-w-0 items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="shrink-0 text-[11px] text-[#8ea0b5]">Style</span>
                  <div className="flex min-w-0 items-center gap-1.5">
                    {STYLE_PRESETS.map((item) => (
                      <Chip
                        key={item}
                        active={stylePreset === item}
                        onClick={() => setStylePreset(item)}
                      >
                        {item}
                      </Chip>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-[#8ea0b5]">Pace</span>
                  <select
                    className="h-8 rounded-lg border border-white/8 bg-[#0b1c2a] px-3 text-xs text-[#dbe6f2] outline-none"
                    value={pace}
                    onChange={(event) => setPace(event.target.value as Pace)}
                  >
                    <option>Normal</option>
                    <option>Fast</option>
                    <option>Slow</option>
                  </select>
                </div>
              </div>

              <div className="relative min-h-0 overflow-hidden rounded-2xl border border-white/12 bg-[linear-gradient(180deg,#0a1a28,#091722)] shadow-inner">
                <textarea
                  className="h-full w-full resize-none bg-transparent px-5 pt-5 pb-14 text-right text-[21px] leading-[1.85] text-white outline-none placeholder:text-[#52677a]"
                  value={script}
                  onChange={(event) => setScript(event.target.value)}
                  placeholder="اكتب النص العراقي هنا..."
                  dir="rtl"
                  data-testid="radio-script"
                />
                <div className="absolute inset-x-3 bottom-2.5 flex items-center gap-1.5 border-t border-white/[.055] pt-2">
                  {QUICK_INSERTS.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      className="h-7 rounded-full border border-white/8 bg-white/[.03] px-2.5 text-[10px] text-[#aebfd0] hover:bg-white/[.06] hover:text-white"
                      onClick={() => insertQuickText(item.text)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-[auto_minmax(180px,1fr)_44px_auto_130px] items-center gap-2.5">
                <span className="text-[11px] text-[#8ea0b5]">Energy</span>
                <input
                  className="w-full accent-[#e30613]"
                  type="range"
                  min="0"
                  max="100"
                  value={energy}
                  onChange={(event) => setEnergy(Number(event.target.value))}
                />
                <span className="text-center text-xs tabular-nums text-[#dbe6f2]">{energy}%</span>
                <GaugeIcon className="h-4 w-4 text-[#65788e]" />
                <span className="text-left text-[11px] text-[#65788e]">Iraqi · Broadcast</span>
              </div>

              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-1.5">
                  {DURATIONS.map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={cn(
                        'h-8 min-w-11 rounded-lg border px-2 text-xs tabular-nums transition',
                        duration === item
                          ? 'border-red-500/60 bg-red-500/15 text-white'
                          : 'border-white/8 bg-white/[.02] text-[#c9d5e1] hover:bg-white/[.05]',
                      )}
                      onClick={() => setDuration(item)}
                    >
                      {item}s
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-3 text-[10px] tabular-nums text-[#8ea0b5]">
                  <span>{charCount} chars</span>
                  <span>{estimatedSeconds.toFixed(1)}s estimate</span>
                  <span>Target {duration}s</span>
                </div>
              </div>

              <div className="flex items-center justify-between gap-3">
                <button
                  type="button"
                  className="flex h-8 items-center gap-2 rounded-lg border border-white/8 bg-white/[.02] px-3 text-[11px] text-[#8ea0b5] hover:bg-white/[.05] hover:text-white"
                  onClick={() => navigate({ to: '/settings/models' })}
                >
                  <SlidersHorizontalIcon className="h-3.5 w-3.5" />
                  Advanced
                </button>
                <button
                  type="button"
                  className="flex h-12 min-w-56 items-center justify-center gap-2 rounded-[13px] bg-[linear-gradient(180deg,#ff2430,#e30613)] px-6 text-sm font-black text-white shadow-lg shadow-red-950/30 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={isGenerating || !selectedProfile || !script.trim() || !engines.activeTtsReady}
                  onClick={() => void generateThree('Generated')}
                >
                  {isGenerating ? (
                    <RefreshCwIcon className="h-4 w-4 animate-spin" />
                  ) : (
                    <SparklesIcon className="h-4 w-4" />
                  )}
                  {isGenerating ? generationLabel : 'Generate 3 Takes'}
                </button>
              </div>
            </div>
          </section>

          <section className="grid min-h-0 grid-rows-[54px_minmax(0,1fr)] overflow-hidden rounded-[18px] border border-white/8 bg-[linear-gradient(180deg,rgba(17,36,56,.96),rgba(11,27,42,.98))] shadow-2xl shadow-black/20">
            <PanelHeader step="3" title="Takes" subtitle="Compare & select" />
            <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto_auto] gap-2.5 overflow-hidden p-3.5" dir="rtl">
              <div className="grid min-h-0 grid-rows-[auto_42px_minmax(0,1fr)_auto] gap-2.5 overflow-hidden rounded-2xl border border-white/12 bg-[linear-gradient(180deg,#0a1a29,#091722)] p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-[#e30613] shadow-[0_0_0_4px_rgba(227,6,19,.09)]" />
                    <strong className="text-sm">Take {selectedTakeId}</strong>
                  </div>
                  <span className="text-[10px] text-[#8ea0b5]">
                    {selectedTake ? 'Ready · ' + (selectedProfile?.name ?? '') : 'Waiting'}
                  </span>
                </div>
                <div className="flex items-center gap-2.5">
                  <button
                    type="button"
                    className="grid h-9 w-10 place-items-center rounded-xl border border-white/8 bg-white/[.035] text-white disabled:opacity-35"
                    disabled={!selectedTake}
                    onClick={() => selectedTake && void playTake(selectedTake)}
                    aria-label="Play selected take"
                  >
                    <PlayIcon className="h-4 w-4 fill-current" />
                  </button>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/8">
                    <div
                      className="h-full rounded-full bg-[linear-gradient(90deg,#e30613,#ff5961)]"
                      style={{ width: selectedTake ? '42%' : '0%' }}
                    />
                  </div>
                  <span className="text-[10px] tabular-nums text-[#a9bacb]">
                    {formatSeconds(selectedTake?.result.durationSeconds ?? null)}
                  </span>
                </div>
                <div className="min-h-0 overflow-hidden rounded-xl border border-white/[.055] bg-white/[.012] p-2">
                  {selectedTake ? (
                    <WaveformPlayer
                      key={selectedTake.objectUrl}
                      src={selectedTake.objectUrl}
                      source="output"
                      height={110}
                    />
                  ) : (
                    <PlaceholderWave />
                  )}
                </div>
                <div className="text-[10px] text-[#65788e]">
                  {selectedTake
                    ? jobType + ' · ' + stylePreset + ' · ' + pace + ' · voice locked'
                    : 'Generate three takes from the same fixed voice.'}
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2">
                {TAKE_IDS.map((id) => {
                  const take = takes.find((item) => item.id === id);
                  return (
                    <button
                      key={id}
                      type="button"
                      className={cn(
                        'grid h-16 grid-cols-[34px_1fr_auto] items-center gap-2 rounded-xl border px-2.5 text-left transition',
                        selectedTakeId === id
                          ? 'border-red-500/55 bg-red-500/[.08]'
                          : 'border-white/8 bg-white/[.02] hover:bg-white/[.05]',
                      )}
                      onClick={() => setSelectedTakeId(id)}
                    >
                      <span
                        className="grid h-8 w-8 place-items-center rounded-lg border border-white/8 bg-white/[.03]"
                        onClick={(event) => {
                          if (!take) return;
                          event.stopPropagation();
                          void playTake(take);
                        }}
                      >
                        <PlayIcon className="h-3.5 w-3.5 fill-current" />
                      </span>
                      <span className="grid gap-1.5">
                        <span className="text-xs font-bold">Take {id}</span>
                        <span className="h-1 rounded-full bg-[linear-gradient(90deg,#a8b6c6_52%,rgba(255,255,255,.08)_52%)]" />
                      </span>
                      <span className="text-[10px] tabular-nums text-[#8ea0b5]">
                        {formatSeconds(take?.result.durationSeconds ?? null)}
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className="grid grid-cols-3 gap-2">
                <button
                  type="button"
                  className="flex h-10 items-center justify-center gap-2 rounded-xl border border-white/8 bg-white/[.025] text-xs font-semibold text-[#d4e0ec] hover:bg-white/[.06] disabled:opacity-40"
                  disabled={isGenerating || !selectedProfile || !script.trim()}
                  onClick={() => void generateThree('Regenerated')}
                >
                  <RefreshCwIcon className="h-3.5 w-3.5" />
                  Regenerate
                </button>
                <button
                  type="button"
                  className="flex h-10 items-center justify-center gap-2 rounded-xl border border-white/8 bg-white/[.025] text-xs font-semibold text-[#d4e0ec] hover:bg-white/[.06] disabled:opacity-40"
                  disabled={isGenerating || !selectedProfile || !script.trim()}
                  onClick={changeStyleAndGenerate}
                >
                  <SparklesIcon className="h-3.5 w-3.5" />
                  Change Style
                </button>
                <button
                  type="button"
                  className="flex h-10 items-center justify-center gap-2 rounded-xl bg-[#eef3f8] text-xs font-black text-[#09141e] hover:brightness-105 disabled:opacity-40"
                  disabled={!selectedTake}
                  onClick={() => void exportSelected()}
                >
                  <DownloadIcon className="h-3.5 w-3.5" />
                  Export WAV
                </button>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function PanelHeader({
  step,
  title,
  subtitle,
}: {
  step: string;
  title: string;
  subtitle: string;
}) {
  return (
    <header className="flex items-center justify-between border-b border-white/8 bg-white/[.012] px-4" dir="ltr">
      <div className="flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-lg border border-white/12 bg-white/[.035] text-[10px] font-black text-[#d7e1ec]">
          {step}
        </span>
        <h2 className="text-sm font-bold">{title}</h2>
      </div>
      <span className="text-[10px] text-[#8ea0b5]">{subtitle}</span>
    </header>
  );
}

function Chip({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        'h-8 rounded-full border px-3 text-[11px] font-semibold transition',
        active
          ? 'border-red-500/60 bg-red-500/15 text-white'
          : 'border-white/8 bg-white/[.025] text-[#dbe4ed] hover:bg-white/[.055]',
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function PlaceholderWave() {
  const heights = [
    20, 32, 42, 28, 54, 74, 48, 34, 62, 86, 52, 38, 72, 58, 30, 46, 82, 64, 40, 24, 52, 76,
    44, 68, 36, 56, 88, 60, 42, 70, 50, 30, 64, 78, 48, 34, 58, 82, 54, 38, 66, 74, 46, 28,
  ];
  return (
    <div className="flex h-full min-h-[110px] items-center justify-center gap-[3px] opacity-40" aria-hidden="true">
      {heights.map((height, index) => (
        <span
          key={index}
          className="w-1 flex-1 rounded-full bg-[linear-gradient(180deg,#ff6670,#e30613_48%,#7d1118)]"
          style={{ height: height + '%' }}
        />
      ))}
    </div>
  );
}
