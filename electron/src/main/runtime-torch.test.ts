// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { supportedWindowsRocmGpu } from './runtime-torch';

describe('Windows AMD runtime selection', () => {
  const radeon = {
    vendorId: 0x1002,
    deviceId: 0x7550,
    deviceString: 'AMD Radeon RX 9070 XT',
    driverVersion: '32.0.31044.16',
  };

  it('recognizes the supported RX 9070 XT through the native GPU inventory', () => {
    expect(supportedWindowsRocmGpu([radeon], '10.0.26300')).toEqual(radeon);
    expect(supportedWindowsRocmGpu([{ vendorId: 0x1002, deviceString: 'Radeon RX 7900 XTX' }], '10.0.26100'))
      .toEqual({ vendorId: 0x1002, deviceString: 'Radeon RX 7900 XTX' });
  });

  it('leaves NVIDIA, unlisted AMD hardware, and Windows 10 on the existing stack', () => {
    expect(supportedWindowsRocmGpu([radeon, { vendorId: 0x10de }], '10.0.26300')).toBeNull();
    expect(supportedWindowsRocmGpu([{ vendorId: 0x1002, deviceString: 'Radeon RX 6700 XT' }], '10.0.26100'))
      .toBeNull();
    expect(supportedWindowsRocmGpu([radeon], '10.0.19045')).toBeNull();
  });
});
