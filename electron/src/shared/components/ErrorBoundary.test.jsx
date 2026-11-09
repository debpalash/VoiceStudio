import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

vi.mock('../utils/errorDocsMap', async () => {
  const actual = await vi.importActual('../utils/errorDocsMap');
  return {
    ...actual,
    openDocsFor: vi.fn(async (_cls) => {}),
  };
});

import ErrorBoundary from './ErrorBoundary';
import { openDocsFor } from '../utils/errorDocsMap';

function Boom({ message = 'pkg_resources missing' }) {
  throw new Error(message);
  // eslint-disable-next-line no-unreachable
  return null;
}

function Boom401() {
  const e = new Error('HfHubHTTPError: 401 Unauthorized');
  throw e;
}

describe('ErrorBoundary deeplink button', () => {
  beforeEach(() => {
    vi.mocked(openDocsFor).mockClear();
    // Suppress the noisy React error log that fires on a thrown render.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('renders the "Open docs for this error" button on failure', () => {
    render(
      <ErrorBoundary name="test">
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/open docs for this error/i)).toBeInTheDocument();
  });

  it('clicking the docs button calls openDocsFor with the classified class', async () => {
    render(
      <ErrorBoundary name="test">
        <Boom message="ModuleNotFoundError: pkg_resources" />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByText(/open docs for this error/i));
    expect(openDocsFor).toHaveBeenCalledTimes(1);
    expect(openDocsFor).toHaveBeenCalledWith('PKG_RESOURCES_MISSING');
  });

  it('maps a 401 HF error to HF_AUTH_FAILED', async () => {
    render(
      <ErrorBoundary name="test">
        <Boom401 />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByText(/open docs for this error/i));
    expect(openDocsFor).toHaveBeenCalledWith('HF_AUTH_FAILED');
  });

  it('still renders a docs button for unknown errors (default fallback)', () => {
    render(
      <ErrorBoundary name="test">
        <Boom message="something totally unrelated" />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByText(/open docs for this error/i));
    // classifyError returns null → openDocsFor still called → wrapper picks default
    expect(openDocsFor).toHaveBeenCalledWith(null);
  });
});

describe('stale module graph', () => {
  const crash = (message) =>
    render(
      <ErrorBoundary name="lazy-view">
        <Boom message={message} />
      </ErrorBoundary>,
    );

  let reloadPage;

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    reloadPage = vi.spyOn(window.location, 'reload').mockImplementation(() => {});
    sessionStorage.clear();
  });

  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it.each([
    ['Safari wording', 'Importing a module script failed.'],
    ['Chromium wording', 'Failed to fetch dynamically imported module: /assets/page.js'],
  ])('refreshes once for %s', (_label, message) => {
    crash(message);

    expect(sessionStorage.getItem('ov_stale_chunk_reload')).not.toBeNull();
    expect(reloadPage).toHaveBeenCalledOnce();
  });

  it('leaves a later boundary rendered instead of entering a reload loop', () => {
    const initialFailure = crash('Error loading dynamically imported module');
    initialFailure.unmount();

    crash('Failed to load module script: stale.js');

    expect(screen.getByText(/failed to load module script: stale\.js/i)).toBeInTheDocument();
    expect(reloadPage).toHaveBeenCalledOnce();
  });

  it('treats a stale timestamp as a permanent guard for the current tab', () => {
    sessionStorage.setItem('ov_stale_chunk_reload', String(Date.now() - 60_000));

    crash('Importing a module script failed');

    expect(reloadPage).not.toHaveBeenCalled();
  });

  it('renders an unrelated exception without recording a refresh', () => {
    crash('ordinary render failure');

    expect(screen.getByText('ordinary render failure')).toBeInTheDocument();
    expect(sessionStorage.getItem('ov_stale_chunk_reload')).toBeNull();
    expect(reloadPage).not.toHaveBeenCalled();
  });
});
