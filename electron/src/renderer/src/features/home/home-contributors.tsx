import type { CSSProperties, MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUpRightIcon } from 'lucide-react';
import { getBridge } from '@/components/bridge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { runRendererTask } from '@/lib/global-error-recovery';
import { REPO_URL, X_URL } from '@shared/utils/contactLinks';
import { homeContributors } from './home-contributors-data';

function openProfile(event: MouseEvent<HTMLAnchorElement>) {
  const bridge = getBridge();
  if (!bridge) return;
  event.preventDefault();
  const href = event.currentTarget.href;
  runRendererTask('Open contributor profile', () => bridge.files.openExternal(href));
}

/** Home contributor credits: every qualifying avatar, then the All link. */
export function HomeContributors() {
  const { t } = useTranslation();
  // Fill three tapered rows from the right edge, ending with All.
  const items = [...homeContributors, { login: 'all', avatar: '' }];
  const topCount = Math.ceil((items.length + 3) / 3);
  const middleCount = Math.ceil((items.length - topCount + 1) / 2);
  const starts = [0, topCount, topCount + middleCount];
  return (
    <ul
      className="home-contributors"
      aria-label={t('homeUi.allContributors')}
      style={{ '--contributor-columns': topCount * 2 } as CSSProperties}
    >
      {items.map(({ login, avatar }, index) => {
        const row = index < starts[1] ? 0 : index < starts[2] ? 1 : 2;
        const start = starts[row];
        const portrait = (
          <span className="home-contributor-avatar">
            {login === 'all' ? (
              <span className="home-contributor-all">{t('homeUi.all')}</span>
            ) : (
              <img src={avatar} alt="" width={44} height={44} />
            )}
          </span>
        );
        return (
          <li
            key={login}
            style={{
              gridRow: row + 1,
              gridColumn: `${topCount * 2 - 1 - (index - start) * 2} / span 2`,
            }}
          >
            {login === 'debpalash' ? (
              <Popover>
                <PopoverTrigger
                  openOnHover
                  delay={120}
                  closeDelay={250}
                  render={
                    <button type="button" className="home-contributor" aria-label="@debpalash" />
                  }
                >
                  {portrait}
                </PopoverTrigger>
                <PopoverContent
                  side="bottom"
                  align="end"
                  className="home-contributor-popup"
                  aria-label="@debpalash"
                >
                  <div className="home-contributor-identity">
                    <img src={avatar} alt="" width={40} height={40} />
                    <strong>Palash</strong>
                  </div>
                  {[
                    { label: 'GitHub', handle: 'debpalash', href: 'https://github.com/debpalash' },
                    { label: 'X', handle: 'idebpalash', href: X_URL },
                  ].map(({ label, handle, href }) => (
                    <a
                      key={label}
                      aria-label={`${label} @${handle}`}
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={openProfile}
                    >
                      <span>
                        <strong>{label}</strong>
                        <small>@{handle}</small>
                      </span>
                      <ArrowUpRightIcon className="size-3.5" aria-hidden="true" />
                    </a>
                  ))}
                </PopoverContent>
              </Popover>
            ) : (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <a
                      className="home-contributor"
                      href={
                        login === 'all'
                          ? `${REPO_URL}/graphs/contributors`
                          : `https://github.com/${login}`
                      }
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={
                        login === 'all' ? t('homeUi.allContributors') : `GitHub · @${login}`
                      }
                      onClick={openProfile}
                    />
                  }
                >
                  {portrait}
                </TooltipTrigger>
                <TooltipContent surface="theme" side="bottom">
                  {login === 'all' ? t('homeUi.allContributors') : `@${login}`}
                </TooltipContent>
              </Tooltip>
            )}
          </li>
        );
      })}
    </ul>
  );
}
