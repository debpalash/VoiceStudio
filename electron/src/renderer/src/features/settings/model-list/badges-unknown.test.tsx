import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { DeviceChip } from './badges';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

it('localizes an unverified sidecar device', () => {
  render(<DeviceChip device="unknown" />);
  expect(screen.getByText('common.unknown')).toBeTruthy();
});
