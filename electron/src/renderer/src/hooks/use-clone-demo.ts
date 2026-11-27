import { useCloneSetting } from '@/lib/store/clone-settings';
import { useTtsReadiness } from './use-tts-readiness';

export const DEMO_PROFILE_ID = 'demo0001';

export function useCloneDemo() {
  const profile = useCloneSetting('selectedProfileId');
  const readiness = useTtsReadiness();
  return profile === DEMO_PROFILE_ID && readiness === 'engine';
}
