/**
 * TitleTabs — the titlebar tab strip, the alternative to the icon rail.
 *
 * Settings → Appearance → Navigation style swaps this in for `NavRail`
 * (`navStyle: 'tabs'`); both render the same `navItems` list, so a workspace
 * added there appears in both skins.
 *
 * The strip lives INSIDE the header row (`Header.jsx`) rather than in its own
 * row, so the tabs sit in the window's title bar the way a browser's do — on
 * macOS the header's 64px left inset keeps them clear of the traffic lights.
 * Everything that makes the tab read as a tab (the connective bottom curl,
 * the accent rule, the separators) is in `index.css` under `.tabstrip`.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { NAV_ITEMS, NAV_FOOTER_ITEMS } from './navItems';

function Tab({ item, active, showSeparator, onClick }) {
  const { Icon, label, accent } = item;
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-current={active ? 'page' : undefined}
      data-testid={`titletab-${item.id}`}
      className={`tabstrip__tab ${active ? 'is-active' : ''} ${
        showSeparator ? 'has-separator' : ''
      }`}
      style={{ '--tab-accent': accent }}
    >
      <Icon size={13} className="tabstrip__icon" />
      <span className="tabstrip__label">{label}</span>
    </button>
  );
}

/**
 * Labels are all-or-nothing.
 *
 * Nine tabs sharing a title bar with the status cluster is a width the strip
 * loses: letting every tab shrink turns the whole row into "Laun… / V… / D. /
 * St…", which is denser AND less legible than the icons alone. So the strip
 * measures what it would need with every label shown and, when that doesn't
 * fit, keeps the label only on the tab you're in — the one label that says
 * something the icon doesn't. The rest keep their tooltips.
 *
 * Measured, not thresholded: the room available depends on locale (German
 * labels run ~40% longer), UI scale, font choice and whether the live-metrics
 * cluster is on. Any px breakpoint would be wrong for some combination of
 * those. `is-measuring` forces the full layout for one synchronous read so
 * the answer never depends on the answer we gave last time.
 */
function useCompactLabels(stripRef, deps) {
  const [compact, setCompact] = React.useState(false);
  React.useLayoutEffect(() => {
    const el = stripRef.current;
    if (!el) return undefined;
    const measure = () => {
      el.classList.add('is-measuring');
      const needed = el.scrollWidth;
      const available = el.clientWidth;
      el.classList.remove('is-measuring');
      // 1px of slack: sub-pixel label widths shouldn't decide the layout.
      setCompact(needed > available + 1);
    };
    measure();
    // Webfonts land after first paint and change every label's width without
    // resizing anything — re-measure once they do.
    document.fonts?.ready?.then?.(measure).catch?.(() => {});
    if (typeof ResizeObserver === 'undefined') return undefined;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return compact;
}

export default function TitleTabs({ mode, setMode }) {
  const { t } = useTranslation();
  const items = React.useMemo(
    () => NAV_ITEMS.map((d) => ({ ...d, label: t(`nav.${d.tKey}`) })),
    [t],
  );
  const tailItems = React.useMemo(
    () => NAV_FOOTER_ITEMS.map((d) => ({ ...d, label: t(`nav.${d.tKey}`) })),
    [t],
  );
  const stripRef = React.useRef(null);
  const compact = useCompactLabels(stripRef, [items, tailItems, mode]);

  return (
    <nav
      ref={stripRef}
      className={`tabstrip ${compact ? 'is-compact' : ''}`}
      aria-label={t('nav.workspaces', { defaultValue: 'Workspaces' })}
    >
      {items.map((it, i) => (
        <Tab
          key={it.id}
          item={it}
          active={mode === it.id}
          // Chrome's hairline between neighbours: drawn by the tab on the left
          // of the gap, and only where neither side is the active (raised) tab.
          showSeparator={mode !== it.id && mode !== items[i + 1]?.id && i < items.length - 1}
          onClick={() => setMode(it.id)}
        />
      ))}
      <span className="tabstrip__gap" aria-hidden="true" />
      {tailItems.map((it) => (
        <Tab key={it.id} item={it} active={mode === it.id} onClick={() => setMode(it.id)} />
      ))}
    </nav>
  );
}
