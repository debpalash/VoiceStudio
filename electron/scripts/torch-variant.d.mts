export type TorchVariant = 'auto' | 'cpu' | 'cuda' | 'rocm';
export type SelectedTorchVariant = Exclude<TorchVariant, 'auto'> | 'native';
export function nvidiaDriverPresent(
  platform?: NodeJS.Platform,
  exists?: (path: string) => boolean,
  env?: NodeJS.ProcessEnv,
): boolean;
export function hasNvidiaGpu(options?: {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  exists?: (path: string) => boolean;
  run?: typeof import('node:child_process').execFile;
}): Promise<boolean>;
export function requestedTorchVariant(env?: NodeJS.ProcessEnv): TorchVariant;
export function selectTorchVariant(options?: {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  arch?: string;
  nvidiaAvailable?: () => Promise<boolean>;
}): Promise<SelectedTorchVariant>;
export function torchSyncArgs(variant: SelectedTorchVariant): string[];
