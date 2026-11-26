import { useEffect, useRef, useState } from 'react';
import { MicIcon } from 'lucide-react';
import { METER_BARS, formatElapsed, nextBars } from './level-meter';

const TICK_MS = 60;

interface CompactOverlayProps {
  /** Subscribes to live microphone levels (0..1); returns an unsubscribe. */
  onLevel: (listener: (level: number) => void) => () => void;
  /** Epoch ms recording began; the timer is blank until then. */
  startedAt?: number;
  /** False once recording stopped: the bars settle and the timer freezes. */
  live: boolean;
}

/** Level meter in the theme colour plus an elapsed timer; nothing else. */
export function CompactOverlay({ onLevel, startedAt, live }: CompactOverlayProps) {
  const latest = useRef(0);
  const [bars, setBars] = useState<number[]>(() => Array(METER_BARS).fill(0));
  const [now, setNow] = useState(() => Date.now());
  const frozenAt = useRef<number | null>(null);

  useEffect(() => {
    if (!live) return;
    return onLevel((level) => {
      latest.current = level;
    });
  }, [live, onLevel]);

  useEffect(() => {
    if (!live) latest.current = 0;
    const id = setInterval(() => {
      setBars((previous) => nextBars(previous, latest.current));
      if (live) setNow(Date.now());
    }, TICK_MS);
    return () => clearInterval(id);
  }, [live]);

  if (live) frozenAt.current = null;
  else frozenAt.current ??= Date.now();
  const elapsed = startedAt ? (frozenAt.current ?? now) - startedAt : 0;

  return (
    <div
      data-testid="compact-overlay"
      className="app-drag flex h-full items-center gap-3 px-3 text-foreground"
    >
      <MicIcon className="size-4 shrink-0 text-primary" aria-hidden />
      <div className="flex h-8 flex-1 items-center justify-center gap-1" aria-hidden>
        {bars.map((value, index) => (
          <span
            key={index}
            data-testid="compact-bar"
            className="w-1.5 rounded-full bg-primary transition-[height] duration-75"
            style={{ height: `${Math.max(12, Math.round(value * 100))}%` }}
          />
        ))}
      </div>
      <span role="timer" className="w-10 shrink-0 text-right text-sm tabular-nums">
        {formatElapsed(elapsed)}
      </span>
    </div>
  );
}
