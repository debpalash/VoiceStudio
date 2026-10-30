import type { NativeRuntimeDiagnosis, RuntimeRunner } from './runtime-project';
import { WINDOWS_ROCM_ABI_PROBE } from './runtime-windows-rocm';
import { WINDOWS_ROCM_IMPORT_TIMEOUT_MS } from './runtime-probe';

/** Check interpreter ABI before loading native wheels; Windows HIP is repaired by its installer. */
export async function inspectRuntimeInterpreter(
  python: string,
  project: string,
  run: RuntimeRunner,
  signal: AbortSignal,
  windowsRocm: boolean,
  emulatedX64: boolean,
  imports: string,
  nativeImports: string,
  repairPackages: readonly string[],
  diagnoseNative: () => Promise<NativeRuntimeDiagnosis>,
): Promise<{ existingPython: boolean; repairPackages: string[]; missingSystemLibraries: string[] }> {
  const repairs: string[] = [];
  let missingSystemLibraries: string[] = [];
  try {
    await run(
      python,
      [
        '-c',
        windowsRocm
          ? WINDOWS_ROCM_ABI_PROBE
          : 'import sys; assert sys.version_info[:2] == (3, 11)' +
            (emulatedX64
              ? "; import platform; assert platform.machine().upper() in ('AMD64', 'X86_64')"
              : ''),
      ],
      project,
    );
    // Never import a broken HIP wheel or hold its DLLs before replacement.
    if (windowsRocm) {
      try {
        await run(python, ['-c', 'import sentencepiece'], project, undefined, {
          timeoutMs: WINDOWS_ROCM_IMPORT_TIMEOUT_MS,
        });
      } catch {
        signal.throwIfAborted();
        repairs.push('sentencepiece');
      }
      return { existingPython: true, repairPackages: repairs, missingSystemLibraries };
    }
    try {
      await run(python, ['-c', imports], project);
    } catch {
      try {
        await run(python, ['-c', nativeImports], project);
      } catch {
        const diagnosis = await diagnoseNative();
        missingSystemLibraries = diagnosis.missingSystemLibraries;
        if (!missingSystemLibraries.length) repairs.push(...repairPackages);
      }
      signal.throwIfAborted();
      try {
        await run(python, ['-c', 'import sentencepiece'], project);
      } catch {
        repairs.push('sentencepiece');
      }
    }
    return { existingPython: true, repairPackages: repairs, missingSystemLibraries };
  } catch {
    signal.throwIfAborted();
    return { existingPython: false, repairPackages: repairs, missingSystemLibraries };
  }
}
