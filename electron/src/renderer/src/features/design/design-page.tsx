import { DisabledReason, firstReason } from '@/components/disabled-reason';
import { VoicesSidebar } from '@/features/clone/voices-sidebar';
import { WandSparklesIcon } from 'lucide-react';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { ProfileAvatar } from '@/components/profile-avatar';
import { DemoPresets } from './demo-presets';
import { setCloneSetting } from '@/lib/store/clone-settings';
import {
  PersonalityPresets,
  STARTING_POINT_CHIP,
  presetIcon,
  sameRecipe,
} from './personality-presets';
import { EditProfile } from '@/features/clone/edit-profile';
import { WorkspacePane } from '@/components/workspace-pane';
import { TakeDetails } from '@/features/clone/take-details';
import { openTake, useSelectedTake } from '@/lib/store/takes';
import {
  DESIGN_DRAFT_EVENT,
  STORAGE,
  applyDescription,
  canResetToDescription,
  designInstruct,
  designRecipe,
  designRequestSeed,
  editVoice,
  linkedDesignProfile,
  pickDetail,
  readDraft,
  replaceRecipe,
  restoreDesignProfile,
  type DesignDraft,
} from './design-draft';
import { useDescription } from './use-description';
import { useFollowLatestTake } from './use-follow-latest-take';
import { useProfiles } from '@/hooks/use-profiles';
import { useEngines } from '@/hooks/use-engines';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { apiJson, describeError, profileAudioUrl } from '@/lib/api/client';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { DesignComposer } from './design-composer';
import {
  BookmarkIcon,
  ChevronDownIcon,
  HistoryIcon,
  LightbulbIcon,
  PanelRightOpenIcon,
  PencilIcon,
  SaveIcon,
  ShuffleIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  RotateCcwIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useHistory } from '@/hooks/use-history';
import { DESIGN_CARD, DesignDisclosure, DesignHeading } from './design-section';
import { detailLabel, plainLabel } from './detail-labels';
import { useGenerateClone } from '@/hooks/use-generate';
import { OutputPanel } from '@/features/clone/output-panel';
import { queryKeys } from '@/lib/query';
import { cn } from '@/lib/utils';
import { cloneSettingsStore } from '@/lib/store/clone-settings';
import type { Profile } from '@/lib/api/types';
import { CATEGORIES, PRESETS } from '@shared/utils/constants';
import { buildDesignInstruct, mergeDescribedAttrs } from '@shared/utils/voiceInstruct';
import { pickDesignSeed } from '@shared/utils/seed';
export function DesignPage() {
  const { t } = useTranslation();
  const selectedTake = useSelectedTake();
  useFollowLatestTake();
  const history = useHistory();
  const [draft, setDraft] = useState(readDraft);
  const [name, setName] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [startingOpen, setStartingOpen] = useState(true);
  // Results are keyed by the text they were computed for; if the text has since
  // moved on (typed over, or swapped out elsewhere) the result is stale: ignore it.
  const mapper = useDescription((mapped, described) => {
    setDraft((current) => {
      if (current.description.trim() !== described) return current;
      return applyDescription(current, mapped, described);
    });
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const generation = useGenerateClone();
  const activeEngine = useEngines().activeTts;
  const freeform = generation.instructVocabulary === 'freeform';
  const description = draft.description;
  const client = useQueryClient();
  const profiles = useProfiles();
  const savedProfilesRef = useRef<HTMLDetailsElement>(null);
  const designProfiles = profiles.data?.filter((profile) => profile.kind === 'design') ?? [];
  // The saved voice this draft still is; any edit turns it into a new design.
  const activeProfile = linkedDesignProfile(draft, profiles.data);
  // Re-rendering a saved sample is cloning, which any ready engine can do.
  const designBlocker = generation.designBlockerFor(activeProfile);
  const editingProfile = profiles.data?.find((profile) => profile.id === editingId);
  useEffect(() => {
    const restore = (event: Event) => {
      setDraft((event as CustomEvent<DesignDraft>).detail ?? readDraft());
    };
    window.addEventListener(DESIGN_DRAFT_EVENT, restore);
    return () => window.removeEventListener(DESIGN_DRAFT_EVENT, restore);
  }, []);
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
    // A running mapping is left alone; its result is merged beneath this pick.
    setDraft((current) => pickDetail(current, category, value).draft);
    const reset = pickDetail(draft, category, value).clearedCategory;
    if (reset === null) return;
    toast.info(t('clone.vd_exclusive_cleared', { cleared: t('clone.cat_' + reset) }));
  };
  const descriptionStatus = freeform
    ? t('clone.describe_freeform')
    : mapper.pending
      ? t('preferences.loading')
      : !mapper.matched
        ? t('clone.describe_no_match')
        : mapper.unmatched.length
          ? t('clone.describe_unmatched', { items: mapper.unmatched.join(', ') })
          : t('clone.describe_hint');
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
      toast.success(t('clone.saved_profile'));
    } catch (error) {
      setSaveError(describeError(error));
    } finally {
      setSaving(false);
    }
  };
  const identityRecipe =
    Object.values(draft.attrs)
      .filter((value) => value && value !== 'Auto')
      .map((value) => detailLabel(t, value))
      .join(' · ') || t('clone.identity_auto');
  const latestDesignTake = history.data?.find((item) => item.mode === 'design') ?? null;
  // Below this width the takes pane overlays the script as a drawer instead of
  // squeezing it into a narrow column.
  const drawer =
    '@max-[76rem]:absolute @max-[76rem]:inset-y-0 @max-[76rem]:right-0 @max-[76rem]:z-30 @max-[76rem]:shadow-2xl';
  const paneOpen = Boolean(editingProfile || selectedTake?.mode === 'design');
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('designWorkspace.title')}</h1>
      </WorkspaceHeader>
      <div className="relative flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t('designWorkspace.title')}
          icon={WandSparklesIcon}
          size="wide"
          variant="controls"
          library={<VoicesSidebar />}
          libraryLabel={t('clone.savedTab')}
          tabsInHeader
          className="space-y-3"
        >
          <section className={cn(DESIGN_CARD, 'space-y-2.5')}>
            <div className="flex min-h-7 items-center gap-2">
              <DesignHeading icon={SparklesIcon} htmlFor="voice-description">
                {t('clone.describe_label')}
              </DesignHeading>
              {canResetToDescription(draft) && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  title={t('clone.reset_to_description')}
                  aria-label={t('clone.reset_to_description')}
                  disabled={generation.isGenerating || mapper.pending}
                  onClick={() => {
                    setDraft((current) => ({ ...current, picks: {}, profileId: null }));
                    mapper.reset(description);
                  }}
                >
                  <RotateCcwIcon />
                </Button>
              )}
            </div>
            <Textarea
              id="voice-description"
              rows={3}
              maxLength={2000}
              className="min-h-20 resize-y text-sm"
              disabled={generation.isGenerating}
              value={description}
              placeholder={t('clone.describe_placeholder')}
              onChange={(event) => {
                const value = event.target.value;
                setDraft((current) => editVoice(current, { description: value }));
                // Always map, even for a free-form engine, so the details stay
                // in step for when the user switches back to OmniVoice.
                mapper.describe(value);
              }}
            />
            <div className="flex items-center gap-2">
              <p role="status" className="min-w-0 flex-1 text-xs text-muted-foreground">
                {descriptionStatus}
              </p>
              {!freeform && mapper.failed && (
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => mapper.describe(description)}
                >
                  {t('backend.retry')}
                </Button>
              )}
            </div>
          </section>
          <DesignDisclosure
            icon={BookmarkIcon}
            title={t('clone.saved_profiles')}
            meta={designProfiles.length ? String(designProfiles.length) : undefined}
            detailsRef={savedProfilesRef}
          >
            {designProfiles.length ? (
              <div className="space-y-0.5">
                {designProfiles.map((profile) => (
                  <div key={profile.id} className="flex items-center gap-1">
                    <Button
                      className="min-w-0 flex-1 justify-start truncate font-normal"
                      variant={activeProfile?.id === profile.id ? 'secondary' : 'ghost'}
                      size="sm"
                      aria-pressed={activeProfile?.id === profile.id}
                      disabled={generation.isGenerating}
                      onClick={() => {
                        mapper.cancel();
                        const restored = restoreDesignProfile(profile, draft.seed);
                        setDraft((current) =>
                          replaceRecipe(current, {
                            attrs: restored.attrs,
                            seed: restored.seed,
                            profileId: restored.profileId,
                            description: restored.description,
                            picks: restored.picks,
                            mapped: restored.mapped,
                          }),
                        );
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
                      disabledLabel={
                        generation.designBlocker === 'design'
                          ? t('designWorkspace.engine_cannot_design', {
                              engine: activeEngine?.display_name ?? '',
                            })
                          : t('engines.none_ready_title')
                      }
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
            ) : (
              <p className="text-xs text-muted-foreground">{t('designWorkspace.saved_empty')}</p>
            )}
          </DesignDisclosure>
          <DesignDisclosure
            icon={LightbulbIcon}
            title={t('clone.starting_points')}
            open={startingOpen}
            onToggle={setStartingOpen}
          >
            <div className="grid grid-cols-2 gap-1.5 @[26rem]:grid-cols-3">
              <PersonalityPresets
                disabled={generation.isGenerating}
                attrs={draft.attrs}
                onSelect={(attrs) => {
                  mapper.cancel();
                  setDraft((current) =>
                    replaceRecipe(current, { attrs: mergeDescribedAttrs(attrs) }),
                  );
                }}
              />
              {PRESETS.map((preset) => {
                const Icon = presetIcon(preset.id);
                const recipe = mergeDescribedAttrs(preset.attrs);
                const active = sameRecipe(draft.attrs, recipe);
                const label = plainLabel(t('clone.preset_' + preset.id));
                return (
                  <Button
                    key={preset.id}
                    type="button"
                    size="sm"
                    variant="ghost"
                    className={STARTING_POINT_CHIP}
                    title={label}
                    aria-pressed={active}
                    disabled={generation.isGenerating}
                    onClick={() => {
                      mapper.cancel();
                      setDraft((current) => replaceRecipe(current, { attrs: recipe }));
                    }}
                  >
                    <Icon aria-hidden="true" />
                    <span>{label}</span>
                  </Button>
                );
              })}
            </div>
          </DesignDisclosure>
          <DesignDisclosure
            icon={SlidersHorizontalIcon}
            title={t('clone.details')}
            meta={identityRecipe}
          >
            <div className="space-y-4">
              {Object.entries(CATEGORIES).map(([category, values]) => (
                <fieldset key={category} disabled={generation.isGenerating} className="space-y-1.5">
                  <legend className="mb-1.5 text-xs font-medium text-muted-foreground">
                    {t('clone.cat_' + category)}
                  </legend>
                  <div className="flex flex-wrap gap-1">
                    {values.map((value) => (
                      <Button
                        key={value}
                        size="xs"
                        variant={draft.attrs[category] === value ? 'secondary' : 'ghost'}
                        aria-pressed={draft.attrs[category] === value}
                        className="font-normal aria-pressed:font-medium"
                        onClick={() => change(category, value)}
                      >
                        {detailLabel(t, value)}
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
                      setDraft((current) => editVoice(current, { seed }));
                  }}
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  disabled={generation.isGenerating}
                  aria-label={t('clone.seed_reroll')}
                  title={t('clone.seed_reroll')}
                  onClick={() =>
                    setDraft((current) => editVoice(current, { seed: pickDesignSeed(false, null) }))
                  }
                >
                  <ShuffleIcon />
                </Button>
              </div>
            </div>
          </DesignDisclosure>
          <form
            className={cn(DESIGN_CARD, 'space-y-2')}
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
            <DisabledReason
              className="w-full"
              reason={firstReason([
                [generation.isGenerating, t('disabledReason.generating')],
                [!name.trim(), t('disabledReason.name_required')],
              ])}
            >
              {(describedBy) => (
                <Button
                  type="submit"
                  size="sm"
                  className="w-full"
                  disabled={!name.trim() || saving || mapper.pending || generation.isGenerating}
                  aria-describedby={describedBy}
                >
                  <SaveIcon />
                  {t('clone.save_as_profile')}
                </Button>
              )}
            </DisabledReason>
            {saveError && (
              <p role="alert" className="text-xs text-destructive">
                {t('clone.save_failed', { message: saveError })}
              </p>
            )}
          </form>
        </SecondarySidebar>
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col gap-4 overflow-y-auto px-6 pt-5 pb-4">
            <div className="flex min-w-0 items-center justify-between gap-3">
              <Button
                variant="ghost"
                disabled={generation.isGenerating || designProfiles.length === 0}
                aria-label={t('cloneFlow.change_voice')}
                className="h-12 w-fit min-w-0 max-w-full justify-start gap-2.5 px-0 hover:bg-transparent"
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
              {!paneOpen && latestDesignTake && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="shrink-0 text-muted-foreground hover:text-foreground"
                  title={t('designWorkspace.takes_show')}
                  onClick={() => openTake(latestDesignTake)}
                >
                  <PanelRightOpenIcon />
                  <span className="@max-[48rem]:sr-only">{t('clone.history_title')}</span>
                </Button>
              )}
            </div>
            <DemoPresets
              initialOpen={!draft.text.trim()}
              disabled={generation.isGenerating}
              onUse={(preset) => {
                mapper.cancel();
                setDraft((current) =>
                  replaceRecipe(current, {
                    text: preset.script || current.text,
                    attrs: mergeDescribedAttrs(preset.attrs),
                  }),
                );
                setCloneSetting('language', preset.language || 'Auto');
              }}
            />
            <div
              data-slot="design-script"
              className="flex min-h-52 flex-1 flex-col rounded-xl border border-border/45 bg-muted/10 transition-[border-color,box-shadow] duration-150 hover:border-border/70 focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-ring/20"
            >
              <label
                htmlFor="design-script"
                className="px-4 pt-3 text-xs font-medium text-muted-foreground"
              >
                {t('clone.text_label')}
              </label>
              <textarea
                dir="auto"
                id="design-script"
                className="min-h-32 flex-1 resize-none bg-transparent px-4 pt-1.5 pb-2 text-base leading-7 outline-none placeholder:text-muted-foreground/70"
                value={draft.text}
                placeholder={t('clone.prompt_placeholder')}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    text: event.target.value,
                  }))
                }
              />
              <span className="self-end px-4 pb-2.5 text-xs tabular-nums text-muted-foreground">
                {t('clone.characters', { count: draft.text.length })}
              </span>
            </div>
          </div>
          <OutputPanel />
          <DesignComposer
            generation={generation}
            blocker={designBlocker}
            // Free-form engines take the description itself, so a mapping still
            // in flight does not hold them back.
            ready={Boolean(draft.text.trim()) && (freeform || !mapper.pending)}
            engineName={activeEngine?.display_name ?? ''}
            text={draft.text}
            onSynthesize={() =>
              void generation.generateDesign({
                text: draft.text,
                instruct: designInstruct(draft, generation.instructVocabulary),
                recipe: designRecipe(draft),
                seed: designRequestSeed(draft, activeProfile),
                profileId: activeProfile?.id ?? null,
              })
            }
          />
        </section>
        {paneOpen && (
          // Drawer mode: a click beside the pane closes it, as with any overlay.
          <button
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            className="absolute inset-0 z-20 hidden bg-background/45 @max-[76rem]:block"
            onClick={() => (editingProfile ? setEditingId(null) : openTake(null))}
          />
        )}
        {editingProfile && (
          <WorkspacePane
            className={drawer}
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
            className={drawer}
            title={t('clone.history_title')}
            icon={HistoryIcon}
            onClose={() => openTake(null)}
          >
            <TakeDetails item={selectedTake} />
          </WorkspacePane>
        )}
      </div>
    </div>
  );
}
