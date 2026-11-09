import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import '@/i18n';

const output = vi.hoisted(() => ({ result: null as object | null }));
vi.mock('@/lib/store/output', () => ({ useLatestOutput: () => output }));

describe('ExpressionTip (#3422)', () => {
  it('waits for a first take, then stays dismissed', async () => {
    const { ExpressionTip, markExpressionTipSeen } = await import('./expression-tip');
    const { rerender } = render(<ExpressionTip />);
    expect(screen.queryByRole('status')).toBeNull();

    output.result = { blob: new Blob() };
    rerender(<ExpressionTip />);
    expect(screen.getByRole('status')).toHaveTextContent(/shape the delivery with Emotion/);

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('status')).toBeNull();
    // Opening Emotion marks it seen too; marking twice is harmless.
    act(() => markExpressionTipSeen());
    expect(screen.queryByRole('status')).toBeNull();
  });
});
