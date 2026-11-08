import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '@/i18n';
import type { ExpressionCaps } from '@/lib/api/types';
import { AudiobookMarkupToolbar } from './audiobook-markup-toolbar';

const engine = vi.hoisted(() => ({
  name: 'IndexTTS 2.5 (multilingual emotion-controlled cloning)',
  caps: { emotion: 'graded', tags: [] } as ExpressionCaps,
}));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({
    activeTts: { id: 'engine', display_name: engine.name, expression_caps: engine.caps },
  }),
}));

function renderToolbar(text: string, selection: [number, number]) {
  const ref = createRef<HTMLTextAreaElement>();
  const setText = vi.fn();
  render(
    <>
      <textarea ref={ref} defaultValue={text} aria-label="script" />
      <AudiobookMarkupToolbar textareaRef={ref} text={text} disabled={false} setText={setText} />
    </>,
  );
  ref.current!.setSelectionRange(...selection);
  return setText;
}

beforeEach(() => {
  engine.name = 'IndexTTS 2.5 (multilingual emotion-controlled cloning)';
  engine.caps = { emotion: 'graded', tags: [] };
});

describe('Audiobook Emotion markup (#3422)', () => {
  it('inserts an empty [emotion:] at the caret for a free-text mood', () => {
    const setText = renderToolbar('Hello there.', [6, 6]);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(setText).toHaveBeenCalledWith('Hello [emotion:]there.');
  });

  it('wraps a selection so the mood ends after it', () => {
    const setText = renderToolbar('Calm. Scared. Calm.', [6, 13]);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(setText).toHaveBeenCalledWith('Calm. [emotion:]Scared.[emotion:] Calm.');
  });

  it('stays visible but grayed with the reason where the engine ignores emotion', () => {
    engine.name = 'VoiceStudio (k2-fsa/OmniVoice, 600+ languages)';
    engine.caps = { emotion: 'none', tags: ['[laughter]'] };
    const setText = renderToolbar('Hello.', [0, 0]);
    const button = screen.getByRole('button', { name: 'Emotion' });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).toHaveAttribute(
      'title',
      expect.stringMatching(/isn't available with OmniVoice/),
    );
    fireEvent.click(button);
    expect(setText).not.toHaveBeenCalled();
  });
});
