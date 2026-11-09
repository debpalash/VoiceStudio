import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import '@/i18n';
import { ValidationWarnings } from './validation-warnings';

it('turns uncast characters into one calm note instead of a warning per name', () => {
  render(
    <ValidationWarnings
      warnings={[
        { type: 'unknown_voice', name: 'Narrator' },
        { type: 'unknown_voice', name: 'Mara' },
        { type: 'unknown_voice', name: 'Cole' },
      ]}
      onDismiss={vi.fn()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Narrator, Mara, and Cole aren’t cast yet');
  expect(screen.queryByText(/Unknown voice/)).toBeNull();
  expect(screen.queryByText('Before you create')).toBeNull();
});

it('keeps real script problems as warnings beside the note', () => {
  render(
    <ValidationWarnings
      warnings={[
        { type: 'unknown_voice', name: 'Mara' },
        { type: 'unknown_tag', tag: '[whisper]' },
      ]}
      onDismiss={vi.fn()}
    />,
  );
  expect(screen.getByText(/Mara isn’t cast yet/)).toBeVisible();
  expect(screen.getByText('Before you create')).toBeVisible();
  expect(screen.getByText(/\[whisper\]/)).toBeVisible();
});
