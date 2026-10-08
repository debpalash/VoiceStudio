export function clearCtranslate2ExecutableStack(
  sitePackages: string,
  platform?: NodeJS.Platform,
): Promise<number>;
export function repairSourceNativeLibraries(
  project: string,
  platform?: NodeJS.Platform,
  options?: { env?: NodeJS.ProcessEnv; run?: typeof import('node:child_process').execFile },
): Promise<void>;
