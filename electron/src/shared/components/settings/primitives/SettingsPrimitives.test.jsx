import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Settings, Volume2 } from 'lucide-react';
import SettingsSection from './SettingsSection';
import SettingRow from './SettingRow';

describe('settings content primitives', () => {
  it('gives every settings section the shared elevated card and responsive header', () => {
    render(
      <SettingsSection
        icon={Settings}
        title="Audio"
        description="Choose how generated audio behaves."
        actions={<button type="button">Reset</button>}
      >
        <SettingRow icon={Volume2} title="Volume" control={<span>80%</span>} />
      </SettingsSection>,
    );

    const section = screen.getByRole('region', { name: 'Audio' });
    expect(section.className).toContain('bg-[color-mix');
    expect(section.className).toContain('shadow-[');
    expect(section.querySelector('[data-slot="settings-section-icon"]')).toBeInTheDocument();
    expect(section.querySelector('[data-slot="settings-section-actions"]')).toHaveClass(
      'flex-wrap',
    );
  });

  it('uses a consistent divider and icon tile for setting rows', () => {
    render(<SettingRow icon={Volume2} title="Volume" control={<span>80%</span>} />);

    const row = screen.getByText('Volume').closest('[data-slot="setting-row"]');
    expect(row.className).toContain('border-[color-mix');
    expect(row.querySelector('[data-slot="setting-row-icon"]')).toBeInTheDocument();
  });
});
