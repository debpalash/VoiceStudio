import type { ComponentProps } from 'react';
import { useTtsLanguages } from '@/hooks/use-tts-languages';
import { LanguagePicker } from './language-picker';

/** Output language only: reference recordings and translation targets stay independent. */
export function EngineLanguagePicker({
  operation = 'clone',
  ...props
}: ComponentProps<typeof LanguagePicker> & { operation?: string }) {
  const { names, modelLabel, state, dialects } = useTtsLanguages(operation);
  return (
    <LanguagePicker
      {...props}
      recentsScope="tts"
      supportedOptions={names}
      modelLabel={modelLabel}
      capabilityState={state}
      renderedDialects={dialects}
    />
  );
}
