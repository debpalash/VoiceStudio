import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useGenerateClone } from '@/hooks/use-generate';
import { router } from '@/router';
import { writeDraft } from '@/features/design/design-draft';
import defaults from '@shared/utils/firstSound.json';
import {
  instructToVdStates,
  mergeDescribedAttrs,
} from '@shared/utils/voiceInstruct';
import { pickDesignSeed } from '@shared/utils/seed';
import { FIRST_SOUND_EVENT } from '@/lib/first-sound';

/**
 * Finish onboarding by doing the product's core job through the normal shared
 * controller. The Design workspace therefore owns progress, cancellation,
 * recovery and Vidstack playback exactly like a user-started generation.
 */
export function FirstSoundHandoff() {
  const { t } = useTranslation();
  const { generateDesign, designBlocker } = useGenerateClone();
  const running = useRef(false);
  const pending = useRef(false);

  useEffect(() => {
    const run = async () => {
      if (running.current || !pending.current || designBlocker) return;
      pending.current = false;
      running.current = true;
      const text = t('demo.clone_prompt');
      const seed = pickDesignSeed(false, null);
      writeDraft({
        text,
        attrs: mergeDescribedAttrs(instructToVdStates(defaults.instruct)),
        seed,
        profileId: null,
      });
      await router.navigate({ to: '/design' });
      try {
        await generateDesign({
          text,
          instruct: defaults.instruct,
          seed,
          language: 'Auto',
        });
      } finally {
        running.current = false;
      }
    };
    const listener = () => {
      pending.current = true;
      void run().catch(() => {
        // The normal generation controller owns actionable failures. Navigation
        // teardown must not leak a rejected onboarding promise into Chromium.
      });
    };
    window.addEventListener(FIRST_SOUND_EVENT, listener);
    if (pending.current) void run().catch(() => {});
    return () => window.removeEventListener(FIRST_SOUND_EVENT, listener);
  }, [designBlocker, generateDesign, t]);

  return null;
}
