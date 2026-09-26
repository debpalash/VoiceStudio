import React, { isValidElement } from 'react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

/**
 * Menu — floating action menu triggered by a child element.
 * Backed by shadcn/ui DropdownMenu (which wraps @radix-ui/react-dropdown-menu)
 * for keyboard navigation, collision-aware positioning, enter/exit animation,
 * and proper ARIA attributes.
 *
 * @param children   exactly one child — the trigger element
 * @param items      array of items or 'separator' strings
 * @param placement  'bottom-start' | 'bottom-end' | 'top-start' | 'top-end'
 * @param open       controlled open state (optional)
 * @param onOpenChange callback on open/close
 * @param width      optional fixed width (px)
 * @param disabled   disable the trigger
 */
export default function Menu({
  children,
  items = [],
  placement = 'bottom-start',
  open: controlledOpen,
  onOpenChange,
  width,
  disabled = false,
}) {
  // Map our placement string to Radix side + align
  const sideMap = {
    'bottom-start': { side: 'bottom', align: 'start' },
    'bottom-end': { side: 'bottom', align: 'end' },
    'top-start': { side: 'top', align: 'start' },
    'top-end': { side: 'top', align: 'end' },
  };
  const { side, align } = sideMap[placement] || sideMap['bottom-start'];

  if (!isValidElement(children)) {
    return children ?? null;
  }

  const rootProps = {};
  if (controlledOpen != null) {
    rootProps.open = controlledOpen;
    rootProps.onOpenChange = onOpenChange;
  } else if (onOpenChange) {
    rootProps.onOpenChange = onOpenChange;
  }

  return (
    <DropdownMenu {...rootProps}>
      <DropdownMenuTrigger asChild disabled={disabled}>
        {children}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side={side}
        align={align}
        sideOffset={4}
        avoidCollisions
        collisionPadding={8}
        className="ui-menu"
        style={width ? { width } : undefined}
      >
        {items.map((item, i) => {
          if (item === 'separator' || item?.type === 'separator') {
            return <DropdownMenuSeparator key={`sep-${i}`} className="ui-menu__separator" />;
          }
          const Icon = item.icon;
          return (
            <DropdownMenuItem
              key={item.id ?? i}
              className={`ui-menu__item ${item.destructive ? 'is-destructive' : ''} ${item.disabled ? 'is-disabled' : ''}`}
              disabled={item.disabled}
              onSelect={() => item.onSelect?.()}
            >
              {Icon && <Icon size={12} className="ui-menu__icon" />}
              <span className="ui-menu__label">{item.label}</span>
              {item.shortcut && <span className="ui-menu__shortcut">{item.shortcut}</span>}
              {item.trailing}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
