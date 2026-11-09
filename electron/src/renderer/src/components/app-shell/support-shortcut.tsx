import { Link } from '@tanstack/react-router';
import { GemIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import './support-shortcut.css';
import { GithubStar } from './github-star';

export function SupportShortcut() {
  const { t } = useTranslation();
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Tooltip>
        <TooltipTrigger
          render={
            <Link
              to="/pro"
              aria-label={t('supportPlans.get_pro')}
              className="support-shortcut app-no-drag flex items-center gap-1 px-1.5 focus-visible:outline-2 focus-visible:outline-ring"
            />
          }
        >
          <GemIcon aria-hidden="true" className="size-3.5" />
          <span className="hidden text-xs font-medium sm:inline">{t('supportPlans.get_pro')}</span>
        </TooltipTrigger>
        <TooltipContent surface="theme" side="bottom">
          {t('supportPlans.get_pro')}
        </TooltipContent>
      </Tooltip>
      <GithubStar />
    </div>
  );
}
