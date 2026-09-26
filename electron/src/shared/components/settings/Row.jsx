import React from 'react';
import { SettingRow } from './primitives';

// About/Privacy read-only data rows delegate to the shared SettingRow primitive
// so they pick up the redesigned grid + mono value styling unchanged.
export default function Row({ label, value, mono }) {
  return <SettingRow title={label} control={value} mono={mono} />;
}
