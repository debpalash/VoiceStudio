import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import i18n from '@/i18n';
import {
  DEFAULT_OVERRIDES,
  overridesToRequest,
} from '@shared/utils/longformOverrides';
import { ProductionSettings } from './production-settings';
it('shows effective legacy joins for untouched and reset drafts', () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ProductionSettings value={DEFAULT_OVERRIDES} disabled={false} onChange={() => {}} />
    </QueryClientProvider>,
  );
  expect(screen.getByLabelText(i18n.t('audiobook.line_gap'))).toHaveValue('0');
  expect(screen.getByLabelText(i18n.t('audiobook.paragraph_gap'))).toHaveValue('0');
  const trim = screen.getByText(i18n.t('audiobook.trim_edges')).querySelector('[role="switch"]');
  expect(trim).toHaveAttribute('aria-checked', 'false');
  expect(overridesToRequest(DEFAULT_OVERRIDES, 'Auto')).toEqual({});
});
