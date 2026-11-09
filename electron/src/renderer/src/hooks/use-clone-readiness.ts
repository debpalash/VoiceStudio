import { useCloneDemo } from './use-clone-demo';
import { useStore } from '@tanstack/react-store';
import { useProfiles } from './use-profiles';
import { useCloneSettings } from '@/lib/store/clone-settings';
import { recordingBusyStore, useReference } from '@/lib/store/reference';
import { cloneBlocker } from '@/lib/clone-readiness';
import { isPresetOnly, useTtsReadiness } from './use-tts-readiness';
import { useEngines } from './use-engines';
export function useCloneInputsReadiness() {
  const demo = useCloneDemo();
  const settings = useCloneSettings();
  const reference = useReference();
  const profiles = useProfiles();
  const recording = useStore(recordingBusyStore);
  const { activeTts } = useEngines();
  if (demo) return 'engine' as const;
  return cloneBlocker({
    text: settings.text,
    profileId: settings.selectedProfileId,
    profiles: profiles.isError ? [] : profiles.data,
    fileSize: reference.file?.size ?? 0,
    busy: recording || reference.pending,
    needsReference: !isPresetOnly(activeTts),
  });
}

export function useCloneReadiness() {
  const ttsBlocker = useTtsReadiness('clone');
  const inputBlocker = useCloneInputsReadiness();
  return ttsBlocker ?? inputBlocker;
}
