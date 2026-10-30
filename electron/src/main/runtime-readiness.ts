import { runtimeReady, runtimeCompatible, runtimeDependenciesReady } from './runtime-project';
import type { TorchChoice } from './runtime-project';
import { withWindowsRocmReadinessDecision } from './runtime-windows-rocm';

/** Share only the native result just proven for this read-only decision. */
export function runtimeProjectReady(
  bundle: string,
  project: string,
  choice?: TorchChoice,
  managed = false,
): Promise<boolean> {
  return withWindowsRocmReadinessDecision(
    async () =>
      ((await runtimeReady(bundle, project, choice, managed)) ||
        (await runtimeCompatible(bundle, project, choice, managed))) &&
      (await runtimeDependenciesReady(project, undefined, choice, managed)),
  );
}
