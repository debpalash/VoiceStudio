import { addBreadcrumb } from '@shared/utils/breadcrumbs';
import { capture, capturePageview } from '@shared/utils/analytics';

const WORKSPACES = new Set([
  'home',
  'clone',
  'personas',
  'stories',
  'dub',
  'batch',
  'gallery',
  'transcriptions',
  'design',
  'audiobook',
  'projects',
  'tools',
  'calls',
  'integrations',
  'pro',
]);

const SETTINGS_PAGES = new Set([
  'appearance',
  'general',
  'models',
  'logs',
  'media',
  'pronunciation',
  'network',
  'sharing',
  'credentials',
  'performance',
  'usage',
  'workers',
  'privacy',
  'permissions',
  'storage',
  'support',
  'updates',
  'openapi',
  'diagnostics',
]);

export const REPORT_ACTIONS = [
  'generate:clone:start',
  'generate:clone:complete',
  'generate:clone:cancel',
  'generate:clone:error',
  'generate:design:start',
  'generate:design:complete',
  'generate:design:cancel',
  'generate:design:error',
  'dub:upload',
  'dub:ingest-url',
  'dub:prepare:start',
  'dub:prepare:complete',
  'dub:prepare:cancel',
  'dub:prepare:error',
  'dub:transcribe:start',
  'dub:transcribe:complete',
  'dub:transcribe:cancel',
  'dub:transcribe:error',
  'dub:translate:start',
  'dub:translate:complete',
  'dub:translate:cancel',
  'dub:translate:error',
  'dub:generate:start',
  'dub:generate:complete',
  'dub:generate:cancel',
  'dub:generate:error',
  'transcribe:fast:start',
  'transcribe:fast:complete',
  'transcribe:fast:cancel',
  'transcribe:fast:error',
  'transcribe:accurate:start',
  'transcribe:accurate:complete',
  'transcribe:accurate:cancel',
  'transcribe:accurate:error',
] as const;

export type ReportAction = (typeof REPORT_ACTIONS)[number];
const reportActions = new Set<string>(REPORT_ACTIONS);

/** Convert a hash route into a fixed, privacy-safe action label. */
export function routeBreadcrumb(hash: string): string {
  const path = hash.replace(/^#/, '').split(/[?#]/, 1)[0] || '/';
  const segments = path.split('/').filter(Boolean);
  if (!segments.length) return 'view:home';
  const [root, child] = segments;
  if (root === 'settings')
    return `view:settings/${child && SETTINGS_PAGES.has(child) ? child : 'other'}`;
  return `view:${WORKSPACES.has(root) ? root : 'other'}`;
}

export function recordRouteBreadcrumb(hash = window.location.hash): void {
  const action = routeBreadcrumb(hash);
  addBreadcrumb(action);
  capture('screen_viewed', { stage: action });
  capturePageview(action);
}

/** Record only closed-set action names; user text, paths and URLs are rejected. */
export function recordActionBreadcrumb(action: ReportAction): void {
  if (reportActions.has(action)) {
    addBreadcrumb(action);
    capture('workflow_action', { stage: action });
  }
}
