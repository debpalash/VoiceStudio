import { AlertTriangleIcon, InfoIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

export interface ScriptWarning {
  type: string;
  name?: string;
  title?: string;
  tag?: string;
}

/**
 * Script checks shown under the editor, in normal flow. Characters that are
 * not cast yet are not a problem (they read in the main voice), so they get
 * one calm note instead of a warning per name; real problems stay warnings.
 */
export function ValidationWarnings({
  warnings,
  onDismiss,
}: {
  warnings: ScriptWarning[];
  onDismiss: () => void;
}) {
  const { t, i18n } = useTranslation();
  if (!warnings.length) return null;
  const uncast = [
    ...new Set(
      warnings.flatMap((warning) =>
        warning.type === 'unknown_voice' && warning.name ? [warning.name] : [],
      ),
    ),
  ];
  const problems = warnings.filter((warning) => warning.type !== 'unknown_voice');

  const message = (warning: ScriptWarning) => {
    if (warning.type === 'empty_chapter')
      return t('audiobook.warn_empty_chapter', {
        title: warning.title || t('audiobook.untitled'),
      });
    if (warning.type === 'unknown_tag')
      return t('audiobook.warn_unknown_tag', { tag: warning.tag });
    if (warning.type === 'emotion_ignored')
      return t('expression.unsupported', { engine: warning.name });
    return '';
  };
  const dismiss = (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      className="ml-auto shrink-0"
      aria-label={t('audiobook.dismiss')}
      title={t('audiobook.dismiss')}
      onClick={onDismiss}
    >
      <XIcon />
    </Button>
  );

  return (
    <div className="shrink-0 space-y-2">
      {uncast.length > 0 && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs text-muted-foreground"
        >
          <InfoIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <p className="min-w-0 flex-1 leading-5">
            {t('audiobook.cast_note', {
              count: uncast.length,
              names: new Intl.ListFormat(i18n.language, { type: 'conjunction' }).format(uncast),
            })}
          </p>
          {!problems.length && dismiss}
        </div>
      )}
      {problems.length > 0 && (
        <div
          role="status"
          className="rounded-xl border border-warning/30 bg-warning/5 p-3 shadow-[inset_0_1px_0_rgb(255_255_255/4%)]"
        >
          <div className="flex items-center gap-2">
            <AlertTriangleIcon className="size-4 text-warning" aria-hidden="true" />
            <p className="text-xs font-medium">{t('audiobook.warnings_title')}</p>
            {dismiss}
          </div>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5 text-muted-foreground">
            {problems.map((warning, index) => (
              <li key={`${warning.type}:${index}`}>{message(warning)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
