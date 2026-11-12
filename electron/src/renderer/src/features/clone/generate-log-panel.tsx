import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDownIcon, ScrollTextIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { stripLogAnsi } from '@/lib/log-format';
import { cn } from '@/lib/utils';

const SHOW_LOGS_KEY = 'voicestudio.generate.showLogs';
const TAIL = 60;
const POLL_MS = 1500;

function readShowLogs(): boolean {
  if (typeof localStorage === 'undefined') return false;
  try {
    return localStorage.getItem(SHOW_LOGS_KEY) === '1';
  } catch {
    return false;
  }
}

function writeShowLogs(value: boolean): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(SHOW_LOGS_KEY, value ? '1' : '0');
  } catch {
    /* private mode / quota */
  }
}

interface LogResponse {
  lines?: string[];
}

/** Live backend log strip while a take is generating. Off by default; toggle to enable. */
export function GenerateLogPanel({ active }: { active: boolean }) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(readShowLogs);
  const [text, setText] = useState('');
  const scroll = useRef<HTMLPreElement>(null);
  const follow = useRef(true);

  useEffect(() => {
    if (!active || !enabled) return;
    let cancelled = false;
    const pull = async () => {
      try {
        const data = await apiJson<LogResponse>(`/system/logs?tail=${TAIL}`);
        if (cancelled) return;
        setText((data.lines ?? []).map(stripLogAnsi).join(''));
      } catch {
        /* Backend may be busy or remote; keep the last successful tail. */
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, enabled]);

  useEffect(() => {
    if (!follow.current || !scroll.current) return;
    scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [text]);

  if (!active) return null;

  const toggle = () => {
    const next = !enabled;
    setEnabled(next);
    writeShowLogs(next);
    follow.current = true;
  };

  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-background/40">
      <div className="flex items-center gap-2 px-2 py-1.5">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 gap-1.5 px-2"
          aria-pressed={enabled}
          aria-expanded={enabled}
          onClick={toggle}
        >
          <ScrollTextIcon className="size-3.5 shrink-0" />
          <span className="text-xs">{t('clone.generate_logs')}</span>
          <ChevronDownIcon
            className={cn('size-3.5 shrink-0 transition-transform', enabled && 'rotate-180')}
          />
        </Button>
        <span className="ms-auto text-[11px] text-muted-foreground">
          {enabled ? t('clone.generate_logs_live') : t('clone.generate_logs_enable')}
        </span>
      </div>
      {enabled ? (
        <pre
          ref={scroll}
          className="studio-scrollbar max-h-40 overflow-auto border-t border-border/50 px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted-foreground"
          aria-label={t('clone.generate_logs')}
          onScroll={(event) => {
            const node = event.currentTarget;
            follow.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 24;
          }}
        >
          {text.trim() ? text : t('clone.generate_logs_empty')}
        </pre>
      ) : null}
    </div>
  );
}
