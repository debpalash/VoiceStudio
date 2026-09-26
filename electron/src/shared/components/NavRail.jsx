import React from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeftRight, PanelLeftOpen, PanelLeftClose } from 'lucide-react';
import './NavRail.css';
import { NAV_ITEMS as ITEM_DEFS, NAV_FOOTER_ITEMS as FOOTER_DEFS } from './navItems';

// Shared icon-button base for the chrome rail (was `.rail-btn`). `group` enables
// the hover-reveal of the per-button tooltip label below.
const RAIL_BTN_BASE =
  'group relative inline-flex h-[36px] w-[36px] cursor-pointer items-center justify-center rounded-[var(--chrome-radius-pill)] [transition:background_0.14s,color_0.14s,border-color_0.14s]';

// Hover-reveal tooltip label (was `.rail-btn .rail-label`); flips to the opposite
// edge when the rail sits on the right.
function railLabelCls(side) {
  const sideCls =
    side === 'right'
      ? 'left-auto right-[48px] [transform:translate(4px,-50%)]'
      : 'left-[46px] [transform:translate(-4px,-50%)]';
  return `pointer-events-none absolute top-1/2 z-[10000] whitespace-nowrap rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-bg)] px-[8px] py-[3px] font-sans text-[11px] font-medium text-[var(--chrome-fg)] opacity-0 [border:1px_solid_var(--chrome-border-strong)] [transition:opacity_0.15s,transform_0.15s] group-hover:opacity-100 group-hover:[transform:translate(0,-50%)] ${sideCls}`;
}

function RailBtn({ active, Icon, label, accent, side, onClick, expanded }) {
  // Active = accent-tinted fill/border + an accent indicator bar (`::before`)
  // hanging off the rail edge; flips edges with the rail side.
  const stateCls = active
    ? `text-[var(--rail-accent,var(--chrome-accent))] bg-[color-mix(in_srgb,var(--rail-accent,var(--chrome-accent))_12%,transparent)] [border:1px_solid_color-mix(in_srgb,var(--rail-accent,var(--chrome-accent))_35%,transparent)] before:absolute before:top-[20%] before:bottom-[20%] before:w-[3px] before:rounded-[2px] before:bg-[var(--rail-accent,#f3a5b6)] before:content-[''] before:[box-shadow:0_0_10px_color-mix(in_srgb,var(--rail-accent,#f3a5b6)_50%,transparent)] ${
        side === 'right' ? 'before:right-[-8px]' : 'before:left-[-8px]'
      }`
    : 'bg-transparent text-[var(--chrome-fg-dim)] [border:1px_solid_transparent] hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)]';
  return (
    <button
      onClick={onClick}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      className={`nav-rail-item ${RAIL_BTN_BASE} ${stateCls}`}
      data-expanded={expanded}
      style={{ '--rail-accent': accent }}
    >
      <Icon size={18} aria-hidden="true" />
      <span className={expanded ? 'min-w-0 truncate text-sm' : railLabelCls(side)}>{label}</span>
    </button>
  );
}

export default function NavRail({ mode, setMode, side = 'left', onFlipSide }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = React.useState(false);
  const railRef = React.useRef(null);
  React.useEffect(() => {
    if (!expanded) return;
    const closeOutside = (event) => {
      if (!railRef.current?.contains(event.target)) setExpanded(false);
    };
    const escape = (event) => {
      if (event.key === 'Escape') {
        setExpanded(false);
        railRef.current?.querySelector('[aria-expanded]')?.focus();
      }
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', escape);
    };
  }, [expanded]);
  const items = React.useMemo(
    () => ITEM_DEFS.map((d) => ({ ...d, label: t(`nav.${d.tKey}`) })),
    [t],
  );
  const footerItems = React.useMemo(
    () => FOOTER_DEFS.map((d) => ({ ...d, label: t(`nav.${d.tKey}`) })),
    [t],
  );

  // `nav-rail` is retained purely as the layout hook the (out-of-scope)
  // `.app-container > .nav-rail` grid rules position by; all visual styling now
  // lives in the utilities below. Border flips to the inner edge when on the right.
  const asideBorder =
    side === 'right'
      ? '[border-left:1px_solid_var(--chrome-border)]'
      : '[border-right:1px_solid_var(--chrome-border)]';

  return (
    <aside
      ref={railRef}
      data-expanded={expanded}
      data-side={side}
      className={`nav-rail z-50 flex select-none flex-col items-center gap-[10px] bg-[var(--chrome-bg)] pb-[10px] pt-[18px] ${asideBorder}`}
    >
      {expanded && (
        <svg
          className="nav-rail-waves"
          viewBox="0 0 232 1000"
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
        >
          {[0, 12, 24, 36].map((offset) => (
            <path
              key={offset}
              transform={`translate(${offset} 0)`}
              d="M-90 80 C270 240 -170 450 60 650 S270 860 120 1040"
            />
          ))}
        </svg>
      )}
      <button
        type="button"
        aria-label={t('nav.workspaces')}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        className="nav-rail-toggle inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border-0 bg-transparent text-[var(--chrome-fg-muted)] cursor-pointer hover:bg-[var(--chrome-hover-bg)]"
      >
        {expanded ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
        {expanded && <span className="text-sm">{t('nav.workspaces')}</span>}
      </button>
      <div className="nav-rail-links flex flex-1 flex-col items-center gap-[9px]">
        {items.map((it) => (
          <RailBtn
            key={it.id}
            {...it}
            side={side}
            expanded={expanded}
            active={mode === it.id}
            onClick={() => {
              setMode(it.id);
              setExpanded(false);
            }}
          />
        ))}
      </div>
      <div className="nav-rail-footer flex flex-col items-center gap-[8px]">
        {footerItems.map((it) => (
          <RailBtn
            key={it.id}
            {...it}
            side={side}
            expanded={expanded}
            active={mode === it.id}
            onClick={() => {
              setMode(it.id);
              setExpanded(false);
            }}
          />
        ))}
        <button
          onClick={onFlipSide}
          title={side === 'left' ? t('nav.move_rail_right') : t('nav.move_rail_left')}
          aria-label={t('nav.flip_rail')}
          className="relative mt-[6px] inline-flex h-[30px] w-[36px] cursor-pointer items-center justify-center rounded-none bg-transparent pt-[10px] text-[var(--chrome-fg-dim)] [border-top:1px_solid_var(--chrome-border)] [border-right:1px_solid_transparent] [border-bottom:1px_solid_transparent] [border-left:1px_solid_transparent] [transition:background_0.14s,color_0.14s,border-color_0.14s] hover:bg-transparent hover:text-[var(--chrome-accent)] hover:[transform:rotate(180deg)]"
        >
          <ArrowLeftRight size={15} />
        </button>
      </div>
    </aside>
  );
}
