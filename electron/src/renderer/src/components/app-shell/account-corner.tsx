import { useLayoutEffect, useRef } from 'react';
import { isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { AccountMenu } from './account-menu';
import { SystemNotifications } from './system-notifications';
import { UpdatePill } from './update-pill';

const SPACE_VAR = '--corner-space';
/** Clear space kept between a page's own title bar buttons and the corner. */
const CORNER_GAP = 12;

/**
 * The window's top-right corner, on every page: the icon-only account control
 * (profile photo, or a Sign in icon) with the notifications bell to its right,
 * and the update pill to its left while an update needs attention.
 *
 * Windows/Linux draw their window buttons over the right 148px (titleBarOverlay),
 * so the corner sits just left of them; on macOS it sits at the edge. While it
 * shows, `--corner-space` widens every page title bar's right padding
 * (globals.css), so it never covers a page's own buttons.
 */
export function AccountCorner({ notificationsEnabled }: { notificationsEnabled: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const node = host.current;
    const measure = () => {
      if (node && node.childElementCount > 0)
        root.style.setProperty(SPACE_VAR, `${Math.ceil(node.offsetWidth) + CORNER_GAP}px`);
      else root.style.removeProperty(SPACE_VAR);
    };
    measure();
    const resize =
      node && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    if (node && resize) resize.observe(node);
    const children =
      node && typeof MutationObserver !== 'undefined' ? new MutationObserver(measure) : null;
    if (node && children) children.observe(node, { childList: true, subtree: true });
    return () => {
      resize?.disconnect();
      children?.disconnect();
      root.style.removeProperty(SPACE_VAR);
    };
  }, []);
  return (
    <div
      ref={host}
      data-slot="account-corner"
      className={cn(
        // Spans the 52px title bar so the corner centres on the same line as every header control.
        'app-no-drag fixed top-0 z-50 flex h-[52px] items-center gap-0.5',
        isMac() ? 'right-3.5' : 'right-[148px]',
      )}
    >
      <UpdatePill />
      <AccountMenu />
      <SystemNotifications enabled={notificationsEnabled} titlebar />
    </div>
  );
}
