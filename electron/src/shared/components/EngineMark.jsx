import React from 'react';
import { cn } from '@/lib/utils';

/**
 * EngineMark — the per-engine identity mark for the Models & Engines
 * Settings surfaces.
 *
 * A small monogram chip whose hue is derived deterministically from the
 * engine id (the same trick as `models/format.js`'s `orgColor` for HF
 * orgs), so the same engine is instantly recognizable everywhere it
 * appears on these pages: the Engine Compatibility Matrix rows and the
 * "in memory" residency chips. Purely decorative (`aria-hidden`) — the
 * engine's name and id are always rendered as text alongside it.
 *
 * Theme-safe by construction: the hue is fixed per engine, but the fill
 * is a low-opacity `color-mix` over transparent and the glyph color is
 * mixed toward `--chrome-fg`, so it stays legible on light and dark
 * themes without per-theme overrides.
 */

/** Deterministic hue (0–359) from an engine id. */
export function engineHue(id) {
  const s = String(id || '');
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0xffff;
  return h % 360;
}

/** Two-character monogram from an engine id ("mlx-audio" → "MA",
 *  "voxcpm2" → "VO"). Falls back to "?" for an empty id. */
export function engineMonogram(id) {
  const parts = String(id || '')
    .split(/[^a-z0-9]+/i)
    .filter(Boolean);
  if (parts.length === 0) return '?';
  const mono = parts.length >= 2 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2);
  return mono.toUpperCase();
}

export default function EngineMark({ id, size = 20, className = '' }) {
  const accent = `hsl(${engineHue(id)} 62% 52%)`;
  return (
    <span
      aria-hidden="true"
      data-testid={`engine-mark-${id}`}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center rounded-[5px] font-semibold tracking-[0.02em]',
        className,
      )}
      style={{
        width: size,
        height: size,
        fontSize: Math.max(8, Math.round(size * 0.42)),
        background: `color-mix(in srgb, ${accent} 15%, transparent)`,
        border: `1px solid color-mix(in srgb, ${accent} 40%, transparent)`,
        color: `color-mix(in srgb, ${accent} 55%, var(--chrome-fg, currentColor))`,
      }}
    >
      {engineMonogram(id)}
    </span>
  );
}
