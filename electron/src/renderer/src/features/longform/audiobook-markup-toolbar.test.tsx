import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import '@/i18n';
import { AudiobookMarkupToolbar } from './audiobook-markup-toolbar';

vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({
    activeTts: {
      id: 'cosyvoice',
      display_name: 'CosyVoice 3 (9 langs, zero-shot, instruct, Apache-2.0)',
      expression_caps: { emotion: 'direction', tags: ['[breath]', '[laughter]'] },
    },
  }),
}));

describe('AudiobookMarkupToolbar reactions (#3422)', () => {
  it("offers the engine's tags first and grays out the rest with a reason", async () => {
    render(
      <AudiobookMarkupToolbar
        textareaRef={createRef<HTMLTextAreaElement>()}
        text=""
        disabled={false}
        setText={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Reactions/ }));
    const items = await screen.findAllByRole('menuitem');
    expect(items[0]).toHaveTextContent('[breath]');
    expect(items[0]).not.toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('menuitem', { name: '[sigh]' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(
      screen.getByText('Grayed tags would be read aloud as words by CosyVoice 3.'),
    ).toBeInTheDocument();
  });
});
