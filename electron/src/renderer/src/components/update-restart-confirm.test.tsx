import { act, cleanup, render, screen } from '@testing-library/react';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { afterEach, beforeAll, expect, test, vi } from 'vitest';
import en from '@/i18n/locales/en.json';

vi.mock('@/components/bridge', () => ({ getBridge: () => null }));
vi.mock('@/features/dub/dub-session', () => ({
  dubSession: { state: { phase: 'idle', recovery: null, batchProgress: null } },
  flushDubDraft: vi.fn(),
}));

import { UpdateRestartConfirm } from './update-restart-confirm';
import { restartConfirmation } from '@/lib/update-actions';

const i18n = i18next.createInstance();
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    resources: { en: { translation: en } },
    lng: 'en',
    interpolation: { escapeValue: false },
  });
});
afterEach(() => {
  act(() => restartConfirmation.setState(() => null));
  cleanup();
});

test.each([
  [1, 'Restarting will stop 1 running task.'],
  [3, 'Restarting will stop 3 running tasks.'],
])('says how many running tasks a restart stops (%i)', async (count, text) => {
  render(
    <I18nextProvider i18n={i18n}>
      <UpdateRestartConfirm />
    </I18nextProvider>,
  );
  act(() => restartConfirmation.setState(() => ({ count, unknown: false })));
  expect(await screen.findByText(text)).toBeInTheDocument();
});
