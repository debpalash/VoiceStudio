import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { IntegrationsPage } from './integrations-page';
const navigate = vi.hoisted(() => vi.fn());
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
}));
vi.mock('@shared/config/integration-catalog', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/config/integration-catalog')>();
  return {
    ...actual,
    INTEGRATION_CATALOG: actual.INTEGRATION_CATALOG.map((entry) => ({
      ...entry,
      featured: entry.name === 'Twilio',
    })),
  };
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it('only lists integrations with completed wiring pages', () => {
  const { container } = render(<IntegrationsPage />);
  const names = [...container.querySelectorAll('.integration-card h3')].map(
    (heading) => heading.textContent,
  );
  expect(names.sort()).toEqual([
    'Claude Code',
    'Codex CLI',
    'Cursor',
    'Docker',
    'GitHub Container Registry',
    'Model Context Protocol',
    'OpenAI Agents',
    'Twilio',
    'VoiceStudio API',
    'n8n',
  ]);
  expect(container.textContent).not.toContain('Zapier');
  expect(container.textContent).not.toContain('integrationCatalog.externalLink');
  expect(container.querySelector('.integrations-featured')).toBeNull();
});

it('keeps wired integrations distinct while filtering', async () => {
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  const { container } = render(<IntegrationsPage />);
  const originalCount = container.querySelectorAll('.integration-card').length;
  fireEvent.change(screen.getByRole('textbox', { name: 'common.search' }), {
    target: { value: 'mcp' },
  });
  const cards = [...container.querySelectorAll<HTMLButtonElement>('.integration-card')];
  expect(cards.length).toBeGreaterThan(1);
  for (const card of cards) {
    fireEvent.click(card);
    await waitFor(() => expect(navigate).toHaveBeenCalled());
  }
  const slugs = navigate.mock.calls.map(([arg]) => arg.params.slug);
  expect(new Set(slugs).size).toBe(cards.length);
  fireEvent.change(screen.getByRole('textbox', { name: 'common.search' }), {
    target: { value: '' },
  });
  expect(container.querySelectorAll('.integration-card')).toHaveLength(originalCount);
  expect(errors).not.toHaveBeenCalled();
});

it('badges every listed integration as working with VoiceStudio', () => {
  const { container } = render(<IntegrationsPage />);
  const card = (name: string) =>
    [...container.querySelectorAll('.integration-card')].find(
      (element) => element.querySelector('h3')?.textContent === name,
    )!;
  expect(card('Codex CLI')).toHaveTextContent('integrationCatalog.worksWith');
  expect(card('Codex CLI')).toHaveTextContent('integrationCatalog.capability.mcp');
  expect(card('Twilio')).toHaveTextContent('integrationCatalog.worksWith');
  expect(card('Twilio')).toHaveTextContent('integrationCatalog.capability.phoneCalls');
  expect(container.textContent).not.toContain('directoryExamples.example');
});

it('badges only featured (sponsored) integrations', () => {
  const { container } = render(<IntegrationsPage />);
  const card = (name: string) =>
    [...container.querySelectorAll('.integration-card')].find(
      (element) => element.querySelector('h3')?.textContent === name,
    )!;
  expect(card('Twilio').querySelector('.integration-badge--featured')).not.toBeNull();
  expect(card('Codex CLI').querySelector('.integration-badge--featured')).toBeNull();
});
