/**
 * Shared pieces of the "where does the next job run" controls.
 *
 * Two surfaces ask the same question — the title-bar picker (<GpuTarget/>) and
 * the status-bar quick settings (<ComputeQuickSettings/>) — and they had
 * drifted: three copies of the same JSON wrapper, and status dots painted from
 * raw Tailwind palette classes (`bg-emerald-400`, `text-amber-400`) that do not
 * move with `[data-theme]`. On Midnight or Catppuccin the menu showed Gruvbox
 * greens next to the theme's own, which reads as a rendering bug rather than a
 * status colour.
 *
 * Everything here paints from the themed `--color-*` tokens instead, so one
 * definition per theme drives both controls.
 */
import React from 'react';
import { apiFetch } from '../api/client';

/**
 * `apiFetch` returns the raw Response and sets no Content-Type — it preserves
 * the call shape so FormData posts keep working. Every JSON caller has to say
 * so itself and parse the body, and a non-2xx is not an exception, so an
 * unchecked call fails silently. This does all three, and surfaces FastAPI's
 * `detail` so the user reads "Remote workers are turned off…" not "500".
 */
export async function workersRequest(path, { body, ...opts } = {}) {
  const res = await apiFetch(path, {
    ...opts,
    ...(body === undefined
      ? {}
      : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = payload?.detail;
    const error = new Error(
      typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `HTTP ${res.status}`,
    );
    error.isServerMessage = typeof detail === 'string' && detail.trim().length > 0;
    throw error;
  }
  return payload;
}

/** ready → success, busy → warn, offline → danger. Themed, not palette-fixed. */
const DOT = {
  ready: 'bg-[var(--color-success)]',
  busy: 'bg-[var(--color-warn)]',
  offline: 'bg-[var(--color-danger)]',
};

export function StatusDot({ status, className = '' }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-[6px] w-[6px] shrink-0 rounded-full ${DOT[status] || DOT.offline} ${className}`.trim()}
    />
  );
}

/** The one colour that means "what you picked is not what is running". */
export const FELL_BACK_TEXT = 'text-[color:var(--color-warn)]';

/**
 * A floating menu over the app chrome. `--chrome-bg` alone is the same colour
 * as the bar it drops out of, so the old menu relied entirely on `shadow-lg`
 * to separate itself and read as part of the bar on any theme whose chrome is
 * light. This lifts the surface a few percent and carries a real drop shadow —
 * the same treatment the footer's other popovers use.
 */
export const MENU_SURFACE =
  'rounded-[8px] border-0 p-1 bg-[color-mix(in_srgb,var(--chrome-fg)_6%,var(--chrome-bg))] ' +
  'shadow-[0_8px_24px_rgba(0,0,0,0.45)] [color:var(--chrome-fg)]';

/** One selectable machine. Hover tint is a token, not a hardcoded white wash. */
export const MENU_ITEM =
  'flex w-full items-center gap-2 rounded-[5px] border-0 bg-transparent px-2 py-1.5 text-left ' +
  'cursor-pointer [font-family:inherit] [color:inherit] ' +
  'hover:bg-[color-mix(in_srgb,var(--chrome-fg)_8%,transparent)] ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]';

/** Latency is only meaningful for a machine across a network. */
export function latencyLabel(target) {
  if (!target || target.is_local || !target.connected) return '';
  const ms = target.latency_ms;
  // 0 means "not measured yet", not "instantaneous" — say nothing rather than
  // claim a suspiciously perfect link.
  if (!ms) return '';
  return ms < 1 ? '<1 ms' : `${Math.round(ms)} ms`;
}
