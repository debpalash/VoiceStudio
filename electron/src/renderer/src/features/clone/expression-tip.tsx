import { LightbulbIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { Button } from '@/components/ui/button';
import { useLatestOutput } from '@/lib/store/output';

const STORAGE_KEY = 'voicestudio.clone.expression-tip.v1';

function readSeen(): boolean {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(STORAGE_KEY) === 'seen';
  } catch {
    return false;
  }
}

const tipStore = new Store<boolean>(readSeen());

/** Hide the tip for good: dismissed, or the user found Emotion on their own. */
export function markExpressionTipSeen(): void {
  if (tipStore.state) return;
  tipStore.setState(() => true);
  try {
    localStorage.setItem(STORAGE_KEY, 'seen');
  } catch {
    // Private mode: the tip stays hidden for this session.
  }
}

/** One-time pointer to expressive controls after the first take (#3422). */
export function ExpressionTip() {
  const { t } = useTranslation();
  const seen = useStore(tipStore);
  const { result } = useLatestOutput();
  if (seen || !result) return null;
  return (
    <div
      role="status"
      className="mb-3 flex items-center gap-2 rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 text-xs text-muted-foreground"
    >
      <LightbulbIcon className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
      <span className="min-w-0 flex-1">{t('expression.tip')}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label={t('common.dismiss')}
        onClick={markExpressionTipSeen}
      >
        <XIcon aria-hidden="true" />
      </Button>
    </div>
  );
}
