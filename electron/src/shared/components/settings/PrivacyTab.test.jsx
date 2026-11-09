import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

// Mock the zustand store for the openSettingsTab deep-link action.
const { openSettingsTab } = vi.hoisted(() => ({ openSettingsTab: vi.fn() }));
vi.mock('../../store', () => ({
  useAppStore: (selector) => selector({ openSettingsTab }),
}));

import PrivacyTab from './PrivacyTab';

describe('PrivacyTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not claim "Offline translation" when system info is missing (backend down)', () => {
    render(<PrivacyTab info={undefined} />);
    expect(screen.getByTestId('privacy-translator-unknown')).toBeInTheDocument();
    expect(screen.queryByText('Offline translation')).not.toBeInTheDocument();
  });

  it("does not claim \"Offline translation\" for the backend's safe-defaults 'unknown'", () => {
    render(<PrivacyTab info={{ translate_provider: 'unknown' }} />);
    expect(screen.getByTestId('privacy-translator-unknown')).toBeInTheDocument();
    expect(screen.queryByText('Offline translation')).not.toBeInTheDocument();
  });

  it('shows the green badge only for confirmed-offline providers', () => {
    render(<PrivacyTab info={{ translate_provider: 'nllb' }} />);
    expect(screen.getByText('Offline translation')).toBeInTheDocument();
    expect(screen.queryByTestId('privacy-translator-unknown')).not.toBeInTheDocument();
  });

  it('warns for online providers and deep-links to Translation settings', () => {
    render(<PrivacyTab info={{ translate_provider: 'google' }} />);
    expect(screen.getByText('Online through google')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('privacy-change-translator'));
    expect(openSettingsTab).toHaveBeenCalledWith('translation');
  });
});
