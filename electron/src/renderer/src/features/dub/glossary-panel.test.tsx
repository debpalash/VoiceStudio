import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { apiJson } from '@/lib/api/client';
import { GlossaryPanel } from './glossary-panel';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it('extracts glossary terms from the source language the panel was given', async () => {
  vi.mocked(apiJson).mockResolvedValue([]);
  render(
    <GlossaryPanel
      projectId="pack-contract"
      sourceLang="nl"
      targetLang="fr"
      segments={[{ id: 'a', start: 0, end: 2, text: 'Goedemorgen', text_original: 'Goedemorgen' }]}
      onCountChange={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: /glossary.auto_btn/ }));

  await waitFor(() =>
    expect(
      vi.mocked(apiJson).mock.calls.some(([url]) => String(url).includes('auto-extract')),
    ).toBe(true),
  );
  const call = vi.mocked(apiJson).mock.calls.find(([url]) => String(url).includes('auto-extract'));
  const body = JSON.parse(String(call?.[1]?.body)) as Record<string, unknown>;
  expect(body.source_lang).toBe('nl');
  expect(body.target_lang).toBe('fr');
});
