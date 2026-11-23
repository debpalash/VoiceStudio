import type { TFunction } from 'i18next';

export function captureTrayLabels(t: TFunction) {
  return {
    show: `${t('common.open')} ${t('app.name')}`,
    start: t('transcriptions.capture'),
    stop: t('clone.stop_recording'),
    settings: t('nav.settings'),
    exit: t('common.quit'),
  };
}
