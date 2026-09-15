import { SidebarToggle } from '@/components/app-shell/sidebar-toggle';
import { EditProfile } from './edit-profile';
import { ProfileAvatar } from '@/components/profile-avatar';
import { VoiceSetup } from './voice-setup';
import { useReferenceTranscript } from '@/hooks/use-reference-transcript';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';
import { openTake, useSelectedTake } from '@/lib/store/takes';
import { TakeDetails } from './take-details';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from '@tanstack/react-router';
import {
  HistoryIcon,
  AudioLinesIcon,
  ChevronDownIcon,
  PencilIcon,
  SearchIcon,
  SlidersHorizontalIcon,
} from 'lucide-react';
import { isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { WorkspacePane } from '@/components/workspace-pane';
import { useProfiles } from '@/hooks/use-profiles';
import { setCloneSetting, useCloneSetting } from '@/lib/store/clone-settings';
import { useReference } from '@/lib/store/reference';
import { EngineNotice } from '@/components/engine-notice';
import { useGenerateClone } from '@/hooks/use-generate';
import { ActionBar, ProductionSettings } from './action-bar';
import { OutputPanel } from './output-panel';
import { ReferencePanel, SaveProfileForm } from './reference-panel';
import { ScriptPanel } from './script-panel';
import { useCloneDemo } from '@/hooks/use-clone-demo';
import { runRendererTask } from '@/lib/global-error-recovery';

const DEMO_PROFILE_ID = 'demo0001';
const DEMO_PROMPTED_KEY = 'omnivoice.demoClonePrompted';

export function ClonePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const selectedTake = useSelectedTake();
  const { generate, isGenerating } = useGenerateClone();
  const demo = useCloneDemo();
  const { panel, editingProfileId } = useWorkspace();
  const setPanel = (panel: 'voice' | 'settings' | null) => setWorkspace({ panel });
  const setLibraryTab = (libraryTab: 'voices' | 'takes') => setWorkspace({ libraryTab });
  useEffect(() => {
    if (selectedTake) setWorkspace({ panel: null });
  }, [selectedTake]);

  const profiles = useProfiles();
  const editingProfile = profiles.data?.find((profile) => profile.id === editingProfileId);
  const selectedId = useCloneSetting('selectedProfileId');
  const text = useCloneSetting('text');
  const reference = useReference();
  const transcription = useReferenceTranscript(selectedId ? null : reference.file);
  const selectedVoice = profiles.data?.find(
    (profile) => profile.id === selectedId && profile.kind === 'clone' && profile.ref_audio_path,
  );
  const hasVoice = selectedId ? Boolean(selectedVoice) : Boolean(reference.file?.size);
  const [changingVoice, setChangingVoice] = useState(false);
  const [showDemoCoachmark, setShowDemoCoachmark] = useState(false);
  const [skippedFile, setSkippedFile] = useState<File | null>(null);
  const savingUpload =
    !selectedId && Boolean(reference.file) && reference.file !== skippedFile && !changingVoice;
  const choosingVoice = !hasVoice || changingVoice;

  useEffect(() => {
    if (selectedId !== DEMO_PROFILE_ID) {
      setShowDemoCoachmark(false);
      return;
    }
    if (text || demoWasPrompted()) return;
    setCloneSetting('text', t('demo.clone_prompt'));
    setShowDemoCoachmark(true);
    try {
      localStorage.setItem(DEMO_PROMPTED_KEY, '1');
    } catch {
      // The current session still receives guidance when storage is unavailable.
    }
  }, [selectedId, t, text]);
  const previousVoice = useRef({
    id: selectedId,
    file: reference.file,
    ready: hasVoice,
  });
  const focusScript = () =>
    requestAnimationFrame(() =>
      document.querySelector<HTMLTextAreaElement>('[data-clone-script]')?.focus(),
    );
  const finishChoice = () => {
    setChangingVoice(false);
    setWorkspace({ panel: null });
    focusScript();
  };
  useEffect(() => {
    const previous = previousVoice.current;
    if (
      hasVoice &&
      (!previous.ready || previous.id !== selectedId || previous.file !== reference.file)
    ) {
      setChangingVoice(false);
      setWorkspace({ panel: null });
      if (selectedId) focusScript();
    }
    previousVoice.current = {
      id: selectedId,
      file: reference.file,
      ready: hasVoice,
    };
  }, [hasVoice, selectedId, reference.file]);
  const wasGenerating = useRef(false);
  if (isGenerating) wasGenerating.current = true;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key === 'Escape' &&
        !event.defaultPrevented &&
        !(event.target instanceof HTMLElement && event.target.closest('[role=dialog], [role=menu]'))
      ) {
        openTake(null);
        setWorkspace({ panel: null, editingProfileId: null });
        return;
      }
      if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter' || event.repeat) return;
      if (isGenerating || demo || event.defaultPrevented) return;
      if (event.target instanceof HTMLElement && event.target.closest('[role="dialog"], aside'))
        return;
      event.preventDefault();
      void generate();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [demo, generate, isGenerating, panel]);

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <div className="flex min-w-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col">
          <header
            className={cn(
              'workspace-titlebar flex shrink-0 items-center justify-between gap-3 px-5',
              !editingProfile &&
                !selectedTake &&
                (!panel || choosingVoice) &&
                !isMac() &&
                'native-controls-right',
            )}
          >
            <div className="flex min-w-0 items-center gap-2">
              <SidebarToggle />
              <h1 className="truncate text-sm font-medium">{t('clone.title')}</h1>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t('preferences.search')}
                title="Ctrl/Cmd + K"
                onClick={() => window.dispatchEvent(new Event('voicestudio:commands'))}
              >
                <SearchIcon />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setLibraryTab('takes');
                  setWorkspace({ libraryOpen: true });
                }}
              >
                <HistoryIcon data-icon="inline-start" />
                {t('clone.history_title')}
              </Button>
            </div>
          </header>
          <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col gap-5 overflow-y-auto px-6 pt-6 pb-4">
            <EngineNotice operation="clone" />
            {choosingVoice ? (
              <VoiceSetup
                onChosen={finishChoice}
                onBack={
                  hasVoice
                    ? finishChoice
                    : () => runRendererTask('Leave voice selection', () => navigate({ to: '/' }))
                }
              />
            ) : savingUpload && reference.file ? (
              <div className="w-full space-y-5">
                <SaveProfileForm
                  key={reference.file.name + reference.file.lastModified}
                  file={reference.file}
                  transcribing={transcription.state === 'busy'}
                  onDone={() => {
                    setSkippedFile(reference.file);
                    finishChoice();
                  }}
                />
                <ReferencePanel hideSave transcription={transcription} />
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between gap-3">
                  <Button
                    variant="ghost"
                    disabled={isGenerating}
                    aria-label={t('cloneFlow.change_voice')}
                    className="h-12 min-w-0 justify-start gap-2.5 px-0 hover:bg-transparent"
                    onClick={() => {
                      openTake(null);
                      setChangingVoice(true);
                      setPanel(null);
                    }}
                  >
                    <ProfileAvatar
                      name={selectedVoice?.name ?? ''}
                      imageUrl={selectedVoice?.image_url}
                    />
                    <span className="shrink-0 font-normal text-muted-foreground">
                      {t('clone.voice_kicker')} <span aria-hidden="true">·</span>
                    </span>
                    <span className="max-w-60 truncate font-semibold">
                      {selectedVoice?.name ?? t('cloneFlow.voice_sample')}
                    </span>
                    <ChevronDownIcon className="text-muted-foreground" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="font-normal text-muted-foreground hover:text-foreground"
                    aria-expanded={panel === 'voice'}
                    onClick={() => {
                      openTake(null);
                      setWorkspace({ editingProfileId: null });
                      setPanel(panel === 'voice' ? null : 'voice');
                    }}
                  >
                    <AudioLinesIcon />
                    {t('cloneFlow.voice_sample')}
                  </Button>
                </div>
                <ScriptPanel
                  voiceName={selectedVoice?.name}
                  coachmark={showDemoCoachmark ? t('demo.clone_coachmark') : undefined}
                  onUserEdit={() => setShowDemoCoachmark(false)}
                />
              </>
            )}
          </div>
          {((!choosingVoice && !savingUpload) || isGenerating) && (
            <div className="z-10 mt-auto shrink-0 bg-background">
              <div className="mx-auto w-full max-w-4xl px-6 pb-4">
                <div className="glass-panel composer-surface rounded-xl border border-border/60 bg-muted/30 p-2.5 shadow-[0_2px_12px_rgb(0_0_0/4%)]">
                  <ActionBar
                    settingsOpen={panel === 'settings'}
                    onOpenSettings={() => {
                      openTake(null);
                      setPanel(panel === 'settings' ? null : 'settings');
                    }}
                  />
                </div>
              </div>
              <OutputPanel />
            </div>
          )}
        </section>
        {editingProfile && (
          <WorkspacePane
            layout="editor"
            title={t('paneActions.edit')}
            icon={PencilIcon}
            onClose={() => setWorkspace({ editingProfileId: null })}
          >
            <EditProfile
              key={editingProfile.id}
              profile={editingProfile}
              onDone={() => setWorkspace({ editingProfileId: null })}
            />
          </WorkspacePane>
        )}
        {!editingProfile && selectedTake && (
          <WorkspacePane
            title={t('clone.history_title')}
            icon={HistoryIcon}
            onClose={() => openTake(null)}
          >
            <TakeDetails item={selectedTake} />
          </WorkspacePane>
        )}
        {!editingProfile && panel && !choosingVoice && !savingUpload && (
          <WorkspacePane
            collapsible={panel === 'voice'}
            title={t(panel === 'voice' ? 'cloneFlow.voice_sample' : 'clone.production_overrides')}
            icon={panel === 'voice' ? AudioLinesIcon : SlidersHorizontalIcon}
            onClose={() => setPanel(null)}
          >
            <div hidden={panel !== 'voice'}>
              <ReferencePanel transcription={transcription} />
            </div>
            {panel === 'settings' && <ProductionSettings />}
          </WorkspacePane>
        )}
      </div>
      <div className="sr-only" role="status" aria-live="polite">
        {isGenerating
          ? t('clone.generating_status')
          : wasGenerating.current
            ? t('clone.generating_done_status')
            : null}
      </div>
    </div>
  );
}

function demoWasPrompted(): boolean {
  try {
    return localStorage.getItem(DEMO_PROMPTED_KEY) === '1';
  } catch {
    return false;
  }
}
