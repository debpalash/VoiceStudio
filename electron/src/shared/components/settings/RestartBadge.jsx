import React from 'react';
import { RotateCw, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Badge } from '../../ui';

/**
 * RestartBadge — a tiny, reusable affordance that tells the user whether a
 * setting takes effect immediately or needs an app restart.
 *
 * Settings split into two classes:
 *   • "Applies now"       — store / live-API changes that take effect instantly.
 *   • "Restart required"  — durable env writes read once at process start
 *                           (cache dir, HF mirror, torch.compile, remote backend).
 *
 * Pass `applies` for the live affordance; default is the restart warning.
 *
 * @param {boolean=} applies  render the "Applies now" success affordance instead
 * @param {string=}  className extra class
 */
export default function RestartBadge({ applies = false, className = '' }) {
  const { t } = useTranslation();
  if (applies) {
    return (
      <Badge tone="success" size="xs" className={className}>
        <Check size={10} aria-hidden="true" />{' '}
        {t('settings.applies_now', { defaultValue: 'Applies now' })}
      </Badge>
    );
  }
  return (
    <Badge tone="warn" size="xs" className={className}>
      <RotateCw size={10} aria-hidden="true" />{' '}
      {t('settings.restart_required', { defaultValue: 'Restart required' })}
    </Badge>
  );
}
