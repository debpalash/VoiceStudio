import React from 'react';
import { Tabs as ShadcnTabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

/**
 * Tabs — pill-style segmented tab group, backed by shadcn/ui Tabs (which wraps
 * @radix-ui/react-tabs).
 *
 * Provides roving tabindex, arrow-key navigation, and proper
 * aria-selected / role="tab" / role="tablist" attributes.
 *
 * @param items     array of { id, label, icon?, accent? }
 * @param value     currently selected id
 * @param onChange  (id) => void
 * @param size      'sm' | 'md'
 * @param variant   'pill' (default) | 'underline'
 * @param idPrefix  optional prefix used to associate triggers with external panels
 * @param children  optional Radix TabsContent elements associated by value
 */
export default function Tabs({
  items = [],
  value,
  onChange,
  size = 'md',
  variant = 'pill',
  idPrefix,
  children,
  className = '',
  ...rest
}) {
  const isPill = variant === 'pill';
  const isSm = size === 'sm';

  // The `ui-tabs*` / `is-active` semantic classes carry no styling here (the
  // Tabs.css that defined them was converted to the utilities below). They are
  // retained as stable hooks for page-level overrides — Settings.css restyles
  // the tab rail via `.ui-tabs.settings-tabs-ui .ui-tabs__tab.is-active …`,
  // which is unlayered and so still wins over these layered utilities.
  //
  // Active/inactive visual state is expressed with `data-[state=active]` /
  // `data-[state=inactive]` variants (Radix sets data-state) rather than a JS
  // boolean, so it carries the same CSS specificity as — and twMerge-overrides
  // — shadcn's TabsTrigger defaults (e.g. `data-[state=active]:bg-background`).
  // The leading utilities also reset shadcn's list/trigger box-model defaults
  // (h-9, bg-muted, rounded-lg, flex-1, active shadow) back to VoiceStudio's.
  const listClass = isPill
    ? 'h-auto inline-flex shrink-0 gap-[3px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-bg)] p-[3px]'
    : 'h-auto inline-flex shrink-0 gap-[var(--space-5)] rounded-none border-0 border-b border-transparent bg-transparent p-0';

  return (
    <ShadcnTabs
      value={value}
      onValueChange={onChange}
      activationMode="manual"
      className="block gap-0"
    >
      <TabsList className={`ui-tabs ${listClass} ${className}`} {...rest}>
        {items.map((item) => {
          const active = value === item.id;
          const Icon = item.icon;
          const tabClass = isPill
            ? [
                'relative flex flex-none cursor-pointer items-center justify-center gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] border border-transparent font-sans tracking-[0.01em] data-[state=active]:shadow-none',
                '[transition:background_var(--dur-fast)_var(--ease-out),color_var(--dur-fast)_var(--ease-out),border-color_var(--dur-fast)_var(--ease-out)]',
                'focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none',
                isSm
                  ? 'px-[10px] py-[3px] text-[length:var(--text-xs)]'
                  : 'px-[14px] py-[5px] text-[length:var(--text-base)]',
                'data-[state=active]:border-[var(--chrome-accent-border)] data-[state=active]:bg-[var(--chrome-accent-bg)] data-[state=active]:font-semibold data-[state=active]:text-[color:var(--chrome-accent)]',
                'data-[state=inactive]:border-transparent data-[state=inactive]:bg-transparent data-[state=inactive]:font-medium data-[state=inactive]:text-[color:var(--chrome-fg-muted)] hover:data-[state=inactive]:bg-[var(--chrome-hover-bg)] hover:data-[state=inactive]:text-[color:var(--chrome-fg)]',
              ].join(' ')
            : [
                'mb-[-1px] flex flex-none cursor-pointer items-center gap-[var(--space-2)] rounded-none border-0 border-b-2 bg-transparent px-[2px] py-[6px] font-sans font-medium text-[length:var(--text-base)] data-[state=active]:bg-transparent data-[state=active]:shadow-none',
                '[transition:color_var(--dur-fast),border-color_var(--dur-fast)]',
                'focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none',
                'data-[state=active]:border-b-[var(--chrome-accent)] data-[state=active]:text-[color:var(--chrome-accent)]',
                'data-[state=inactive]:border-b-transparent data-[state=inactive]:text-[color:var(--chrome-fg-muted)] hover:data-[state=inactive]:text-[color:var(--chrome-fg)]',
              ].join(' ');
          return (
            <TabsTrigger
              key={item.id}
              value={item.id}
              {...(idPrefix
                ? {
                    id: `${idPrefix}-tab-${item.id}`,
                    'aria-controls': `${idPrefix}-panel-${item.id}`,
                  }
                : {})}
              className={`ui-tabs__tab ${active ? 'is-active' : ''} ${tabClass}`}
              title={item.title}
              style={active && item.accent ? { '--ui-tab-accent': item.accent } : undefined}
            >
              {Icon && <Icon size={12} className="ui-tabs__icon" />}
              <span>{item.label}</span>
            </TabsTrigger>
          );
        })}
      </TabsList>
      {children}
    </ShadcnTabs>
  );
}
