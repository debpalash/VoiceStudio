import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

/** Reject unsupported Node before loading tools or starting development setup. */
export function assertSupportedNode(version = process.versions.node) {
  const [major, minor] = version.split('.').map(Number);
  if (major > 22 || (major === 22 && minor >= 12)) return;
  throw new Error(
    `VoiceStudio needs Node.js 22.12.0 or newer; you are running ${version}. ` +
      'Install a current Node.js LTS release from https://nodejs.org, ' +
      'reopen your terminal, and run the command again.',
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    assertSupportedNode();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
