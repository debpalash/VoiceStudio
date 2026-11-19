// @vitest-environment node
import { execFileSync } from 'node:child_process';
import { describe, expect, it, vi } from 'vitest';
import { downloadProxyEnv, parseWinInetProxy, proxyBypassHosts } from './proxy-env';

vi.mock('node:child_process', () => ({ execFileSync: vi.fn() }));

describe('WinINET proxy normalization', () => {
  it('converts SOCKS settings into URLs instead of DNS hostnames', () => {
    expect(parseWinInetProxy('socks=127.0.0.1:10808')).toEqual({
      HTTP_PROXY: 'socks5h://127.0.0.1:10808',
      HTTPS_PROXY: 'socks5h://127.0.0.1:10808',
    });
  });
  it('routes per-protocol proxies without changing the tunnel protocol', () => {
    expect(parseWinInetProxy('http=one:80;https=two:81;socks=three:1080')).toEqual({
      HTTP_PROXY: 'http://one:80',
      HTTPS_PROXY: 'http://two:81',
    });
    expect(parseWinInetProxy('https://secure:8443')).toEqual({
      HTTP_PROXY: 'https://secure:8443',
      HTTPS_PROXY: 'https://secure:8443',
    });
  });
  it('rejects malformed proxy values', () => {
    expect(parseWinInetProxy('socks=bad host:12')).toEqual({});
    expect(parseWinInetProxy('ftp=ftp.example:21')).toEqual({});
  });
  it('preserves explicit proxy choices and loopback exclusions', () => {
    const read = vi.fn(() => 'socks=system:1080');
    const env = downloadProxyEnv(
      { https_proxy: 'http://explicit:80', NO_PROXY: 'internal' },
      'win32',
      read,
    );
    expect(read).not.toHaveBeenCalled();
    expect(env.https_proxy).toBe('http://explicit:80');
    expect(env.HTTPS_PROXY).toBeUndefined();
    expect(env.NO_PROXY).toBe('internal,localhost,127.0.0.1,::1');
    expect(env.no_proxy).toBe(env.NO_PROXY);
  });
  it('reads Windows settings only and exposes only proxy variables', () => {
    const read = vi.fn(() => 'socks=127.0.0.1:1080');
    expect(downloadProxyEnv({ CUSTOM: 'keep' }, 'win32', read)).toMatchObject({
      HTTPS_PROXY: 'socks5h://127.0.0.1:1080',
    });
    expect(
      downloadProxyEnv({ PYTHONPATH: '/untrusted', CUSTOM: 'keep' }, 'linux', read).PYTHONPATH,
    ).toBeUndefined();
    read.mockClear();
    expect(downloadProxyEnv({}, 'linux', read).HTTPS_PROXY).toBeUndefined();
    expect(read).not.toHaveBeenCalled();
  });
});

it('preserves representable system bypasses and declines lossy conversions', () => {
  expect(proxyBypassHosts('*.example.com;internal;127.0.0.1')).toEqual([
    '.example.com',
    'internal',
    '127.0.0.1',
  ]);
  const env = downloadProxyEnv({}, 'win32', () => ({
    server: 'socks=proxy:1080',
    bypass: '*.example.com',
  }));
  expect(env.NO_PROXY).toContain('.example.com');
});

it('reads enabled registry values with a timeout and handles denied access', () => {
  vi.mocked(execFileSync).mockReturnValue(
    'ProxyEnable    REG_DWORD    0x1\nProxyServer    REG_SZ    socks=127.0.0.1:10808\nProxyOverride    REG_SZ    *.internal.example',
  );
  expect(downloadProxyEnv({}, 'win32').HTTPS_PROXY).toBe('socks5h://127.0.0.1:10808');
  expect(execFileSync).toHaveBeenCalledWith(
    'reg.exe',
    expect.any(Array),
    expect.objectContaining({ timeout: 2500, windowsHide: true }),
  );
  vi.mocked(execFileSync).mockReturnValue(
    'ProxyEnable    REG_DWORD    0x0\nProxyServer    REG_SZ    socks=127.0.0.1:10808',
  );
  expect(downloadProxyEnv({}, 'win32').HTTPS_PROXY).toBeUndefined();
  vi.mocked(execFileSync).mockImplementation(() => {
    throw new Error('denied');
  });
  expect(downloadProxyEnv({}, 'win32').HTTPS_PROXY).toBeUndefined();
});

it('converts the bypass list proxy tools write by default instead of blocking setup', () => {
  const clash = 'localhost;127.*;10.*;172.16.*;192.168.*;<local>';
  expect(proxyBypassHosts(clash)).toEqual([
    'localhost',
    '127.0.0.0/8',
    '10.0.0.0/8',
    '172.16.0.0/16',
    '192.168.0.0/16',
  ]);
  const env = downloadProxyEnv({}, 'win32', () => ({ server: '127.0.0.1:7890', bypass: clash }));
  expect(env.HTTPS_PROXY).toBe('http://127.0.0.1:7890');
  expect(env.NO_PROXY).toContain('192.168.0.0/16');
  expect(proxyBypassHosts('300.*')).toBeNull();
});

it('names unrepresentable proxy bypass rules instead of falling into misleading DNS errors', () => {
  expect(() =>
    downloadProxyEnv({}, 'win32', () => ({ server: 'socks=proxy:1080', bypass: '172.1?.*' })),
  ).toThrow('VOICESTUDIO_PROXY_BYPASS_UNSUPPORTED');
});
