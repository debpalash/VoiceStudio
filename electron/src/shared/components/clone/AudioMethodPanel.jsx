import { useState, useSyncExternalStore } from 'react';
import { ChevronDown, Save, UploadCloud, X } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Button, Input, Segmented } from '../../ui';
import VoiceSelect from './VoiceSelect';
import MicButton from './MicButton';
import WaveformPlayer from '../WaveformPlayer';
import VoiceModeIcon from './VoiceModeIcon';

const EMPTY_LEVEL_STORE = {
  getSnapshot: () => 0,
  subscribe: () => () => {},
};

const isAudioFile = (file) =>
  file && (file.type.startsWith('audio/') || /\.(mp3|wav|m4a|flac|ogg|aac|webm)$/i.test(file.name));

export default function AudioMethodPanel({
  t,
  selectedProfile,
  setSelectedProfile,
  profiles,
  ingestRefAudio,
  refAudio,
  isCleaning,
  isRecording,
  isStartingRecording = false,
  recordingTime,
  audioInputs = [],
  selectedAudioInputId = '',
  setSelectedAudioInputId,
  channelMode = 'auto',
  setChannelMode,
  inputLevelStore = EMPTY_LEVEL_STORE,
  startRecording,
  stopRecording,
  refText,
  setRefText,
  instruct,
  setInstruct,
  showSaveProfile,
  setShowSaveProfile,
  profileName,
  setProfileName,
  handleSaveProfile,
}) {
  const [sourceMode, setSourceMode] = useState(
    isStartingRecording || isRecording ? 'record' : 'upload',
  );
  const inputLevel = useSyncExternalStore(inputLevelStore.subscribe, inputLevelStore.getSnapshot);
  const hasReference = Boolean(refAudio || selectedProfile);

  const ingestFile = (file) => {
    if (isAudioFile(file)) {
      ingestRefAudio(file);
      return;
    }
    if (file) toast.error(t('clone.unsupported_audio'));
  };

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div className="flex items-start justify-between gap-3 max-[520px]:flex-col">
        <div>
          <div className="text-[length:var(--text-sm)] font-semibold text-fg">
            {t('clone.reference_audio')}
          </div>
          <p className="mt-0.5 text-[length:var(--text-xs)] text-fg-muted">
            {t('clone.reference_hint')}
          </p>
        </div>
        {!hasReference && (
          <Segmented
            size="sm"
            value={sourceMode}
            onChange={setSourceMode}
            disabled={isStartingRecording || isRecording}
            aria-label={t('clone.reference_audio')}
            items={[
              {
                value: 'upload',
                label: (
                  <span className="voice-source-tab inline-flex items-center gap-2">
                    <VoiceModeIcon mode="upload" />
                    {t('clone.upload_audio')}
                  </span>
                ),
              },
              {
                value: 'record',
                label: (
                  <span className="voice-source-tab inline-flex items-center gap-2">
                    <VoiceModeIcon mode="record" />
                    {t('clone.record')}
                  </span>
                ),
              },
            ]}
          />
        )}
      </div>

      {!hasReference && sourceMode === 'upload' && (
        <div className="flex flex-1 flex-col min-h-32">
          <input
            type="file"
            accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg"
            onChange={(event) => {
              ingestFile(event.target.files[0]);
              event.target.value = '';
            }}
            className="sr-only"
            id="audio-upload"
          />
          <label
            htmlFor="audio-upload"
            className="flex flex-1 min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg bg-[var(--chrome-hover-bg)] px-4 py-3 text-center transition-[background] duration-[var(--dur-fast)] hover:bg-[var(--chrome-accent-bg)] focus-within:bg-[var(--chrome-accent-bg)] [&.is-dragging]:bg-[var(--chrome-accent-bg)]"
            onDragOver={(event) => {
              event.preventDefault();
              event.currentTarget.classList.add('is-dragging');
            }}
            onDragLeave={(event) => event.currentTarget.classList.remove('is-dragging')}
            onDrop={(event) => {
              event.preventDefault();
              event.currentTarget.classList.remove('is-dragging');
              ingestFile(event.dataTransfer.files[0]);
            }}
          >
            <UploadCloud className="text-fg-muted" size={20} />
            <span className="text-[length:var(--text-xs)] font-medium text-fg-muted">
              {t('clone.drop_audio')}
            </span>
          </label>
        </div>
      )}

      {!hasReference && sourceMode === 'record' && (
        <div className="flex flex-1 flex-col gap-5 rounded-lg bg-[var(--chrome-hover-bg)] p-4">
          <div className="flex flex-1 min-h-28 items-center justify-center [&>button]:min-h-24 [&>button]:min-w-32">
            <MicButton
              isCleaning={isCleaning}
              isStarting={isStartingRecording}
              isRecording={isRecording}
              recordingTime={recordingTime}
              onStart={startRecording}
              onStop={stopRecording}
            />
          </div>
          <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-4 max-[520px]:grid-cols-1">
            <label className="min-w-0 text-[length:var(--text-xs)] text-fg-muted">
              <span className="mb-1 block">{t('recording.input_device')}</span>
              <VoiceSelect
                label={t('recording.input_device')}
                value={selectedAudioInputId}
                onChange={(value) => {
                  if (!isStartingRecording && !isRecording && !isCleaning) {
                    setSelectedAudioInputId?.(value);
                  }
                }}
                disabled={isStartingRecording || isRecording || isCleaning}
                options={[
                  { value: '', label: t('recording.default_input') },
                  ...audioInputs
                    .filter((device) => device.deviceId)
                    .map((device, index) => ({
                      value: device.deviceId,
                      label:
                        device.label || t('recording.microphone_number', { number: index + 1 }),
                    })),
                ]}
              />
            </label>
            <label className="min-w-0 text-[length:var(--text-xs)] text-fg-muted">
              <span className="mb-1 block">{t('recording.channels')}</span>
              <VoiceSelect
                label={t('recording.channels')}
                value={channelMode}
                onChange={(value) => {
                  if (!isStartingRecording && !isRecording && !isCleaning) {
                    setChannelMode?.(value);
                  }
                }}
                disabled={isStartingRecording || isRecording || isCleaning}
                options={['auto', 'mono', 'stereo'].map((value) => ({
                  value,
                  label: t(`recording.channels_${value}`),
                }))}
              />
            </label>
          </div>
          {isRecording && (
            <div
              className="col-span-full flex items-center gap-2 text-[length:var(--text-xs)]"
              role="status"
              aria-live="polite"
            >
              <span
                className={`size-2 shrink-0 rounded-full ${inputLevel >= 0.025 ? 'bg-success' : 'bg-fg-muted'}`}
                aria-hidden="true"
              />
              <meter
                className="h-2 min-w-0 flex-1 accent-[var(--color-success)]"
                min="0"
                max="1"
                value={inputLevel}
                aria-label={t('recording.input_level')}
              />
              <span className={inputLevel >= 0.025 ? 'text-success' : 'text-fg-muted'}>
                {inputLevel >= 0.025
                  ? t('recording.input_detected')
                  : t('recording.no_input_detected')}
              </span>
            </div>
          )}
        </div>
      )}

      {refAudio && !selectedProfile && (
        <div className="rounded-lg bg-[var(--chrome-hover-bg)] p-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="min-w-0 flex-1 truncate text-[length:var(--text-sm)] font-medium text-fg">
              {refAudio.name}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => ingestRefAudio(null)}
              leading={<X size={12} />}
            >
              {t('clone.clear')}
            </Button>
          </div>
          <WaveformPlayer
            src={refAudio}
            source="clone-reference"
            height={34}
            compact
            className="mt-2"
          />
        </div>
      )}

      {selectedProfile && (
        <div className="flex items-center gap-3 rounded-lg bg-[rgba(142,192,124,0.08)] p-3 text-[length:var(--text-sm)]">
          <span className="min-w-0 flex-1 truncate text-success">
            {t('clone.using_profile', {
              name: profiles.find((profile) => profile.id === selectedProfile)?.name,
            })}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSelectedProfile(null)}
            leading={<X size={12} />}
          >
            {t('clone.clear')}
          </Button>
        </div>
      )}

      {hasReference && (
        <details
          className="group rounded-lg bg-[var(--chrome-hover-bg)]"
          defaultOpen={Boolean(refText || instruct)}
        >
          <summary className="flex min-h-9 cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-[length:var(--text-xs)] font-medium text-fg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)] [&::-webkit-details-marker]:hidden">
            {t('clone.optional_details')}
            <ChevronDown
              size={14}
              className="transition-transform duration-[var(--dur-fast)] group-open:rotate-180"
            />
          </summary>
          <div className="grid grid-cols-2 gap-2 bg-black/10 p-3 max-[700px]:grid-cols-1">
            <label htmlFor="clone-transcript" className="min-w-0">
              <span className="label-row">{t('clone.transcript')}</span>
              <input
                id="clone-transcript"
                type="text"
                className="input-base"
                value={refText}
                onChange={(event) => setRefText(event.target.value)}
                placeholder={t('clone.optional')}
              />
            </label>
            <label htmlFor="clone-style" className="min-w-0">
              <span className="label-row">{t('clone.style')}</span>
              <input
                id="clone-style"
                type="text"
                className="input-base"
                value={instruct}
                onChange={(event) => setInstruct(event.target.value)}
                placeholder={t('clone.style_placeholder')}
              />
            </label>
          </div>
        </details>
      )}

      {refAudio && !selectedProfile && (
        <div>
          {!showSaveProfile ? (
            <Button
              variant="subtle"
              size="sm"
              onClick={() => setShowSaveProfile(true)}
              leading={<Save size={12} />}
            >
              {t('clone.save_as_profile')}
            </Button>
          ) : (
            <div className="flex items-center gap-2 max-[520px]:flex-wrap [&>:first-child]:min-w-40 [&>:first-child]:flex-1">
              <Input
                size="sm"
                placeholder={t('clone.profile_name')}
                value={profileName}
                onChange={(event) => setProfileName(event.target.value)}
              />
              <Button variant="subtle" size="sm" onClick={handleSaveProfile}>
                {t('clone.save')}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setShowSaveProfile(false)}>
                {t('clone.cancel')}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
