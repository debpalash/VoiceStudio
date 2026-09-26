import React from 'react';
import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import NotificationPanel from './NotificationPanel';

vi.mock('../api/hooks', () => ({
  useVisibleNotifications: () => ({ notifications: [{ level: 'error' }, { level: 'warn' }] }),
}));

it('keeps the badge inside the title-bar button', () => {
  render(<NotificationPanel />);
  expect(screen.getByText('2')).toHaveClass('top-0', 'right-0');
  expect(screen.getByText('2')).not.toHaveClass('-top-[4px]');
});
