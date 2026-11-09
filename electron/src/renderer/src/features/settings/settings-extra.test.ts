import { expect, it } from 'vitest';

import { extraSettingsFor } from './settings-page';

it('lists Settings → Account only when the build offers cloud accounts', () => {
  expect(extraSettingsFor(false).some((item) => item.to === '/settings/account')).toBe(false);
  expect(extraSettingsFor(true).some((item) => item.to === '/settings/account')).toBe(true);
});
