import {
  useLayoutEffect,
  useRef,
  useState,
  type ComponentProps,
  type ComponentType,
  type ReactNode,
} from 'react';
import { CheckIcon } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const supportsFieldSizing = () =>
  typeof CSS !== 'undefined' &&
  typeof CSS.supports === 'function' &&
  CSS.supports('field-sizing', 'content');

/**
 * A borderless textarea that grows with its text. Chromium (Electron) sizes it
 * natively via `field-sizing: content`; browsers serving the web UI without it
 * fall back to measuring scrollHeight, so the editor looks the same everywhere.
 */
export function AutoSizeTextarea({
  inputRef,
  className,
  value,
  ...props
}: ComponentProps<'textarea'> & { inputRef?: (node: HTMLTextAreaElement | null) => void }) {
  const local = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const node = local.current;
    if (!node || supportsFieldSizing()) return;
    const fit = () => {
      node.style.height = 'auto';
      node.style.height = `${node.scrollHeight}px`;
    };
    fit();
    if (typeof ResizeObserver === 'undefined') return;
    let width = node.clientWidth;
    const observer = new ResizeObserver(() => {
      if (node.clientWidth === width) return;
      width = node.clientWidth;
      fit();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [value]);
  return (
    <textarea
      dir="auto"
      ref={(node) => {
        local.current = node;
        inputRef?.(node);
      }}
      rows={1}
      value={value}
      className={cn(
        'block w-full resize-none overflow-hidden bg-transparent outline-none field-sizing-content placeholder:text-muted-foreground/70',
        className,
      )}
      {...props}
    />
  );
}

const chipTone = {
  default: 'bg-muted/60 text-foreground/90 hover:bg-muted',
  muted: 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
  accent: 'bg-primary/12 text-primary hover:bg-primary/18',
} as const;

/** A small pill under a script line that opens its editor in a popover. */
export function ChipPopover({
  label,
  icon: Icon,
  tone = 'default',
  disabled,
  className,
  contentClassName,
  children,
  content,
}: {
  label: string;
  icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' }>;
  tone?: keyof typeof chipTone;
  disabled?: boolean;
  className?: string;
  contentClassName?: string;
  children?: ReactNode;
  content: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        disabled={disabled}
        aria-label={label}
        title={label}
        className={cn(
          'inline-flex h-6 min-w-0 max-w-48 items-center gap-1 rounded-md px-1.5 text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 data-popup-open:bg-secondary data-popup-open:text-foreground',
          chipTone[tone],
          className,
        )}
      >
        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
        {children !== undefined && <span className="truncate">{children}</span>}
      </PopoverTrigger>
      <PopoverContent className={cn('w-64 space-y-2 p-2', contentClassName)}>
        {content(() => setOpen(false))}
      </PopoverContent>
    </Popover>
  );
}

/** A single-choice list inside a chip popover (who speaks, which voice). */
export function ChoiceList<T extends string | null>({
  label,
  options,
  value,
  disabled,
  onSelect,
}: {
  label: string;
  options: { id: T; name: string }[];
  value: T;
  disabled?: boolean;
  onSelect: (id: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="max-h-56 space-y-0.5 overflow-y-auto">
      {options.map((option) => {
        const selected = option.id === value;
        return (
          <Button
            key={option.id ?? ''}
            size="sm"
            variant={selected ? 'secondary' : 'ghost'}
            aria-pressed={selected}
            className="w-full justify-between font-normal"
            disabled={disabled}
            onClick={() => onSelect(option.id)}
          >
            <span className="truncate">{option.name}</span>
            {selected && <CheckIcon className="text-primary" aria-hidden="true" />}
          </Button>
        );
      })}
    </div>
  );
}
