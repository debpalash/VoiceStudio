import { describe, expect, it, vi } from 'vitest';
import {
  collectFaultFacts,
  collectTrustFacts,
  mountIsNoexec,
  parseWindowsFault,
  parseWindowsTrust,
  programBasename,
  runProbe,
  WINDOWS_BLOCK_MATCH,
  WINDOWS_FAULT_SCRIPT,
  WINDOWS_TRUST_SCRIPT,
} from './install-doctor';

const sac = async () => 'on' as const;

describe('collectTrustFacts', () => {
  it('passes the refused path by environment, never in the script', async () => {
    const program = "C:\\x'; Remove-Item C:\\ ;'\\python.exe";
    const run = vi.fn(async () => '{"blocked":["python.exe"],"antivirus":"Defender","signature":"NotSigned"}');
    const facts = await collectTrustFacts({
      platform: 'win32',
      program,
      runtimeRoot: 'C:\\runtime',
      run,
      readSmartAppControl: sac,
    });
    const [, args, options] = run.mock.calls[0] as unknown as [string, string[], { env: NodeJS.ProcessEnv }];
    expect(args.join(' ')).not.toContain('Remove-Item');
    expect(options.env.VS_PROBE_PROGRAM).toBe(program);
    expect(WINDOWS_TRUST_SCRIPT).toContain('$env:VS_PROBE_RUNTIME');
    expect(options.env.VS_PROBE_RUNTIME).toBe('C:\\runtime');
    expect(facts).toEqual({
      smartAppControl: 'on',
      blockedFiles: ['python.exe'],
      antivirus: ['Defender'],
      signature: 'NotSigned',
    });
  });

  it('keeps the Smart App Control fact when PowerShell itself is refused', async () => {
    const run = vi.fn(async () => {
      throw new Error('spawn UNKNOWN');
    });
    await expect(
      collectTrustFacts({ platform: 'win32', program: 'p', run, readSmartAppControl: sac }),
    ).resolves.toEqual({ smartAppControl: 'on' });
  });

  it('survives every probe failing', async () => {
    const run = vi.fn(async () => {
      throw new Error('no');
    });
    await expect(
      collectTrustFacts({
        platform: 'win32',
        run,
        readSmartAppControl: () => Promise.reject(new Error('no')),
      }),
    ).resolves.toEqual({ smartAppControl: 'unknown' });
  });

  it('reads the macOS quarantine attribute', async () => {
    const readSmartAppControl = sac;
    await expect(
      collectTrustFacts({ platform: 'darwin', program: '/a/python', run: async () => 'x', readSmartAppControl }),
    ).resolves.toEqual({ quarantined: true });
    await expect(
      collectTrustFacts({
        platform: 'darwin',
        program: '/a/python',
        run: () => Promise.reject(new Error('absent')),
        readSmartAppControl,
      }),
    ).resolves.toEqual({});
  });

  it('reads a noexec mount on Linux', async () => {
    const readMountInfo = async () =>
      '22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n40 22 8:2 / /mnt/data rw,noexec - ext4 /dev/sda2 rw\n';
    await expect(
      collectTrustFacts({ platform: 'linux', program: '/mnt/data/python', readMountInfo, readSmartAppControl: sac }),
    ).resolves.toEqual({ noexecMount: true });
    await expect(
      collectTrustFacts({ platform: 'linux', program: '/home/u/python', readMountInfo, readSmartAppControl: sac }),
    ).resolves.toEqual({});
  });
});

describe('parsers', () => {
  it('does not treat a sibling path as inside the mount', () => {
    const info = '22 1 8:1 / / rw - ext4 a rw\n40 22 8:2 / /mnt/data rw,noexec - ext4 b rw\n';
    expect(mountIsNoexec(info, '/mnt/database/python')).toBe(false);
  });

  it('ignores anything that is not the expected JSON', () => {
    expect(parseWindowsTrust('Get-WinEvent : access denied')).toEqual({});
    expect(parseWindowsTrust('{"blocked":[1,null],"antivirus":[]}')).toEqual({});
  });

  it('reports a basename from either path style', () => {
    expect(programBasename('C:\\a b\\python.exe')).toBe('python.exe');
    expect(programBasename('/a/python')).toBe('python');
  });

  it('only counts recent block events', () => {
    expect(WINDOWS_TRUST_SCRIPT).toContain('AddMinutes(-10)');
    // Unrelated blocks must not push this runtime's out before the match runs.
    expect(WINDOWS_TRUST_SCRIPT).toContain('-MaxEvents 200');
  });
});

describe.runIf(process.platform === 'win32')('Windows block matching', () => {
  const root = 'C:\\Users\\a\\VS';
  const program = 'C:\\Users\\a\\tools\\python.exe';
  it.each([
    ['\\Device\\HarddiskVolume3\\Users\\a\\VS\\.python\\python.exe', 'yes'],
    ['c:\\users\\a\\vs\\x.dll', 'yes'],
    ['\\Device\\HarddiskVolume3\\Users\\a\\tools\\python.exe', 'yes'],
    ['\\Device\\HarddiskVolume3\\Users\\a\\VS-old\\x.dll', 'no'],
    ['\\Device\\HarddiskVolume3\\Windows\\python.exe', 'no'],
  ])('%s -> %s', async (event, expected) => {
    const script = `${WINDOWS_BLOCK_MATCH}
if(Test-VsBlock $env:VS_E $env:VS_R $env:VS_P){'yes'}else{'no'}`;
    const output = await runProbe('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], {
      env: { ...process.env, VS_E: event, VS_R: root, VS_P: program },
      timeoutMs: 30_000,
    });
    expect(output.trim()).toBe(expected);
  }, 40_000);
});

describe('collectFaultFacts', () => {
  it('reads Windows Error Reporting for the crashed pid, passed by environment', async () => {
    const run = vi.fn(
      async () => '{"module":"torch_cpu.dll","version":"2.8.0.0","offset":"0x000000000001E3F4"}',
    );
    const facts = await collectFaultFacts({ platform: 'win32', pid: 4242, run, retryDelayMs: 0 });
    const [command, args, options] = run.mock.calls[0] as unknown as [
      string,
      string[],
      { env: NodeJS.ProcessEnv },
    ];
    expect(command).toBe('powershell.exe');
    expect(args.join(' ')).not.toContain('4242');
    expect(options.env.VS_PROBE_PID).toBe('4242');
    expect(WINDOWS_FAULT_SCRIPT).toContain('Id=1000');
    expect(facts).toEqual({
      faultingModule: 'torch_cpu.dll',
      faultingModuleVersion: '2.8.0.0',
      faultOffset: '0x000000000001e3f4',
    });
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('reads once more when the event was not written yet, and never fails', async () => {
    const outputs = ['', '{"module":"ntdll.dll","version":"10.0.26100.1","offset":"0x10"}'];
    const run = vi.fn(async () => outputs.shift() ?? '');
    await expect(
      collectFaultFacts({ platform: 'win32', pid: 7, run, retryDelayMs: 0 }),
    ).resolves.toMatchObject({ faultingModule: 'ntdll.dll' });
    const refused = vi.fn(async () => {
      throw new Error('spawn UNKNOWN');
    });
    await expect(
      collectFaultFacts({ platform: 'win32', pid: 7, run: refused, retryDelayMs: 0 }),
    ).resolves.toEqual({});
    expect(refused).toHaveBeenCalledTimes(2);
  });

  it('runs only on Windows with a pid', async () => {
    const run = vi.fn(async () => '{}');
    for (const input of [
      { platform: 'linux' as const, pid: 7 },
      { platform: 'darwin' as const, pid: 7 },
      { platform: 'win32' as const, pid: undefined },
    ])
      await expect(collectFaultFacts({ ...input, run, retryDelayMs: 0 })).resolves.toEqual({});
    expect(run).not.toHaveBeenCalled();
  });

  it('keeps identifiers only', () => {
    expect(
      parseWindowsFault('{"module":"C:\\\\Users\\\\me\\\\x.dll","version":"1 2","offset":"bad"}'),
    ).toEqual({ faultingModule: 'x.dll' });
    expect(parseWindowsFault('{"module":"two words.dll"}')).toEqual({});
    expect(parseWindowsFault('not json')).toEqual({});
  });
});

describe.runIf(process.platform === 'win32')('Windows fault event script', () => {
  it('runs cleanly and prints nothing for a pid with no fault event', async () => {
    const output = await runProbe(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', WINDOWS_FAULT_SCRIPT],
      { env: { ...process.env, VS_PROBE_PID: '999999999' }, timeoutMs: 30_000 },
    );
    expect(output.trim()).toBe('');
  }, 40_000);
});
