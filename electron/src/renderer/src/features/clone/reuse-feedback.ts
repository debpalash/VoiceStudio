import type { TFunction } from 'i18next';
import { toast } from 'sonner';

/**
 * Run a take reuse that may first load the take's whole script (#4483). A
 * failure gets a retryable notice instead of stopping silently, and nothing
 * opens with the clipped script.
 */
export async function reuseWithFeedback(run: () => Promise<void>, t: TFunction): Promise<void> {
  try {
    await run();
  } catch {
    toast.error(t('clone.history_full_text_failed'), {
      action: { label: t('common.retry'), onClick: () => void reuseWithFeedback(run, t) },
    });
  }
}
