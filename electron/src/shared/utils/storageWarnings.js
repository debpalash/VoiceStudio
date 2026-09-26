export function warningText(t, w) {
  const vars = {
    path: w.path,
    free: w.free_gb,
    min: w.min_free_gb,
    percent: w.used_percent,
  };
  if (w.kind === 'low_disk' && w.severity === 'critical') {
    return t('settings.storage_warn_critical', {
      defaultValue:
        'Critically low disk space: {{free}} GB free on {{path}} — VoiceStudio needs at least {{min}} GB. Generation and model downloads may fail.',
      ...vars,
    });
  }
  if (w.kind === 'low_disk') {
    return t('settings.storage_warn_low', {
      defaultValue:
        'Low disk space: {{free}} GB free on {{path}} — getting close to the {{min}} GB minimum.',
      ...vars,
    });
  }
  if (w.kind === 'volume_pressure') {
    return t('settings.storage_warn_pressure', {
      defaultValue: 'The disk holding {{path}} is {{percent}}% full.',
      ...vars,
    });
  }
  return t('settings.storage_warn_unreadable', {
    defaultValue: 'Could not fully scan {{path}} — the size shown may be incomplete.',
    ...vars,
  });
}
