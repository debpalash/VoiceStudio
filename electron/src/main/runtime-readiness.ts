import { runtimeReady, runtimeCompatible, runtimeDependenciesReady } from './runtime-project';
import { withWindowsRocmReadinessDecision } from './runtime-windows-rocm';

/** Share only the native result just proven for this read-only decision. */
export function runtimeProjectReady(bundle: string, project: string): Promise<boolean> {
  return withWindowsRocmReadinessDecision(
    async () =>
      ((await runtimeReady(bundle, project)) || (await runtimeCompatible(bundle, project))) &&
      (await runtimeDependenciesReady(project)),
  );
}
