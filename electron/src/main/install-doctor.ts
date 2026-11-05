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

/** Basename on any host: a report from Windows may be classified anywhere in tests. */
export function programBasename(program: string): string {
  return program.includes('\\') ? win32.basename(program) : basename(program);
}
