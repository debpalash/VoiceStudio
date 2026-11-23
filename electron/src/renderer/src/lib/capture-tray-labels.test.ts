import { createInstance } from 'i18next';
import { expect, it } from 'vitest';
import enLocale from '@/i18n/locales/en.json';
import zhCnLocale from '@/i18n/locales/zh-CN.json';
import { captureTrayLabels } from './capture-tray-labels';

it('labels the Chinese tray action as quit rather than an exit code', async () => {
  const i18n = createInstance();
  await i18n.init({
    lng: 'zh-CN',
    fallbackLng: false,
    resources: {
      en: { translation: enLocale },
      'zh-CN': { translation: zhCnLocale },
    },
  });

  expect(captureTrayLabels(i18n.t).exit).toBe('退出');
  expect(i18n.t('crash.field_exit')).toBe('退出码');
});
