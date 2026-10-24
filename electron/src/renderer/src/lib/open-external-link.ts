import { getBridge } from '@/components/bridge';
import { scrubText } from '@shared/utils/scrub';

/** Keep expected browser-open failures local while retaining safe diagnostics. */
export async function openExternalLink(url: string, onFailure: () => void): Promise<void> {
  try {
    const bridge = getBridge();
    if (bridge) await bridge.files.openExternal(url);
    else window.open(url, '_blank', 'noopener,noreferrer');
  } catch (error) {
    console.warn(
      'Could not open external link:',
      scrubText(error instanceof Error ? error.message : String(error)),
    );
    onFailure();
  }
}
