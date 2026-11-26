import { Store } from '@tanstack/store';

export interface DubInputOptions {
  separateBackground?: boolean;
  subtitlesInTarget?: boolean;
  narrationWanted?: boolean;
}

const PREFERENCES_KEY = 'voicestudio.dub.input-options.v1';
function readPreferences(): DubInputOptions {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFERENCES_KEY) || '{}');
    // Keep the background unless the user chose voice only (the backend default too).
    return { separateBackground: saved.separateBackground !== false };
  } catch {
    return {};
  }
}

interface DubInputSelection extends DubInputOptions {
  media?: File;
  captions?: File;
  profileId: string;
  submitting: boolean;
  step?: number;
  srtChoice?: boolean;
  srtLanguageChosen?: boolean;
}

// Files cannot be restored from JSON. Keep the pending selection in memory so
// changing workspace or refetching voices cannot silently discard it.
export const dubInputSelection = new Store<DubInputSelection>({
  ...readPreferences(),
  profileId: '',
  submitting: false,
});

export function setDubInputSelection(patch: Partial<DubInputSelection>) {
  dubInputSelection.setState((current) => ({ ...current, ...patch }));
  if (patch.separateBackground !== undefined) {
    try {
      localStorage.setItem(
        PREFERENCES_KEY,
        JSON.stringify({ separateBackground: patch.separateBackground }),
      );
    } catch {
      /* Selection still works without storage. */
    }
  }
}

export type PrepareInputResult = void | boolean | Promise<void | boolean>;

export async function submitDubInput(
  onFile: (
    file: File,
    subtitles?: File,
    profileId?: string,
    options?: DubInputOptions,
  ) => PrepareInputResult,
  onSubtitles: (file: File, profileId?: string, options?: DubInputOptions) => PrepareInputResult,
) {
  const selected = dubInputSelection.state;
  if (selected.submitting || (!selected.media && !selected.captions)) return false;
  setDubInputSelection({ submitting: true });
  try {
    const accepted = selected.media
      ? await onFile(selected.media, selected.captions, selected.profileId, {
          separateBackground: selected.separateBackground !== false,
          subtitlesInTarget: selected.subtitlesInTarget === true,
          narrationWanted: selected.narrationWanted === true,
        })
      : await onSubtitles(selected.captions!, selected.profileId, {
          subtitlesInTarget: selected.subtitlesInTarget === true,
          narrationWanted: selected.narrationWanted === true,
        });
    // Only a confirmed handoff may consume these files. A busy/rejected or
    // failed attempt keeps them; a later selection is never cleared by an old run.
    if (
      accepted === true &&
      dubInputSelection.state.media === selected.media &&
      dubInputSelection.state.captions === selected.captions
    ) {
      setDubInputSelection({
        media: undefined,
        captions: undefined,
        step: 0,
        srtChoice: undefined,
        srtLanguageChosen: false,
      });
    }
    return accepted === true;
  } finally {
    setDubInputSelection({ submitting: false });
  }
}
