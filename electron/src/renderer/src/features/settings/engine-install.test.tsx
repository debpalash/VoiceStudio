import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import i18next from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { afterEach, describe, expect, it, vi } from 'vitest';
import en from '@/i18n/locales/en.json';
import pl from '@/i18n/locales/pl.json';
import { EngineInstall } from './engine-install';

const clients: QueryClient[] = [];
const layoutCode = 'windows_rocm_voxcpm2_layout';
const remediation = 'docs/install/windows-rocm.md';

afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function mountInstall({
  language,
  catalog,
  id = 'voxcpm2',
  jobError = layoutCode,
  state = 'failed',
  statusError,
  actionError,
}: {
  language: string;
  catalog: typeof en | typeof pl;
  id?: string;
  jobError?: string;
  state?: string;
  statusError?: string;
  actionError?: string;
}) {
  const translator = i18next.createInstance();
  await translator.init({
    lng: language,
    fallbackLng: false,
    resources: { [language]: { translation: catalog } },
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (input === `/api/engines/sidecar/${id}/install/status`) {
      return new Response(
        JSON.stringify(
          statusError
            ? { detail: { code: layoutCode, message: statusError } }
            : {
                installed: false,
                install_allowed: true,
                job: { state, steps: [], error: jobError, remediation },
              },
        ),
        { status: statusError ? 503 : 200 },
      );
    }
    if (input === `/api/engines/sidecar/${id}/install` && init?.method === 'POST') {
      return new Response(JSON.stringify(actionError ? { detail: actionError } : {}), {
        status: actionError ? 500 : 200,
      });
    }
    throw new Error(`Unexpected test request: ${String(input)}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  const route = createRootRoute({ component: () => <EngineInstall id={id} /> });
  const router = createRouter({
    routeTree: route,
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  render(
    <I18nextProvider i18n={translator}>
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </I18nextProvider>,
  );
  return fetchMock;
}

describe.each([
  { language: 'en', catalog: en },
  { language: 'pl', catalog: pl },
])('EngineInstall linked-environment errors in $language', ({ language, catalog }) => {
  it('localizes only the exact VoxCPM2 job code and retains remediation', async () => {
    await mountInstall({ language, catalog });
    const alert = await screen.findByRole('alert');
    expect(alert.querySelector('pre')?.textContent).toBe(
      `${catalog.engines.voxcpm2LayoutError}\n\n${remediation}`,
    );
    expect(within(alert).queryByText(layoutCode)).toBeNull();
    expect(within(alert).getByText(catalog.modelMaintenance.failed)).toBeInTheDocument();
    fireEvent.click(within(alert).getByText(catalog.profileIdentity.details));
    expect(within(alert).getByRole('link', { name: catalog.settings.logs })).toHaveAttribute(
      'href',
      '/settings/logs',
    );
    expect(screen.getByRole('button', { name: catalog.modelMaintenance.install })).toBeEnabled();
  });

  it.each([
    { id: 'cosyvoice', jobError: layoutCode },
    { id: 'voxcpm2', jobError: `${layoutCode}_other` },
    { id: 'voxcpm2', jobError: 'Existing English job diagnostic' },
  ])('keeps unrelated job details for $id: $jobError', async ({ id, jobError }) => {
    await mountInstall({ language, catalog, id, jobError });
    const alert = await screen.findByRole('alert');
    expect(alert.querySelector('pre')?.textContent).toBe(`${jobError}\n\n${remediation}`);
  });

  it('keeps status-request errors outside the job-code mapping', async () => {
    const statusError = 'The status endpoint could not inspect this environment.';
    await mountInstall({ language, catalog, statusError });
    const alert = await screen.findByRole('alert');
    expect(alert.querySelector('pre')?.textContent).toBe(statusError);
    expect(screen.getByRole('button', { name: catalog.modelMaintenance.install })).toBeDisabled();
  });

  it('dismisses the failure and permits an explicit install retry', async () => {
    const fetchMock = await mountInstall({ language, catalog });
    const alert = await screen.findByRole('alert');
    fireEvent.click(within(alert).getByRole('button', { name: catalog.common.dismiss }));
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: catalog.modelMaintenance.install }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/engines/sidecar/voxcpm2/install', {
        method: 'POST',
      }),
    );
    const retriedAlert = await screen.findByRole('alert');
    expect(retriedAlert.querySelector('pre')?.textContent).toBe(
      `${catalog.engines.voxcpm2LayoutError}\n\n${remediation}`,
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: catalog.modelMaintenance.install })).toBeEnabled(),
    );
  });

  it('preserves install-action error precedence over a previous job failure', async () => {
    const actionError = 'The install request was rejected.';
    await mountInstall({ language, catalog, actionError });
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: catalog.modelMaintenance.install }));
    await waitFor(() =>
      expect(screen.getByRole('alert').querySelector('pre')?.textContent).toBe(actionError),
    );
    expect(screen.getByRole('button', { name: catalog.modelMaintenance.install })).toBeEnabled();
  });

  it('keeps a running installation disabled without displaying a stale error', async () => {
    await mountInstall({ language, catalog, state: 'running' });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: catalog.modelMaintenance.installing })).toBeDisabled(),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
