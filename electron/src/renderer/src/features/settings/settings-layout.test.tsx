import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SettingsPageTitleContext, SettingsSection } from './settings-layout';

afterEach(cleanup);

it('does not repeat the page title as a visible section heading', () => {
  render(
    <SettingsPageTitleContext.Provider value="Network">
      <SettingsSection title="Network">
        <p>row</p>
      </SettingsSection>
      <SettingsSection title="Proxy">
        <p>row</p>
      </SettingsSection>
    </SettingsPageTitleContext.Provider>,
  );
  // Still a heading for screen readers, just not drawn under the breadcrumb.
  expect(screen.getByRole('heading', { name: 'Network' })).toHaveClass('sr-only');
  expect(screen.getByRole('heading', { name: 'Proxy' })).not.toHaveClass('sr-only');
});
