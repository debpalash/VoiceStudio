import { expect, it } from 'vitest';
import { diskMeterClass, visibleStorageWarnings } from './storage-meter';

it('turns the disk bar amber above 85% and red above 95%', () => {
  expect(diskMeterClass(60)).toBe('bg-primary');
  expect(diskMeterClass(85)).toBe('bg-primary');
  expect(diskMeterClass(90)).toBe('bg-warning');
  expect(diskMeterClass(97.7)).toBe('bg-destructive');
});

it('shows a full disk once, keeping scan warnings', () => {
  const warnings = [
    { kind: 'low_disk', severity: 'critical', path: '/' },
    { kind: 'low_disk', severity: 'critical', path: '/System/Volumes/Data' },
    { kind: 'volume_pressure', severity: 'warning', path: '/' },
    { kind: 'unreadable', severity: 'warning', path: '/models' },
  ];
  expect(visibleStorageWarnings(warnings)).toEqual([warnings[0], warnings[3]]);
  expect(visibleStorageWarnings([warnings[2]])).toEqual([warnings[2]]);
  expect(visibleStorageWarnings(undefined)).toEqual([]);
});
