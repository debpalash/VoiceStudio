/** An OS launch refusal is different from an interpreter that ran and failed imports. */
export class RuntimeLaunchRefusedError extends Error {
  constructor(
    readonly program: string,
    cause: unknown,
  ) {
    super(cause instanceof Error ? cause.message : String(cause), { cause });
    this.name = 'RuntimeLaunchRefusedError';
  }
}

export function runtimeLaunchRefusal(
  program: string,
  error: unknown,
): RuntimeLaunchRefusedError | null {
  const failure = error as { code?: unknown; syscall?: unknown } | null;
  if (!['UNKNOWN', 'EACCES', 'EPERM'].includes(String(failure?.code ?? ''))) return null;
  // An EPERM from terminating an already-running probe is not a spawn refusal.
  if (typeof failure?.syscall === 'string' && !/^spawn(?:\s|$)/.test(failure.syscall)) return null;
  return new RuntimeLaunchRefusedError(program, error);
}
