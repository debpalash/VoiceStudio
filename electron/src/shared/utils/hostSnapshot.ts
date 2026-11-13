export interface HostSnapshotLike {
  os?: string;
  arch?: string;
  cpu?: string | null;
  ramGb?: number;
  diskFreeGb?: number | null;
  gpu?: string | null;
  runtime?: {
    pythonPresent?: boolean | null;
    uvPresent?: boolean | null;
    pathFlags?: string[];
  };
}

/** Resolve with the task's value, or null if it rejects or outlasts `ms`. */
export async function withDeadline<T>(task: Promise<T>, ms: number): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      task,
      new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), ms);
      }),
    ]);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const present =(value: boolean | null | undefined) =>
  value == null ? 'unknown' : value ? 'present' : 'MISSING';

/**
 * Report lines for facts the desktop shell read itself. `machine` is only
 * needed when the backend could not supply `/system/info`; the runtime lines
 * are always useful because the backend cannot describe its own missing files.
 */
export function formatHostSnapshot(
  snapshot: HostSnapshotLike | null | undefined,
  { machine }: { machine: boolean },
): string[] {
  if (!snapshot) return [];
  const lines: string[] = [];
  if (machine) {
    if (snapshot.os) lines.push(`os (app): ${snapshot.os}`);
    if (snapshot.arch) lines.push(`arch (app): ${snapshot.arch}`);
    if (snapshot.cpu) lines.push(`cpu_model (app): ${snapshot.cpu}`);
    if (snapshot.ramGb) lines.push(`ram_total_gb (app): ${snapshot.ramGb}`);
    if (snapshot.gpu) lines.push(`gpu (app): ${snapshot.gpu}`);
  }
  if (snapshot.diskFreeGb != null) lines.push(`runtime disk free gb: ${snapshot.diskFreeGb}`);
  const runtime = snapshot.runtime;
  if (runtime) {
    lines.push(`runtime python: ${present(runtime.pythonPresent)}`);
    lines.push(`runtime uv: ${present(runtime.uvPresent)}`);
    lines.push(
      `runtime path flags: ${runtime.pathFlags?.length ? runtime.pathFlags.join(', ') : 'none'}`,
    );
  }
  return lines;
}
