import { Link } from '@tanstack/react-router';
import { GemIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import './support-shortcut.css';
import { GithubStar } from './github-star';
import { useAccount } from '@/features/account/use-account';
import { isProPlan } from '@/features/account/account-format';
import { getBridge } from '@/components/bridge';
import { runRendererTask } from '@/lib/global-error-recovery';

const CLASSES =
  'support-shortcut app-no-drag flex items-center gap-1 px-1.5 focus-visible:outline-2 focus-visible:outline-ring';

export function SupportShortcut() {
  const { t } = useTranslation();
  const { state } = useAccount();
  // Three states: signed out -> "Get Pro" (plans page); signed in on a non-Pro plan ->
  // "Upgrade to Pro" (plans page); Pro account -> "Get Pro app" (dashboard download page,
  // the open-source app and VoiceStudio Pro being different builds) (#4284).
  const signedIn = !!(state?.available && state.accountEnabled && state.signedIn);
  const proDownload = signedIn && isProPlan(state?.plan) ? state?.links?.proDownload : undefined;
  const label = t(
    proDownload ? 'supportPlans.get_pro_app' : signedIn ? 'supportPlans.upgrade_pro' : 'supportPlans.get_pro'
  );
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Tooltip>
        <TooltipTrigger
          render={
            proDownload ? (
              <button
                type="button"
                aria-label={label}
                className={CLASSES}
                onClick={() =>
                  runRendererTask('Open Pro download page', () => getBridge()?.files.openExternal(proDownload))
                }
              />
            ) : (
              <Link to="/pro" aria-label={label} className={CLASSES} />
            )
          }
        >
          <GemIcon aria-hidden="true" className="size-3.5" />
          <span className="hidden text-xs font-medium sm:inline">{label}</span>
        </TooltipTrigger>
        <TooltipContent surface="theme" side="bottom">
          {label}
        </TooltipContent>
      </Tooltip>
      <GithubStar />
    </div>
  );
}
