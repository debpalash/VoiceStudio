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
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it('keeps integrations with a shared vendor URL distinct while filtering', async () => {
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  const { container } = render(<IntegrationsPage />);
  const originalCount = container.querySelectorAll('.integration-card').length;
  fireEvent.change(screen.getByRole('textbox', { name: 'common.search' }), {
    target: { value: 'modelcontextprotocol/servers' },
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

it('badges only entries with a real setup block as working with VoiceStudio', () => {
  const { container } = render(<IntegrationsPage />);
  const card = (name: string) =>
    [...container.querySelectorAll('.integration-card')].find(
      (element) => element.querySelector('h3')?.textContent === name,
    )!;
  expect(card('Codex CLI')).toHaveTextContent('integrationCatalog.worksWith');
  expect(card('Codex CLI')).toHaveTextContent('integrationCatalog.capability.mcp');
  expect(card('Zapier')).toHaveTextContent('integrationCatalog.externalLink');
  expect(card('Zapier').querySelector('.integration-card-capabilities')).toBeNull();
  expect(container.textContent).not.toContain('directoryExamples.example');
});
