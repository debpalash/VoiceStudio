import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { copyText } from '../utils/copyText';
import {
  ChevronUp,
  ChevronDown,
  RefreshCw,
  Trash2,
  Copy,
  Bug,
  X,
  AlertTriangle,
  AlertCircle,
  Info,
  FileText,
  Heart,
  Mail,
  Sparkles,
  Braces,
  Gem,
} from 'lucide-react';

import toast from 'react-hot-toast';
import { clearSystemLogs, clearTauriLogs } from '../api/system';
import {
  useSystemLogs,
  useTauriLogs,
  useVisibleNotifications,
  isDismissibleNotification,
} from '../api/hooks';
import { getFrontendLogs, clearFrontendLogs } from '../utils/consoleBuffer';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../store';
import NetworkToggle from './NetworkToggle';
import ComputeQuickSettings from './ComputeQuickSettings';
import { APP_VERSION, whatsNewPending } from '../utils/appVersion';
import DonateMomentPopover, { DONATE_POPOVER_AUTO_DISMISS_MS } from './DonateMomentPopover';
import { DONATION_MOMENT_EVENT, optOutOfDonationMoments } from '../utils/donationMoments';

/**
 * VSCode-style bottom panel for logs. Always-visible 28 px collapsed bar
 * shows error/warning counts per source (Backend, Frontend, Tauri); click
 * any pill or the chevron to expand into a resizable panel. State — which
 * tab is active, is it collapsed, panel height — persists in localStorage
 * so the panel remembers where the user left it across launches.
 */

const SOURCES = [
  { id: 'backend', label: 'Backend', icon: FileText },
  { id: 'frontend', label: 'Frontend', icon: FileText },
  { id: 'tauri', label: 'Tauri', icon: FileText },
  // Notifications used to live here as a 4th pill but that duplicated the
  // header's bell+badge (single source of truth for notifications). The
  // footer is logs-only now; bell handles notifications.
  // Updates tab moved to Settings → Updates (see Settings.jsx).
];

const LS_HEIGHT = 'omnivoice.logs.height';
const LS_ACTIVE = 'omnivoice.logs.active';

const MIN_H = 180;
const MAX_H = 720;

// Severity heuristics. We scan each line for these keywords so the UI
// can show badge counts per source without waiting for a structured
// logger. Matches word-boundary to avoid false positives on identifiers
// like `warning_count` or `error_handler`.
const RE_ERROR = /\b(error|fatal|exception|traceback)\b/i;
const RE_WARN = /\b(warn(ing)?|deprecated)\b/i;

function classifyLine(raw) {
  const text = typeof raw === 'string' ? raw : (raw?.msg ?? String(raw));
  if (RE_ERROR.test(text)) return 'error';
  if (RE_WARN.test(text)) return 'warn';
  return 'info';
}

function countLevels(lines) {
  let error = 0,
    warn = 0;
  for (const l of lines) {
    const sev = classifyLine(l);
    if (sev === 'error') error++;
    else if (sev === 'warn') warn++;
  }
  return { error, warn, total: lines.length };
}

function formatFrontendLine(entry) {
  const ts = new Date(entry.t).toISOString().slice(11, 23);
  return `[${ts}] ${entry.level.toUpperCase()} ${entry.msg}`;
}

function SeverityIcon({ level, size = 11 }) {
  if (level === 'error') return <AlertCircle size={size} color="#fb4934" />;
  if (level === 'warn') return <AlertTriangle size={size} color="#fabd2f" />;
  return <Info size={size} color="#7c6f64" />;
}

// UiScaleToggle and ThemePicker used to live here as always-visible
// controls in the footer chrome. Moved to Settings → Appearance (see
// AppearancePanel) so the footer can stay focused on logs. The store
// fields (uiScale, theme, setUiScale, setTheme) are unchanged; only the
// rendering moved.

// ── Tailwind utility-string presets (replacing the old `logs-footer__*` BEM
// classes). Chrome palette is referenced via `--chrome-*` arbitrary values so
// the footer keeps its fixed dark-chrome look (same tokens Header uses).
// Severity colors stay as literal arbitrary values (#fb4934 / #fabd2f /
// #83a598) — NOT text-danger/text-accent — so the footer chrome stays fixed
// across themes (the token utilities recolor per [data-theme]).

const ICON_BTN =
  'flex items-center justify-center w-[var(--chrome-icon-btn)] h-[var(--chrome-icon-btn)] ' +
  'rounded-[3px] bg-transparent border-0 cursor-pointer transition-all ' +
  '[color:var(--chrome-fg-muted)] hover:[color:var(--chrome-fg)] hover:[background:var(--chrome-hover-bg)] ' +
  'disabled:opacity-40 disabled:cursor-not-allowed';

// Report button: same shell but hovers warn-yellow instead of chrome-fg.
const ICON_BTN_REPORT =
  'flex items-center justify-center w-[var(--chrome-icon-btn)] h-[var(--chrome-icon-btn)] ' +
  'rounded-[3px] bg-transparent border-0 cursor-pointer transition-all ' +
  '[color:var(--chrome-fg-muted)] hover:[color:#fabd2f] hover:[background:var(--chrome-hover-bg)]';

const PILL_BASE =
  'inline-flex items-center gap-[6px] px-[8px] py-[2px] h-[var(--chrome-pill-h)] rounded-[3px] ' +
  'bg-transparent border text-[11px] cursor-pointer transition-all [font-family:inherit] ' +
  'hover:[background:var(--chrome-hover-bg)] hover:[color:var(--chrome-fg)]';

// Count-badge layout/typography (severity bg+color appended per case).
const BADGE_LAYOUT =
  'inline-flex items-center justify-center min-w-[18px] px-[5px] h-[14px] rounded-[7px] ' +
  'text-[10px] font-semibold [font-family:var(--chrome-font-mono)]';
const BADGE_NEUTRAL = `${BADGE_LAYOUT} [background:rgba(255,255,255,0.08)] [color:var(--chrome-fg)]`;
const BADGE_ERROR = `${BADGE_LAYOUT} [background:rgba(251,73,52,0.2)] [color:#fb4934]`;
const BADGE_WARN = `${BADGE_LAYOUT} [background:rgba(250,189,47,0.2)] [color:#fabd2f]`;

const DISCORD_BTN =
  'flex items-center justify-center w-[var(--chrome-icon-btn)] h-[var(--chrome-icon-btn)] shrink-0 ' +
  'rounded-[4px] bg-transparent border-0 cursor-pointer [color:#7289da] opacity-60 ' +
  'transition-[color,opacity,transform] duration-150 hover:opacity-100 hover:[color:#5865F2] hover:scale-110';

// API-reference button: same compact footer-icon shell as Discord/Mail but on
// the neutral chrome-muted palette, hovering to the theme accent.
const API_REF_BTN =
  'flex items-center justify-center w-[var(--chrome-icon-btn)] h-[var(--chrome-icon-btn)] shrink-0 ' +
  'rounded-[4px] bg-transparent border-0 cursor-pointer [color:var(--chrome-fg-muted)] opacity-70 ' +
  'transition-[color,opacity,transform] duration-150 hover:opacity-100 hover:[color:var(--chrome-accent)] hover:scale-110';

const DONATE_BTN =
  'flex items-center justify-center w-[var(--chrome-icon-btn)] h-[var(--chrome-icon-btn)] shrink-0 ' +
  'rounded-[4px] bg-transparent border-0 cursor-pointer [color:#d3869b] ' +
  'transition-[color,transform] duration-150 hover:[color:var(--chrome-accent)] hover:scale-[1.15]';
// Idle glow vs. the gentle attention pulse while the donation-moment popover
// is open. Split from DONATE_BTN so exactly one animation applies at a time.
const HEART_GLOW =
  '[animation:heart-glow_2.5s_ease-in-out_infinite] motion-reduce:[animation:none]';
const HEART_PULSE =
  '[animation:donate-heart-pulse_1.1s_ease-in-out_infinite] motion-reduce:[animation:none]';

function SourcePill({ source, counts, active, onClick, icon: Icon }) {
  const hasErrors = counts.error > 0;
  const hasWarns = counts.warn > 0;
  // Severity color wins over active wins over muted (matches old cascade).
  const colorClass = hasErrors
    ? '[color:#fb4934]'
    : hasWarns
      ? '[color:#fabd2f]'
      : active
        ? '[color:var(--chrome-accent)]'
        : '[color:var(--chrome-fg-muted)]';
  const activeClass = active
    ? '[background:var(--chrome-accent-bg)] [border-color:var(--chrome-accent-border)]'
    : 'border-transparent';
  return (
    <button
      type="button"
      className={`${PILL_BASE} ${activeClass} ${colorClass}`}
      onClick={onClick}
      aria-label={`${source.label} logs${hasErrors ? `, ${counts.error} errors` : hasWarns ? `, ${counts.warn} warnings` : ''}`}
    >
      {Icon && <Icon size={12} className="shrink-0" aria-hidden="true" />}
      <span className="font-medium">{source.label}</span>
      {hasErrors && <span className={BADGE_ERROR}>{counts.error}</span>}
      {!hasErrors && hasWarns && <span className={BADGE_WARN}>{counts.warn}</span>}
      {!hasErrors && !hasWarns && counts.total > 0 && (
        <span className={BADGE_NEUTRAL}>{counts.total}</span>
      )}
    </button>
  );
}

// ── Seasonal / random donate heart ──────────────────────────────────────
// Christmas (Dec), Diwali (~Oct-Nov), Valentine's (Feb), Eid (~Mar-Apr),
// default pool rotates daily based on day-of-year.
const HEART_POOL = ['❤️', '🩷', '💜', '💙', '🧡', '💛', '🩵', '💖', '💗'];
const SEASONAL = [
  { month: 12, emoji: '🎄', color: '#e74c3c', title: 'Merry Christmas! Support this project' },
  { month: 2, emoji: '💝', color: '#ff6b81', title: "Happy Valentine's! Support this project" },
  // Diwali window — roughly Kartik Amavasya (Oct–Nov)
  { month: 10, emoji: '🪔', color: '#f5a623', title: 'Happy Diwali! Support this project' },
  { month: 11, emoji: '✨', color: '#f5a623', title: 'Happy Diwali! Support this project' },
];

function DonateHeart() {
  const now = new Date();
  const month = now.getMonth() + 1;
  const dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 86400000);

  const seasonal = SEASONAL.find((s) => s.month === month);
  if (seasonal) {
    return (
      <span style={{ fontSize: 14, lineHeight: 1 }} title={seasonal.title}>
        {seasonal.emoji}
      </span>
    );
  }
  // Rotate through the pool daily
  const pick = HEART_POOL[dayOfYear % HEART_POOL.length];
  return <span style={{ fontSize: 14, lineHeight: 1 }}>{pick}</span>;
}

export default function LogsFooter() {
  // Always start collapsed on every launch — per-session toggling works
  // but nothing persists. Kill the legacy key on the way out so users
  // who had it stored as "open" before aren't stuck on the next load.
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem('omnivoice.logs.collapsed');
  }
  const [collapsed, setCollapsed] = useState(true);
  const { t } = useTranslation();
  // Update availability drives the footer version badge's notification dot.
  const updateStatus = useAppStore((s) => s.updateStatus);
  const updateVersion = useAppStore((s) => s.updateVersion);
  const updateReady = updateStatus === 'available' || updateStatus === 'ready';
  // One-time "What's new" affordance after an update (feat/safe-updates):
  // non-blocking footer pill, never a startup modal. First run with no
  // recorded version baselines silently; after an update the pill shows
  // until the user opens the notes (or clicks it away).
  const whatsNewSeen = useAppStore((s) => s.whatsNewSeenVersion);
  useEffect(() => {
    if (whatsNewSeen == null && APP_VERSION !== 'unknown') {
      useAppStore.getState().setWhatsNewSeenVersion(APP_VERSION);
    }
  }, [whatsNewSeen]);
  const showWhatsNew = whatsNewPending(whatsNewSeen, APP_VERSION);
  const openWhatsNew = useCallback(() => {
    useAppStore.getState().setWhatsNewSeenVersion(APP_VERSION);
    useAppStore.getState().openSettingsTab?.('updates');
  }, []);
  const [height, setHeight] = useState(() => {
    const v = Number(localStorage.getItem(LS_HEIGHT));
    return Number.isFinite(v) && v >= MIN_H && v <= MAX_H ? v : 300;
  });
  const [active, setActive] = useState(() => {
    const v = localStorage.getItem(LS_ACTIVE);
    return SOURCES.some((s) => s.id === v) ? v : 'backend';
  });

  // Only the local console ring needs state; HTTP logs render from their query.
  const [frontendLines, setFrontendLines] = useState(() =>
    getFrontendLogs().map(formatFrontendLine),
  );
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => localStorage.setItem(LS_HEIGHT, String(height)), [height]);
  useEffect(() => localStorage.setItem(LS_ACTIVE, active), [active]);

  // Expose the current footer height as a CSS variable on :root. Inside the
  // studio shell the footer is a grid ROW (index.css .app-container), so
  // content clearance needs no variable — but the fixed toasts/previews that
  // anchor above the footer (VoicePreview, ExportModal, …) and the
  // setup-wizard wrapper still position off --logs-footer-height.
  useEffect(() => {
    const h = collapsed ? 28 : height;
    document.documentElement.style.setProperty('--logs-footer-height', `${h}px`);
    return () => {
      document.documentElement.style.setProperty('--logs-footer-height', '28px');
    };
  }, [collapsed, height]);

  // ── TanStack Query for backend + tauri logs ────────────────────────────
  // While collapsed only the count badge is visible, so poll lazily (45s);
  // tighten to 10s when the panel is open (same throttle idea as pullFrontend).
  const backendLogs = useSystemLogs(300, true, collapsed ? 45_000 : 10_000);
  const tauriLogs = useTauriLogs(300, true, collapsed ? 45_000 : 10_000);

  const lines = useMemo(
    () => ({
      backend: backendLogs.data?.lines || [],
      frontend: frontendLines,
      tauri: tauriLogs.data?.lines || [],
    }),
    [backendLogs.data, tauriLogs.data, frontendLines],
  );

  // Skip the state update (and the re-render it forces) when the console ring
  // buffer hasn't changed since the last pull — same length + same last
  // timestamp means nothing new arrived.
  const lastFrontendPull = useRef({ len: -1, t: 0 });
  const pullFrontend = useCallback(() => {
    const raw = getFrontendLogs();
    const lastT = raw.length ? raw[raw.length - 1].t : 0;
    const seen = lastFrontendPull.current;
    if (raw.length === seen.len && lastT === seen.t) return;
    lastFrontendPull.current = { len: raw.length, t: lastT };
    setFrontendLines(raw.map(formatFrontendLine));
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    backendLogs.refetch();
    tauriLogs.refetch();
    pullFrontend();
    setLoading(false);
  }, [backendLogs, tauriLogs, pullFrontend]);

  // Frontend logs still need a local interval (no API, reads from buffer)
  useEffect(() => {
    pullFrontend();
    const iv = setInterval(pullFrontend, collapsed ? 8000 : 3000);
    return () => clearInterval(iv);
  }, [pullFrontend, collapsed]);

  // ── Notifications (shared TanStack Query cache with the header bell) ────
  // Already filtered to what the user hasn't dismissed — badge and tab agree.
  const { notifications, isSuccess: notificationsReady } = useVisibleNotifications();
  const allSourcesReady = backendLogs.isSuccess && tauriLogs.isSuccess && notificationsReady;
  const dismissNotification = useAppStore((s) => s.dismissNotification);

  // ── Donation moment popover (see utils/donationMoments.js) ─────────────
  // The eligibility engine dispatches DONATION_MOMENT_EVENT after a rare,
  // gated value-creation success; the footer just renders the speech bubble
  // above the heart and auto-dismisses it. null = closed.
  const [donateMoment, setDonateMoment] = useState(null);
  useEffect(() => {
    const onMoment = (e) => setDonateMoment({ line: e?.detail?.line ?? 0 });
    window.addEventListener(DONATION_MOMENT_EVENT, onMoment);
    return () => window.removeEventListener(DONATION_MOMENT_EVENT, onMoment);
  }, []);
  useEffect(() => {
    if (!donateMoment) return undefined;
    const timer = setTimeout(() => setDonateMoment(null), DONATE_POPOVER_AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [donateMoment]);

  // Allow header bell to open notifications tab
  useEffect(() => {
    const handler = () => {
      setActive('notifications');
      setCollapsed(false);
    };
    window.addEventListener('omni:open-notifications', handler);
    return () => window.removeEventListener('omni:open-notifications', handler);
  }, []);

  // Auto-scroll to bottom when new lines arrive and panel is open.
  useEffect(() => {
    if (collapsed) return;
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [lines, active, collapsed]);

  const counts = useMemo(
    () => ({
      backend: countLevels(lines.backend),
      frontend: countLevels(lines.frontend),
      tauri: countLevels(lines.tauri),
      notifications: {
        error: notifications.filter((n) => n.level === 'error').length,
        warn: notifications.filter((n) => n.level === 'warn').length,
        total: notifications.length,
      },
    }),
    [lines, notifications],
  );

  const openTo = (id) => {
    setActive(id);
    setCollapsed(false);
  };

  // Combined backend+frontend+tauri counts for the single collapsed "Logs" tab.
  const mergedCounts = useMemo(() => {
    const acc = { error: 0, warn: 0, total: 0 };
    for (const s of SOURCES) {
      const c = counts[s.id] || { error: 0, warn: 0, total: 0 };
      acc.error += c.error;
      acc.warn += c.warn;
      acc.total += c.total;
    }
    return acc;
  }, [counts]);

  // ── Resize handle (drag the top edge) ───────────────────────────────
  const dragRef = useRef(null);
  const onDragStart = (e) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = height;
    const move = (ev) => {
      const dy = startY - ev.clientY;
      const next = Math.min(MAX_H, Math.max(MIN_H, startH + dy));
      setHeight(next);
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  // ── Actions ─────────────────────────────────────────────────────────
  const onClear = async () => {
    try {
      if (active === 'backend') {
        await clearSystemLogs();
        await backendLogs.refetch({ throwOnError: true });
      } else if (active === 'tauri') {
        await clearTauriLogs();
        await tauriLogs.refetch({ throwOnError: true });
      } else if (active === 'frontend') {
        clearFrontendLogs();
        pullFrontend();
      }
      toast.success(t('logs.log_cleared', { source: active }));
    } catch (e) {
      toast.error(t('logs.clear_failed', { message: e?.message || e }));
    }
  };

  const onCopy = async () => {
    try {
      const raw = (lines[active] || []).join('\n');
      await copyText(raw);
      toast.success(t('logs.log_copied', { source: active }));
    } catch (e) {
      toast.error(t('logs.copy_failed', { message: e?.message || e }));
    }
  };

  const onReportIssue = async () => {
    // Collate a short diagnostic dump — last 80 lines per source + counts
    // + user agent — onto the clipboard so the user can paste into a
    // GitHub issue without hand-collecting files.
    const header = [
      `VoiceStudio — diagnostic report`,
      `When: ${new Date().toISOString()}`,
      `UA: ${navigator.userAgent}`,
      `Counts: backend err=${counts.backend.error}/warn=${counts.backend.warn}, ` +
        `frontend err=${counts.frontend.error}/warn=${counts.frontend.warn}, ` +
        `tauri err=${counts.tauri.error}/warn=${counts.tauri.warn}`,
      '',
    ].join('\n');
    const body = SOURCES.map((s) => {
      const l = lines[s.id] || [];
      return (
        `── ${s.label} (last ${Math.min(l.length, 80)} of ${l.length}) ──────────────\n` +
        l.slice(-80).join('\n')
      );
    }).join('\n\n');
    try {
      await copyText(header + body);
      toast.success(t('logs.report_copied'));
    } catch (e) {
      toast.error(t('logs.report_failed', { message: e?.message || e }));
    }
  };

  // ── Render ──────────────────────────────────────────────────────────
  const current = lines[active] || [];

  return (
    <div
      className={['logs-footer', collapsed ? 'logs-footer--collapsed' : 'logs-footer--open'].join(
        ' ',
      )}
      style={collapsed ? undefined : { height }}
    >
      {!collapsed && (
        <div
          ref={dragRef}
          className="absolute top-[-2px] left-0 right-0 h-[5px] cursor-ns-resize z-[1] hover:[background:var(--chrome-accent-border)]"
          onMouseDown={onDragStart}
          title={t('logs.drag_resize')}
        />
      )}

      <div className="flex items-center justify-between h-[28px] px-[8px] gap-[8px] [border-bottom:1px_solid_rgba(255,255,255,0.04)] shrink-0">
        <div className="flex items-center gap-[8px] min-w-0 flex-1 overflow-hidden">
          {/* UI scale + theme picker moved to Settings → Appearance.
              Footer is logs-focused now; rarely-used display prefs don't
              belong in always-visible chrome. */}
          <button
            type="button"
            className={ICON_BTN}
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? t('logs.expand') : t('logs.collapse')}
            aria-label={collapsed ? t('logs.expand_aria') : t('logs.collapse_aria')}
            aria-expanded={!collapsed}
          >
            {collapsed ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
          {collapsed ? (
            /* Collapsed: a single merged "Logs" tab with the combined count.
               Expanding reveals the per-source filter tabs below. */
            <SourcePill
              source={{ id: 'logs', label: t('logs.title') }}
              icon={FileText}
              counts={mergedCounts}
              active={false}
              onClick={() => openTo(SOURCES.some((s) => s.id === active) ? active : 'backend')}
            />
          ) : (
            <>
              <span className="[color:var(--chrome-fg-muted)] text-[11px] uppercase tracking-[0.06em] mr-[4px]">
                {t('logs.title')}
              </span>
              {SOURCES.map((s) => (
                <SourcePill
                  key={s.id}
                  source={s}
                  counts={counts[s.id]}
                  active={active === s.id}
                  onClick={() => setActive(s.id)}
                />
              ))}
            </>
          )}
        </div>
        <div className="flex items-center gap-[4px] shrink-0">
          {!collapsed && (
            <div className="flex items-center gap-[2px]">
              <button
                className={ICON_BTN}
                onClick={refreshAll}
                disabled={loading}
                title={t('logs.refresh')}
                aria-label={t('logs.refresh_aria')}
              >
                <RefreshCw size={12} className={loading ? 'motion-safe:animate-spin' : ''} />
              </button>
              <button
                className={ICON_BTN}
                onClick={onCopy}
                title={t('logs.copy_visible')}
                aria-label={t('logs.copy_visible_aria')}
              >
                <Copy size={12} />
              </button>
              <button
                className={ICON_BTN}
                onClick={onClear}
                title={t('logs.clear')}
                aria-label={t('logs.clear_aria')}
              >
                <Trash2 size={12} />
              </button>
              <button
                className={ICON_BTN_REPORT}
                onClick={onReportIssue}
                title={t('logs.report_issue')}
                aria-label={t('logs.report_issue_aria')}
              >
                <Bug size={12} />
              </button>
              <button
                className={ICON_BTN}
                onClick={() => setCollapsed(true)}
                title={t('logs.close')}
                aria-label={t('logs.close_aria')}
              >
                <X size={12} />
              </button>
            </div>
          )}
          {showWhatsNew && (
            <button
              type="button"
              data-testid="whats-new-pill"
              className={
                'shrink-0 inline-flex items-center gap-[4px] px-[7px] h-[var(--chrome-icon-btn)] rounded-[999px] cursor-pointer ' +
                'text-[10px] tracking-[0.02em] border border-[color:var(--chrome-accent)] bg-transparent ' +
                '[color:var(--chrome-accent)] hover:opacity-80 transition-opacity duration-150'
              }
              onClick={openWhatsNew}
              title={t('updates.whats_new_in', {
                version: APP_VERSION,
                defaultValue: "What's new in v{{version}}",
              })}
              aria-label={t('updates.whats_new_in', {
                version: APP_VERSION,
                defaultValue: "What's new in v{{version}}",
              })}
            >
              <Sparkles size={11} aria-hidden="true" />
              {t('update.whats_new', { defaultValue: "What's new" })}
            </button>
          )}
          <button
            type="button"
            className={
              'shrink-0 px-[6px] h-[var(--chrome-icon-btn)] rounded-[4px] bg-transparent border-0 cursor-pointer ' +
              'text-[11px] [font-variant-numeric:tabular-nums] tracking-[0.02em] transition-[opacity,color] duration-150 ' +
              'hover:opacity-100 hover:[color:var(--chrome-accent)] hover:underline ' +
              (updateReady
                ? 'relative opacity-100 font-semibold [color:var(--chrome-accent)]'
                : 'opacity-55 [color:var(--chrome-fg)]')
            }
            onClick={() => useAppStore.getState().openSettingsTab?.('updates')}
            title={
              updateReady
                ? t('logs.update_available', {
                    version: updateVersion || '',
                    defaultValue: 'Update available ({{version}}) — click to update',
                  })
                : t('logs.version_updates', { defaultValue: 'Check for updates' })
            }
            aria-label={
              updateReady
                ? t('logs.update_available_aria', {
                    version: updateVersion || '',
                    defaultValue: 'Update available ({{version}}) — open updates',
                  })
                : t('logs.version_updates_aria', {
                    defaultValue: 'Open updates — app version {{v}}',
                    v: APP_VERSION,
                  })
            }
          >
            v{APP_VERSION}
            {updateReady && (
              <span
                className="inline-block w-[6px] h-[6px] ml-[5px] rounded-full align-middle [background:var(--chrome-accent)] [animation:version-dot-pulse_2s_ease-in-out_infinite]"
                aria-hidden="true"
              />
            )}
          </button>
          <ComputeQuickSettings />
          <NetworkToggle />
          <button
            type="button"
            className={API_REF_BTN}
            onClick={() => useAppStore.getState().openSettingsTab?.('openapi')}
            title={t('logs.open_api', { defaultValue: 'API reference' })}
            aria-label={t('logs.open_api_aria', { defaultValue: 'Open the OpenAPI reference' })}
          >
            <Braces size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={DISCORD_BTN}
            onClick={() => {
              import('../api/external').then((m) =>
                m.openExternal('https://discord.gg/bzQavDfVV9'),
              );
            }}
            title={t('logs.join_discord')}
            aria-label={t('logs.join_discord_aria')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.947 2.418-2.157 2.418z" />
            </svg>
          </button>
          <button
            type="button"
            className={DISCORD_BTN}
            onClick={() => useAppStore.getState().setMode?.('contact')}
            title={t('logs.contact', { defaultValue: 'Contact' })}
            aria-label={t('logs.contact_aria', { defaultValue: 'Open the contact page' })}
          >
            <Mail size={14} />
          </button>
          {/* Sponsors — a compact link (never a logo strip in the 28px bar).
              Opens the Support page, whose Sponsors section holds the logo
              grid + "Become a sponsor" affordance. */}
          <button
            type="button"
            className={
              'shrink-0 inline-flex items-center gap-[4px] px-[7px] h-[var(--chrome-icon-btn)] rounded-[4px] ' +
              'bg-transparent border-0 cursor-pointer text-[11px] tracking-[0.02em] ' +
              '[color:var(--chrome-fg-muted)] transition-[color,opacity] duration-150 ' +
              'hover:[color:var(--chrome-accent)]'
            }
            onClick={() => useAppStore.getState().setMode?.('donate')}
            title={t('logs.sponsors', { defaultValue: 'Sponsors' })}
            aria-label={t('logs.sponsors_aria', {
              defaultValue: 'View sponsors and support the project',
            })}
          >
            <Gem size={12} aria-hidden="true" />
            {t('logs.sponsors', { defaultValue: 'Sponsors' })}
          </button>
          <div className="relative inline-flex shrink-0 ml-[4px]">
            <button
              type="button"
              className={`${DONATE_BTN} ${donateMoment ? HEART_PULSE : HEART_GLOW}`}
              onClick={() => {
                // Manual entry is unchanged: the heart always opens the full
                // donate view (and quietly retires an open popover).
                setDonateMoment(null);
                useAppStore.getState().setMode?.('donate');
              }}
              title={t('logs.support_project')}
              aria-label={t('logs.support_project_aria')}
            >
              <DonateHeart />
            </button>
            {donateMoment && (
              <DonateMomentPopover
                line={donateMoment.line}
                onLater={() => setDonateMoment(null)}
                onOptOut={() => {
                  optOutOfDonationMoments();
                  // Mirror into the legacy postcard flag so both engines stay
                  // permanently silenced no matter which one is wired.
                  useAppStore.getState().optOutOfDonation?.();
                  setDonateMoment(null);
                }}
              />
            )}
          </div>
        </div>
      </div>

      {!collapsed && active !== 'notifications' && (
        <div
          ref={scrollRef}
          className="logs-footer__body flex-1 min-h-0 overflow-y-auto overflow-x-auto py-[4px] px-[8px] select-text"
        >
          {current.length === 0 && (
            <div className="p-[12px] [color:var(--chrome-fg-dim)] not-italic text-[11px] text-center">
              {active === 'frontend' ? t('logs.empty_frontend_short') : t('logs.empty_lines')}
            </div>
          )}
          {current.map((line, i) => {
            const level = classifyLine(line);
            const lineBg =
              level === 'error'
                ? '[background:rgba(251,73,52,0.06)]'
                : level === 'warn'
                  ? '[background:rgba(250,189,47,0.04)]'
                  : '';
            const lineColor =
              level === 'error'
                ? '[color:#fb4934]'
                : level === 'warn'
                  ? '[color:#fabd2f]'
                  : '[color:#ebdbb2]';
            return (
              <div
                key={i}
                className={`flex items-start gap-[6px] px-[2px] py-[1px] rounded-[2px] ${lineBg}`}
              >
                <span className="pt-[2px] shrink-0">
                  <SeverityIcon level={level} />
                </span>
                <pre
                  className={`m-0 p-0 [font-family:inherit] text-[11.5px] leading-[1.5] whitespace-pre-wrap [word-break:break-word] flex-1 ${lineColor}`}
                >
                  {typeof line === 'string' ? line : JSON.stringify(line)}
                </pre>
              </div>
            );
          })}
        </div>
      )}

      {!collapsed && active === 'notifications' && (
        <div className="logs-footer__body flex flex-col gap-[2px] px-[8px] py-[6px] flex-1 min-h-0 overflow-y-auto select-text">
          {allSourcesReady &&
          notifications.length === 0 &&
          mergedCounts.error === 0 &&
          mergedCounts.warn === 0 ? (
            <div className="p-[12px] [color:var(--chrome-fg-dim)] not-italic text-[11px] text-center">
              {t('logs.all_clear')}
            </div>
          ) : (
            notifications.map((notif) => {
              // Severity border-left (literal colors, fixed across themes) +
              // 1px transparent on the other sides for layout parity.
              const sides =
                '[border-top:1px_solid_transparent] [border-right:1px_solid_transparent] [border-bottom:1px_solid_transparent]';
              const notifBorder =
                notif.level === 'error'
                  ? `[border-left:2px_solid_#fb4934] ${sides}`
                  : notif.level === 'warn'
                    ? `[border-left:2px_solid_#fabd2f] ${sides}`
                    : `[border-left:2px_solid_#83a598] ${sides}`;
              const notifHover = notif.action
                ? 'cursor-pointer hover:[background:rgba(255,255,255,0.06)]'
                : 'hover:[background:rgba(255,255,255,0.04)]';
              return (
                <div
                  key={notif.id}
                  className={`flex gap-[8px] items-start px-[10px] py-[8px] rounded-md [background:rgba(255,255,255,0.02)] ${notifBorder} ${notifHover}`}
                  onClick={() => {
                    if (!notif.action) return;
                    // Acting on the crash notice acknowledges it — the backend
                    // stores the seen crash-log size so it doesn't re-fire
                    // every session until a NEW crash grows the log.
                    if (notif.id === 'crash-last-session') {
                      import('../api/client')
                        .then(({ apiFetch }) => apiFetch('/system/crash/ack', { method: 'POST' }))
                        .catch(() => {});
                    }
                    // Same contract for the run-sentinel notice (#1164):
                    // acting on it watermarks the record server-side, so it
                    // stops re-firing — a NEW unclean death re-arms (its id
                    // carries a fresh detected_at).
                    if (notif.id?.startsWith('last-run-crash-')) {
                      import('../api/client')
                        .then(({ apiFetch }) =>
                          apiFetch('/system/last-run-crash/ack', { method: 'POST' }),
                        )
                        .catch(() => {});
                    }
                    if (notif.action.type === 'navigate') {
                      useAppStore.getState().setMode?.(notif.action.target);
                      setCollapsed(true);
                    } else if (notif.action.type === 'settings-tab') {
                      useAppStore.getState().openSettingsTab?.(notif.action.target);
                      setCollapsed(true);
                    } else if (notif.action.type === 'link') {
                      import('../api/external').then((m) => m.openExternal(notif.action.target));
                    }
                  }}
                  role={notif.action ? 'button' : undefined}
                  tabIndex={notif.action ? 0 : undefined}
                >
                  <span className="shrink-0 mt-[2px]">
                    <SeverityIcon level={notif.level} />
                  </span>
                  <div className="flex flex-col gap-[2px] flex-1 min-w-0">
                    <strong className="text-[12px] [color:var(--color-fg)]">{notif.title}</strong>
                    <span className="text-[11px] text-fg-muted leading-[1.5]">{notif.message}</span>
                  </div>
                  {notif.action && (
                    <span className="shrink-0 text-[11px] font-semibold text-brand whitespace-nowrap">
                      {notif.action.label} →
                    </span>
                  )}
                  {isDismissibleNotification(notif) && notif.id && (
                    <button
                      className="shrink-0 flex h-[18px] w-[18px] cursor-pointer items-center justify-center rounded border-0 bg-transparent p-0 text-fg-muted transition-colors hover:text-fg hover:[background:rgba(255,255,255,0.08)]"
                      onClick={(e) => {
                        // The row itself may navigate; hiding must not.
                        e.stopPropagation();
                        dismissNotification(notif.id);
                      }}
                      aria-label={t('common.dismiss')}
                      title={t('common.dismiss')}
                    >
                      <X size={11} />
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
