import { once } from 'node:events';

export async function forceAndWaitForExit(child, app, kill, signal) {
  const stopped = Promise.all([
    once(child, 'exit', { signal }),
    once(app, 'close', { signal }),
  ]);
  let killError;
  try { kill(); } catch (error) { killError = error; }
  // taskkill can lose a race to an exiting renderer and return nonzero even
  // though it stopped the tree. The observed exits decide; a live process still
  // times out, preserving the command failure for diagnosis.
  try { await stopped; } catch (error) { throw killError ?? error; }
}
