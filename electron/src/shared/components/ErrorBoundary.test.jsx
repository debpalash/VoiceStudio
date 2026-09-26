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

// A dev-server restart (editing vite.config.js is enough) or a new build
// shipped under an open tab makes every React.lazy import fail at once with
// "Importing a module script failed". Nothing in the app is broken — the tab's
// module graph is gone — so the boundary reloads ONCE instead of painting a
// dead card in each of its instances, which is what the user hit.
describe('ErrorBoundary — stale module graph', () => {
  let reload;
  let consoleError;

  beforeEach(() => {
    sessionStorage.clear();
    reload = vi.fn();
    // setup.js already no-ops navigation, so this is a plain reassignment.
    window.location.reload = reload;
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
  });

  const boundary = (name, message) =>
    render(
      <ErrorBoundary name={name}>
        <Boom message={message} />
      </ErrorBoundary>,
    );

  it('reloads when a lazy chunk can no longer be imported', () => {
    boundary('catalogue', 'Importing a module script failed.');
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('reloads for the Chromium stale import signature with a fresh guard', () => {
    sessionStorage.clear();
    boundary('projects', 'Failed to fetch dynamically imported module: /src/x.js');
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('reloads at most once, so a genuinely missing chunk cannot loop', () => {
    boundary('clone-design', 'Importing a module script failed.');
    expect(reload).toHaveBeenCalledTimes(1);

    boundary('projects', 'Failed to fetch dynamically imported module: /src/x.js');
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Failed to fetch dynamically imported module/)).toBeInTheDocument();
  });

  it('does not loop when a failed reload takes longer than ten seconds', () => {
    sessionStorage.setItem('ov_stale_chunk_reload', String(Date.now() - 60_000));
    boundary('projects', 'Failed to fetch dynamically imported module: /src/x.js');
    expect(reload).not.toHaveBeenCalled();
  });

  it('leaves an ordinary render error to the error card', () => {
    boundary('generate', "Cannot read properties of undefined (reading 'map')");
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByText(/Cannot read properties of undefined/)).toBeInTheDocument();
  });
});
