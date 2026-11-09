import { Link } from '@tanstack/react-router';
import { CompassIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { buttonVariants } from '@/components/ui/button';

/** Shown for an address no workspace owns, instead of silently landing on Home. */
export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1>{t('app.not_found_title')}</h1>
      </WorkspaceHeader>
      <div className="grid flex-1 place-items-center p-6">
        <div className="flex max-w-sm flex-col items-center gap-3 text-center">
          <CompassIcon className="size-6 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm text-muted-foreground">{t('app.not_found_body')}</p>
          <Link to="/" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            {t('nav.home')}
          </Link>
        </div>
      </div>
    </div>
  );
}
