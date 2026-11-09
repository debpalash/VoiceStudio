import React from 'react';
import { AlertCircle, BookOpen, Bug, RefreshCw, Search } from 'lucide-react';
import i18next from 'i18next';
import { classifyError, openDocsFor } from '../utils/errorDocsMap';
import { openExternal } from '../api/external';
import { buildIssueSearchUrl, openBugReport } from '../utils/bugReport';
import { Button } from '../ui';

const RELOAD_ATTEMPT_SESSION_SLOT = 'ov_stale_chunk_reload';
const MODULE_LOAD_FAILURE_WORDINGS = [
  /importing a module script failed/i,
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /failed to load module script/i,
];

function refreshAfterChunkLoss(error) {
  const detail = String(error?.message ?? error);
  const isMissingModule = MODULE_LOAD_FAILURE_WORDINGS.some((wording) => wording.test(detail));
  if (!isMissingModule) return false;

  let reloadIsArmed;
  try {
    reloadIsArmed = sessionStorage.getItem(RELOAD_ATTEMPT_SESSION_SLOT) === null;
    if (reloadIsArmed) {
      sessionStorage.setItem(RELOAD_ATTEMPT_SESSION_SLOT, `${Date.now()}`);
    }
  } catch {
    return false;
  }

  if (!reloadIsArmed) return false;
  window.location.reload();
  return true;
}

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Surface via console.error so it reaches our ring buffer (Settings > Logs > Frontend).
    // eslint-disable-next-line no-console
    console.error(`[ErrorBoundary:${this.props.name || 'anon'}]`, error, info?.componentStack);
    refreshAfterChunkLoss(error);
  }

  reset = () => this.setState({ error: null });

  openDocs = async () => {
    const cls =
      this.state.error?.errorClass /* explicit hint from the thrower */ ||
      classifyError(this.state.error);
    try {
      await openDocsFor(cls);
    } catch (err) {
      // openExternal already falls back to window.open; swallow any
      // remaining failure so the error boundary itself never throws.
      // eslint-disable-next-line no-console
      console.warn('[ErrorBoundary] openDocsFor failed', err);
    }
  };

  report = async () => {
    // Prefilled GitHub Issues URL with the scrubbed error attached — the
    // user reviews everything on github.com before anything is submitted.
    try {
      await openBugReport({ error: this.state.error });
    } catch (err) {
      console.warn('[ErrorBoundary] report failed', err);
    }
  };

  searchIssues = async () => {
    // "Has someone already hit this?" — issue search in the browser, so a
    // duplicate gets a 👍 on the existing thread instead of a new report.
    try {
      await openExternal(buildIssueSearchUrl(this.state.error));
    } catch (err) {
      console.warn('[ErrorBoundary] issue search failed', err);
    }
  };

  render() {
    if (!this.state.error) return this.props.children;

    const msg = this.state.error?.message || String(this.state.error);
    return (
      <div className="flex flex-1 items-center justify-center p-8 font-sans">
        <div className="w-full max-w-[520px] rounded-[var(--chrome-radius-pill)] border border-[color:color-mix(in_srgb,var(--chrome-severity-err)_35%,transparent)] border-l-2 border-l-[var(--chrome-severity-err)] bg-[var(--chrome-bg)] p-[22px] text-center">
          <AlertCircle
            size={32}
            color="var(--chrome-severity-err)"
            className="mb-2.5 inline-block"
          />
          <h2 className="m-0 mb-1.5 font-serif text-[1.6rem] font-normal italic tracking-[-0.01em] text-[var(--chrome-fg)]">
            {i18next.t('errors.title')}
          </h2>
          <p className="m-0 mb-3 text-[0.82rem] leading-[1.5] text-[var(--chrome-fg-muted)]">
            {i18next.t('errors.desc')}
          </p>
          <pre className="m-0 mb-3.5 max-h-[140px] overflow-auto rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-hover-bg)] px-2.5 py-2 text-left font-mono text-[0.72rem] text-[var(--chrome-severity-err)]">
            {msg}
          </pre>
          <div className="flex flex-wrap justify-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={this.reset}
              leading={<RefreshCw size={12} />}
            >
              {i18next.t('errors.tryAgain')}
            </Button>
            <Button
              variant="subtle"
              size="sm"
              onClick={this.openDocs}
              title={i18next.t('errors.openDocs')}
              leading={<BookOpen size={12} />}
            >
              {i18next.t('errors.openDocs')}
            </Button>
            <Button
              variant="subtle"
              size="sm"
              onClick={this.searchIssues}
              title={i18next.t('errors.searchIssues')}
              leading={<Search size={12} />}
            >
              {i18next.t('errors.searchIssues')}
            </Button>
            <Button
              variant="subtle"
              size="sm"
              onClick={this.report}
              title={i18next.t('reportBug.title')}
              leading={<Bug size={12} />}
            >
              {i18next.t('errors.report')}
            </Button>
          </div>
        </div>
      </div>
    );
  }
}
