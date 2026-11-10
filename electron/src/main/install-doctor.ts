/**
 * Reads the facts that decide why the OS refused the engine. It runs only after
 * a launch or load was already refused, never on a healthy start, and reads
 * locally: nothing here leaves the machine unless the user submits a report.
 * A probe that fails or runs out of time yields no fact rather than a wrong one.
 */

import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { basename, win32 } from 'node:path';
import type { SmartAppControlState } from '../shared/utils/installFailure';

export const TRUST_PROBE_DEADLINE_MS = 4000;

export interface TrustFacts {
  smartAppControl?: SmartAppControlState;
  blockedFiles?: string[];
  signature?: string;
  antivirus?: string[];
  quarantined?: boolean;
  noexecMount?: boolean;
}

export type ProbeRunner = (
  command: string,
  args: string[],
  options: { env?: NodeJS.ProcessEnv; timeoutMs: number },
) => Promise<string>;

export const runProbe: ProbeRunner = (command, args, { env, timeoutMs }) =>
  new Promise((resolve, reject) => {
    execFile(
      command,
      args,
      { encoding: 'utf8', timeout: timeoutMs, windowsHide: true, ...(env ? { env } : {}) },
      (error, stdout) => (error ? reject(error) : resolve(stdout)),
    );
  });

/**
 * Events name the volume by device (\Device\HarddiskVolume3\...), which a drive
 * letter cannot be compared to without more Win32 calls, so paths compare below the
 * volume. A folder boundary keeps a sibling like runtime-old from matching.
 */
export const WINDOWS_BLOCK_MATCH = String.raw`function Get-VsPath($p){([string]$p -replace '/','\') -replace '^(\\\\\?\\)?([A-Za-z]:|\\Device\\[^\\]+)',''}
function Test-VsBlock($n,$runtime,$prog){$p=Get-VsPath $n;$base=(Get-VsPath $runtime).TrimEnd('\');($base -and $p.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)) -or ($prog -and $p -ieq (Get-VsPath $prog))}`;

/**
 * The refused path arrives through the environment, never the script text, so a
 * crafted file name cannot become PowerShell code. Only recent blocks of this
 * runtime's files or this program count: anything else says nothing about this launch.
 */
export const WINDOWS_TRUST_SCRIPT = String.raw`$ErrorActionPreference='SilentlyContinue'
$r=@{blocked=@();antivirus=@();signature=$null}
$events=Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational';Id=3077;StartTime=(Get-Date).AddMinutes(-10)} -MaxEvents 200
${WINDOWS_BLOCK_MATCH}
foreach($e in $events){$n=[string]$e.Properties[1].Value;if($n -and (Test-VsBlock $n $env:VS_PROBE_RUNTIME $env:VS_PROBE_PROGRAM)){$r.blocked+=[IO.Path]::GetFileName($n)}}
$r.antivirus=@(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -OperationTimeoutSec 2|ForEach-Object{$_.displayName})
if($env:VS_PROBE_PROGRAM){$r.signature=[string](Get-AuthenticodeSignature -LiteralPath $env:VS_PROBE_PROGRAM).Status}
$r|ConvertTo-Json -Compress`;

export function parseWindowsTrust(output: string): TrustFacts {
  const text = (value: unknown): string[] =>
    (Array.isArray(value) ? value : value ? [value] : []).filter(
      (item): item is string => typeof item === 'string' && item.length > 0,
    );
  try {
    const parsed = JSON.parse(output) as Record<string, unknown>;
    const blocked = [...new Set(text(parsed.blocked))].slice(0, 5);
    const antivirus = [...new Set(text(parsed.antivirus))].slice(0, 5);
    return {
      ...(blocked.length ? { blockedFiles: blocked } : {}),
      ...(antivirus.length ? { antivirus } : {}),
      ...(typeof parsed.signature === 'string' && parsed.signature
        ? { signature: parsed.signature }
        : {}),
    };
  } catch {
    return {};
  }
}

/** Whether the mount holding `path` is noexec, from /proc/self/mountinfo text. */
export function mountIsNoexec(mountinfo: string, path: string): boolean | undefined {
  let best: { point: string; noexec: boolean } | undefined;
  for (const line of mountinfo.split('\n')) {
    const fields = line.split(' ');
    const point = fields[4]?.replace(/\\040/g, ' ');
    if (!point) continue;
    const prefix = point === '/' ? '/' : `${point}/`;
    if (path !== point && !path.startsWith(prefix)) continue;
    if (!best || point.length >= best.point.length)
      best = { point, noexec: (fields[5] ?? '').split(',').includes('noexec') };
  }
  return best?.noexec;
}

export interface TrustProbeInput {
  platform?: NodeJS.Platform;
  /** Full path of the refused program, when known. */
  program?: string;
  /** Runtime folder; a block event only counts as evidence inside it. */
  runtimeRoot?: string;
  readSmartAppControl: () => Promise<SmartAppControlState>;
  run?: ProbeRunner;
  readMountInfo?: () => Promise<string>;
}

export async function collectTrustFacts(input: TrustProbeInput): Promise<TrustFacts> {
  const platform = input.platform ?? process.platform;
  const run = input.run ?? runProbe;
  const { program } = input;
  if (platform === 'win32') {
    const [smartAppControl, trust] = await Promise.all([
      input.readSmartAppControl().catch((): SmartAppControlState => 'unknown'),
      run(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', WINDOWS_TRUST_SCRIPT],
        {
          env: {
            ...process.env,
            VS_PROBE_PROGRAM: program ?? '',
            VS_PROBE_RUNTIME: input.runtimeRoot ?? '',
          },
          timeoutMs: TRUST_PROBE_DEADLINE_MS,
        },
      )
        .then(parseWindowsTrust)
        .catch((): TrustFacts => ({})),
    ]);
    return { smartAppControl, ...trust };
  }
  if (!program) return {};
  if (platform === 'darwin') {
    // `xattr -p` exits non-zero when the attribute is absent.
    return run('xattr', ['-p', 'com.apple.quarantine', program], {
      timeoutMs: TRUST_PROBE_DEADLINE_MS,
    }).then(
      () => ({ quarantined: true }),
      () => ({}),
    );
  }
  if (platform === 'linux') {
    const mountinfo = await (input.readMountInfo?.() ?? readFile('/proc/self/mountinfo', 'utf8')).catch(
      () => '',
    );
    const noexecMount = mountIsNoexec(mountinfo, program);
    return noexecMount ? { noexecMount } : {};
  }
  return {};
}

export interface FaultFacts {
  faultingModule?: string;
  faultingModuleVersion?: string;
  faultOffset?: string;
}

/**
 * Windows Error Reporting writes an "Application Error" event (ID 1000) for a
 * process that died of an unhandled native exception. Its properties name the
 * faulting module (3), its version (4), the exception code (6), the offset (7)
 * and the process id in hex (8). Python's fault dump names only the Python
 * frame, so this is the one local record of which library actually crashed
 * (#3767, #3684, #3835). The pid arrives through the environment, never the
 * script text; only an event for that pid counts.
 */
export const WINDOWS_FAULT_SCRIPT = String.raw`$ErrorActionPreference='SilentlyContinue'
$want=[int64]$env:VS_PROBE_PID
$events=Get-WinEvent -FilterHashtable @{LogName='Application';ProviderName='Application Error';Id=1000;StartTime=(Get-Date).AddMinutes(-10)} -MaxEvents 50
foreach($e in $events){$p=[string]$e.Properties[8].Value;$n=$null;if($p -match '^0x([0-9a-fA-F]+)$'){$n=[Convert]::ToInt64($Matches[1],16)}elseif($p -match '^\d+$'){$n=[int64]$p}
if($n -eq $want){@{module=[string]$e.Properties[3].Value;version=[string]$e.Properties[4].Value;offset=[string]$e.Properties[7].Value}|ConvertTo-Json -Compress;break}}
exit 0`;

const MODULE_NAME = /^[\w.+-]{1,80}$/;
const MODULE_VERSION = /^[\w.+-]{1,40}$/;
const FAULT_OFFSET = /^0x[0-9a-f]{1,16}$/i;

/** Identifiers only: anything shaped like a path or free text is dropped. */
export function parseWindowsFault(output: string): FaultFacts {
  try {
    const parsed = JSON.parse(output) as Record<string, unknown>;
    const module = typeof parsed.module === 'string' ? win32.basename(parsed.module) : '';
    if (!MODULE_NAME.test(module)) return {};
    const version = typeof parsed.version === 'string' ? parsed.version : '';
    const offset = typeof parsed.offset === 'string' ? parsed.offset : '';
    return {
      faultingModule: module,
      ...(MODULE_VERSION.test(version) ? { faultingModuleVersion: version } : {}),
      ...(FAULT_OFFSET.test(offset) ? { faultOffset: offset.toLowerCase() } : {}),
    };
  } catch {
    return {};
  }
}

/**
 * Reads after the crash screen is up, so it can never delay it. Windows Error
 * Reporting can finish writing its event just after the exit is seen, so an
 * empty first read is repeated once.
 */
export async function collectFaultFacts(input: {
  pid: number | undefined;
  platform?: NodeJS.Platform;
  run?: ProbeRunner;
  retryDelayMs?: number;
}): Promise<FaultFacts> {
  const platform = input.platform ?? process.platform;
  if (platform !== 'win32' || !input.pid || !Number.isInteger(input.pid)) return {};
  const run = input.run ?? runProbe;
  const read = () =>
    run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', WINDOWS_FAULT_SCRIPT], {
      env: { ...process.env, VS_PROBE_PID: String(input.pid) },
      timeoutMs: TRUST_PROBE_DEADLINE_MS,
    })
      .then(parseWindowsFault)
      .catch((): FaultFacts => ({}));
  const first = await read();
  if (first.faultingModule) return first;
  await new Promise((resolve) => setTimeout(resolve, input.retryDelayMs ?? 3000));
  return read();
}

/** Basename on any host: a report from Windows may be classified anywhere in tests. */
export function programBasename(program: string): string {
  return program.includes('\\') ? win32.basename(program) : basename(program);
}
