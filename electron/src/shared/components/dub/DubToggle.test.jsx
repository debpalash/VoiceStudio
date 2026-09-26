import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import DubToggle from './DubToggle';

describe('DubToggle', () => {
  it('announces its state and updates through the supplied handler', () => {
    const onChange = vi.fn();
    const { rerender } = render(<DubToggle label="Preview" checked={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Preview', checked: false }));
    expect(onChange).toHaveBeenCalledWith(true);
    rerender(<DubToggle label="Preview" checked onChange={onChange} />);
    expect(screen.getByRole('switch', { name: 'Preview' })).toHaveAttribute('aria-checked', 'true');
  });
});
