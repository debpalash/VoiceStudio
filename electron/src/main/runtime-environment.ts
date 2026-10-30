import type { TorchVariant } from './runtime-project';

export interface RuntimeEnvironmentContext {
  managed: boolean;
  platform: NodeJS.Platform;
  variant: TorchVariant;
}

export function runtimeChildEnv(
  env: Readonly<NodeJS.ProcessEnv>,
  context: RuntimeEnvironmentContext,
): NodeJS.ProcessEnv {
  const child = { ...env };
  if (context.managed && context.platform === 'win32' && context.variant === 'rocm') {
    for (const key of Object.keys(child)) {
      if (['HIP_PATH', 'ROCM_HOME', 'ROCM_PATH'].includes(key.toUpperCase())) delete child[key];
    }
  }
  return child;
}
