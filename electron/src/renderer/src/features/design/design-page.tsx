import { WandSparklesIcon } from 'lucide-react';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { ProfileAvatar } from '@/components/profile-avatar';
import { DemoPresets } from './demo-presets';
import { setCloneSetting } from '@/lib/store/clone-settings';
import { PersonalityPresets } from './personality-presets';
import { EditProfile } from '@/features/clone/edit-profile';
import { WorkspacePane } from '@/components/workspace-pane';
import { TakeDetails } from '@/features/clone/take-details';
import { openTake, useSelectedTake } from '@/lib/store/takes';
import {
  DESIGN_DRAFT_EVENT,
  STORAGE,
  readDraft,
  restoreDesignProfile,
  type DesignDraft,
} from './design-draft';
import { useDescription } from './use-description';
import { useProfiles } from '@/hooks/use-profiles';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { apiJson, describeError, profileAudioUrl } from '@/lib/api/client';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { PipelineFailure } from '@/components/pipeline-failure';
import { EngineNotice } from '@/components/engine-notice';
import {
  ChevronDownIcon,
  HistoryIcon,
  LoaderCircleIcon,
  PencilIcon,
  PlayIcon,
  SaveIcon,
  Settings2Icon,
  ShuffleIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  RotateCcwIcon,
  XIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useGenerateClone } from '@/hooks/use-generate';
import { OutputPanel } from '@/features/clone/output-panel';
import { ProductionSettings } from '@/features/clone/action-bar';
import { LanguagePicker } from '@/features/clone/language-picker';
import { queryKeys } from '@/lib/query';
import { cn } from '@/lib/utils';
import { cloneSettingsStore } from '@/lib/store/clone-settings';
import type { Profile } from '@/lib/api/types';
import { CATEGORIES, PRESETS } from '../../../../../../frontend/src/utils/constants';
import {
  applyVdState,
  buildDesignInstruct,
  mergeDescribedAttrs,
} from '../../../../../../frontend/src/utils/voiceInstruct';
import { pickDesignSeed } from '../../../../../../frontend/src/utils/seed';
export function DesignPage() {
  const { t } = useTranslation();
  const selectedTake = useSelectedTake();
  const [draft, setDraft] = useState(readDraft);
  const [name, setName] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [productionOpen, setProductionOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [startingOpen, setStartingOpen] = useState(true);
  const mapper = useDescription((attrs) =>
    setDraft((current) => ({ ...current, attrs: mergeDescribedAttrs(attrs) })),
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const generation = useGenerateClone();
  const client = useQueryClient();
  const profiles = useProfiles();
  const savedProfilesRef = useRef<HTMLDetailsElement>(null);
  const designProfiles = profiles.data?.filter((profile) => profile.kind === 'design') ?? [];
  const activeProfile = designProfiles.find((profile) => profile.id === draft.profileId);
  const editingProfile = profiles.data?.find((profile) => profile.id === editingId);
  useEffect(() => {
    const restore = (event: Event) => {
      setDraft((event as CustomEvent<DesignDraft>).detail ?? readDraft());
    };
    window.addEventListener(DESIGN_DRAFT_EVENT, restore);
    return () => window.removeEventListener(DESIGN_DRAFT_EVENT, restore);
  }, []);
  useEffect(() => {
    if (selectedTake) setProductionOpen(false);
  }, [selectedTake]);
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE, JSON.stringify(draft));
      } catch {
        /* Keep the in-memory draft. */
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [draft]);
  const change = (category: string, value: string) => {
    mapper.cancel();
    const changed = applyVdState(draft.attrs, category, value);
    setDraft((current) => ({
      ...current,
      attrs:
        current.attrs === draft.attrs
          ? changed.vdStates
          : applyVdState(current.attrs, category, value).vdStates,
    }));
    if (changed.clearedCategory) {
      toast(
        t('clone.vd_exclusive_cleared', {
          cleared: t(`clone.cat_${changed.clearedCategory}`),
        }),
      );
    }
  };
  const save = async () => {
    if (!name.trim() || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const body = new FormData();
      body.set('name', name.trim());
      body.set('kind', 'design');
      body.set('seed', String(draft.seed));
      body.set('vd_states', JSON.stringify(draft.attrs));
      body.set('instruct', buildDesignInstruct(draft.attrs, '').instruct);
      body.set('language', cloneSettingsStore.state.language);
      const created = await apiJson<Profile>('/profiles', {
        method: 'POST',
        body,
      });
      await client.invalidateQueries({ queryKey: queryKeys.profiles });
      setDraft((current) => ({ ...current, profileId: created.id }));
      setCloneSetting('language', created.language || 'Auto');
      setName('');
    } catch (error) {
      setSaveError(describeError(error));
    } finally {
      setSaving(false);
    }
  };
  const generationLabel = generation.isGenerating
    ? t(
        generation.stage === 'loading'
          ? generation.modelStage
            ? `synthesisState.${generation.modelStage}`
            : 'synthesisState.loading'
          : generation.stage === 'receiving'
            ? 'synthesisState.receiving'
            : generation.stage === 'preparing'
              ? 'synthesisState.preparing'
              : 'clone.generating_status',
      )
    : t('clone.synthesize');
  const generationProgress =
    generation.stage === 'loading' ? generation.modelProgress : generation.progress;
  const identityRecipe =
    Object.values(draft.attrs)
      .filter((value) => value && value !== 'Auto')
      .join(' · ') || t('clone.identity_auto');
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('designWorkspace.title')}</h1>
      </WorkspaceHeader>
      <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t('designWorkspace.title')}
          icon={WandSparklesIcon}
          size="wide"
          variant="controls"
          className="space-y-3"
        >
          <section className="space-y-2 rounded-xl border border-border/60 bg-muted/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <label
                htmlFor="voice-description"
                className="flex min-w-0 items-center gap-2 text-sm font-medium"
              >
                <SparklesIcon className="size-4 shrink-0 text-primary" aria-hidden="true" />
                {t('clone.describe_label')}
              </label>
              {(description.trim() ||
                Object.values(draft.attrs).some((value) => value !== 'Auto')) && (
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  disabled={generation.isGenerating || mapper.pending}
                  onClick={() => mapper.reset(description)}
                >
                  <RotateCcwIcon />
                  {t('clone.reset_to_description')}
                </Button>
              )}
            </div>
            <textarea
              id="voice-description"
              maxLength={2000}
              disabled={generation.isGenerating}
              className="min-h-24 w-full resize-y rounded-lg border border-input bg-background/35 p-3 text-sm leading-5 outline-none transition-[border-color,background-color,box-shadow] focus-visible:border-primary/30 focus-visible:bg-background/50 focus-visible:ring-2 focus-visible:ring-ring/30"
              value={description}
              placeholder={t('clone.describe_placeholder')}
              onChange={(event) => {
                setDescription(event.target.value);
                mapper.describe(event.target.value);
              }}
            />
            <p role="status" className="text-xs text-muted-foreground">
              {mapper.pending
                ? t('preferences.loading')
                : !mapper.matched
                  ? t('clone.describe_no_match')
                  : mapper.unmatched.length
                    ? t('clone.describe_unmatched', {
                        items: mapper.unmatched.join(', '),
                      })
                    : t('clone.describe_hint')}
            </p>
            {mapper.failed && (
              <Button variant="ghost" size="xs" onClick={() => mapper.describe(description)}>
                {t('backend.retry')}
              </Button>
            )}
          </section>
          <details
            ref={savedProfilesRef}
            className="group rounded-xl border border-border/60 bg-muted/20 p-3 text-sm"
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 font-medium">
              {t('clone.saved_profiles')}
              <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-2 space-y-1">
              {designProfiles.map((profile) => (
                <div key={profile.id} className="flex items-center gap-1">
                  <Button
                    className="min-w-0 flex-1 justify-start truncate"
                    variant={draft.profileId === profile.id ? 'secondary' : 'ghost'}
                    size="sm"
                    aria-pressed={draft.profileId === profile.id}
                    disabled={generation.isGenerating}
                    onClick={() => {
                      mapper.cancel();
                      const restored = restoreDesignProfile(profile, draft.seed);
                      setDraft((current) => ({
                        ...current,
                        attrs: restored.attrs,
                        seed: restored.seed,
                        profileId: restored.profileId,
                      }));
                      setCloneSetting('language', restored.language);
                    }}
                  >
                    {profile.name}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t('paneActions.edit') + ': ' + profile.name}
                    onClick={() => {
                      setProductionOpen(false);
                      setEditingId(profile.id);
                    }}
                  >
                    <PencilIcon />
                  </Button>
                  <AudioPreviewButton
                    src={profileAudioUrl(profile.id, profile.audio_url)}
                    source={'design-profile-' + profile.id}
                    activity={!profile.ref_audio_path ? 'synthesis' : undefined}
                    disabled={!profile.ref_audio_path && Boolean(generation.designBlocker)}
                    disabledLabel={t('engines.none_ready_title')}
                    onReady={
                      !profile.ref_audio_path
                        ? () =>
                            void client.invalidateQueries({
                              queryKey: queryKeys.profiles,
                            })
                        : undefined
                    }
                  />
                </div>
              ))}
            </div>
          </details>
          <details
            open={startingOpen}
            onToggle={(event) => setStartingOpen(event.currentTarget.open)}
            className="group rounded-xl border border-border/60 bg-muted/20 p-3 text-sm"
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 font-medium">
              <SparklesIcon className="size-4 text-muted-foreground" />
              {t('clone.starting_points')}
              <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-3 space-y-2">
              <PersonalityPresets
                disabled={generation.isGenerating}
                attrs={draft.attrs}
                onSelect={(attrs) => {
                  mapper.cancel();
                  setDraft((current) => ({
                    ...current,
                    attrs: mergeDescribedAttrs(attrs),
                  }));
                }}
              />
              <div className="flex flex-wrap gap-1">
                {PRESETS.map((preset) => (
                  <Button
                    key={preset.id}
                    size="xs"
                    variant="ghost"
                    disabled={generation.isGenerating}
                    onClick={() => {
                      mapper.cancel();
                      setDraft((current) => ({
                        ...current,
                        attrs: mergeDescribedAttrs(preset.attrs),
                      }));
                    }}
                  >
                    {t('clone.preset_' + preset.id)
                      .replace(/[\p{Extended_Pictographic}\uFE0F]/gu, '')
                      .trim()}
                  </Button>
                ))}
              </div>
            </div>
          </details>
          <details className="group rounded-xl border border-border/60 bg-muted/20 p-3 text-sm">
            <summary className="flex cursor-pointer list-none items-center gap-2 font-medium">
              <SlidersHorizontalIcon className="size-4 text-muted-foreground" />
              {t('clone.details')}
              <span
                className="ml-auto min-w-0 truncate text-xs font-normal text-muted-foreground"
                title={identityRecipe}
              >
                {identityRecipe}
              </span>
              <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-4 space-y-4">
              {Object.entries(CATEGORIES).map(([category, values]) => (
                <fieldset key={category} disabled={generation.isGenerating} className="space-y-2">
                  <legend className="text-xs font-medium text-muted-foreground">
                    {t('clone.cat_' + category)}
                  </legend>
                  <div className="flex flex-wrap gap-1">
                    {values.map((value) => (
                      <Button
                        key={value}
                        size="xs"
                        variant={draft.attrs[category] === value ? 'secondary' : 'ghost'}
                        aria-pressed={draft.attrs[category] === value}
                        onClick={() => change(category, value)}
                      >
                        {value === 'Auto'
                          ? t('clone.auto')
                          : t('clone.opt_' + value.replaceAll(' ', '_').replaceAll('-', '_'), {
                              defaultValue: value,
                            })}
                      </Button>
                    ))}
                  </div>
                </fieldset>
              ))}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{t('clone.seed_label')}</span>
                <Input
                  className="min-w-0"
                  type="number"
                  min={0}
                  max={2147483647}
                  aria-label={t('clone.seed_label')}
                  disabled={generation.isGenerating}
                  value={draft.seed}
                  onChange={(event) => {
                    const seed = Number(event.target.value);
                    if (Number.isInteger(seed) && seed >= 0 && seed <= 2147483647)
                      setDraft((current) => ({ ...current, seed }));
                  }}
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  disabled={generation.isGenerating}
                  aria-label={t('clone.seed_reroll')}
                  onClick={() =>
                    setDraft((current) => ({
                      ...current,
                      seed: pickDesignSeed(false, null),
                    }))
                  }
                >
                  <ShuffleIcon />
                </Button>
              </div>
            </div>
          </details>
          <form
            className="space-y-2 rounded-xl border border-border/60 bg-muted/20 p-3"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-label={t('clone.profile_name')}
              placeholder={`${t('clone.profile_name')}…`}
            />
            <Button
              type="submit"
              size="sm"
              disabled={!name.trim() || saving || mapper.pending || generation.isGenerating}
            >
              <SaveIcon />
              {t('clone.save_as_profile')}
            </Button>
            {saveError && (
              <p role="alert" className="text-xs text-destructive">
                {t('clone.save_failed', { message: saveError })}
              </p>
            )}
          </form>
        </SecondarySidebar>
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col gap-4 overflow-y-auto px-6 py-6">
            <Button
              variant="ghost"
              disabled={generation.isGenerating || designProfiles.length === 0}
              aria-label={t('cloneFlow.change_voice')}
              className="h-12 w-fit max-w-full justify-start gap-2.5 px-0 hover:bg-transparent"
              onClick={() => {
                const control = savedProfilesRef.current;
                if (!control) return;
                control.open = true;
                control.scrollIntoView({
                  behavior: 'smooth',
                  block: 'nearest',
                });
                control.querySelector<HTMLElement>('button')?.focus({ preventScroll: true });
              }}
            >
              {activeProfile ? (
                <ProfileAvatar name={activeProfile.name} imageUrl={activeProfile.image_url} />
              ) : (
                <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/12 text-primary ring-1 ring-foreground/15">
                  <WandSparklesIcon className="size-4" />
                </span>
              )}
              <span className="shrink-0 font-normal text-muted-foreground">
                {t('clone.voice_kicker')} <span aria-hidden="true">·</span>
              </span>
              <span className="max-w-60 truncate font-semibold">
                {activeProfile?.name ?? t('nav.design')}
              </span>
              {designProfiles.length > 0 && <ChevronDownIcon className="text-muted-foreground" />}
            </Button>
            <DemoPresets
              initialOpen={!draft.text.trim()}
              disabled={generation.isGenerating}
              onUse={(preset) => {
                mapper.cancel();
                setDraft((current) => ({
                  ...current,
                  text: preset.script || current.text,
                  attrs: mergeDescribedAttrs(preset.attrs),
                }));
                setCloneSetting('language', preset.language || 'Auto');
              }}
            />
            <label htmlFor="design-script" className="text-sm font-medium">
              {t('clone.text_label')}
            </label>
            <textarea
              id="design-script"
              className="min-h-40 flex-1 resize-none bg-transparent text-base leading-7 outline-none"
              value={draft.text}
              placeholder={t('clone.prompt_placeholder')}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  text: event.target.value,
                }))
              }
            />
          </div>
          <div className="mx-auto w-full max-w-4xl shrink-0 px-6 pb-4">
            {generation.designBlocker === 'engine' && !generation.isGenerating && (
              <div className="mb-3">
                <EngineNotice operation="design" compact />
              </div>
            )}
            {generation.designBlocker === 'loading' && !generation.isGenerating && (
              <p className="mb-3 px-1 text-sm text-muted-foreground" role="status">
                {t('preferences.loading')}
              </p>
            )}
            {generation.error && (
              <PipelineFailure
                className="mb-3"
                fallback={generation.error}
                onDismiss={generation.clearError}
              />
            )}
            <div className="glass-panel relative grid min-h-16 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 overflow-hidden rounded-xl border border-border/60 bg-muted/30 p-3 max-md:grid-cols-[1fr_auto]">
              <div className="min-w-0 justify-self-start">
                <div className="flex items-center gap-1">
                  <LanguagePicker />
                  <Button
                    variant={productionOpen ? 'secondary' : 'ghost'}
                    size="icon-sm"
                    aria-label={t('clone.production_overrides')}
                    aria-expanded={productionOpen}
                    onClick={() => {
                      openTake(null);
                      setEditingId(null);
                      setProductionOpen((open) => !open);
                    }}
                  >
                    <Settings2Icon />
                  </Button>
                </div>
              </div>
              <span
                role={generation.isGenerating ? 'status' : undefined}
                className="min-w-24 text-center text-xs tabular-nums text-muted-foreground max-md:hidden"
              >
                {generation.isGenerating
                  ? `${generationProgress == null ? '' : `${Math.round(generationProgress)}% · `}${generation.elapsedSeconds.toFixed(1)}s`
                  : null}
              </span>
              <div className="flex w-64 justify-end gap-2 justify-self-end">
                <Button
                  className="h-10 w-52 shrink-0 overflow-hidden rounded-lg px-4"
                  disabled={!draft.text.trim() || mapper.pending || !generation.canGenerateDesign}
                  aria-busy={generation.isGenerating}
                  aria-label={generationLabel}
                  onClick={() =>
                    void generation.generateDesign({
                      text: draft.text,
                      instruct: buildDesignInstruct(draft.attrs, '').instruct,
                      seed: draft.seed,
                      profileId: profiles.data?.some(
                        (profile) => profile.id === draft.profileId && profile.kind === 'design',
                      )
                        ? draft.profileId
                        : null,
                    })
                  }
                >
                  {generation.isGenerating ? (
                    <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                  ) : (
                    <PlayIcon />
                  )}
                  <span className="truncate">{generationLabel}</span>
                </Button>
                <Button
                  size="icon-lg"
                  className={cn('size-10 shrink-0', !generation.isGenerating && 'invisible')}
                  variant="outline"
                  disabled={!generation.isGenerating}
                  onClick={generation.cancel}
                  aria-label={t('clone.cancel_generation')}
                >
                  <XIcon />
                </Button>
              </div>
              {generation.isGenerating && (
                <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-muted">
                  <div
                    className={
                      generationProgress == null
                        ? 'h-full w-full animate-pulse bg-primary motion-reduce:animate-none'
                        : 'h-full bg-primary transition-[width] duration-200'
                    }
                    style={
                      generationProgress == null ? undefined : { width: `${generationProgress}%` }
                    }
                  />
                </div>
              )}
            </div>
          </div>
          <OutputPanel />
        </section>
        {editingProfile && (
          <WorkspacePane
            layout="editor"
            title={t('paneActions.edit')}
            icon={PencilIcon}
            onClose={() => setEditingId(null)}
          >
            <EditProfile
              key={editingProfile.id}
              profile={editingProfile}
              onDone={() => setEditingId(null)}
            />
          </WorkspacePane>
        )}
        {!editingProfile && selectedTake?.mode === 'design' && (
          <WorkspacePane
            title={t('clone.history_title')}
            icon={HistoryIcon}
            onClose={() => openTake(null)}
          >
            <TakeDetails item={selectedTake} />
          </WorkspacePane>
        )}
        {!editingProfile && !selectedTake && productionOpen && (
          <WorkspacePane
            title={t('clone.production_overrides')}
            icon={SlidersHorizontalIcon}
            onClose={() => setProductionOpen(false)}
          >
            <ProductionSettings />
          </WorkspacePane>
        )}
      </div>
    </div>
  );
}
