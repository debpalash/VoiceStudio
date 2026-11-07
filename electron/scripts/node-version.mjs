// Keep this check dependency-free so unsupported Node can explain the problem
// before loading Electron's downloader or starting development setup.
export function assertSupportedNode(version = process.versions.node) {
  const [major, minor] = version.split('.').map(Number);
  if (major > 22 || (major === 22 && minor >= 12)) return;
  throw new Error(
    `VoiceStudio needs Node.js 22.12.0 or newer; you are running ${version}. ` +
      'Install a current Node.js LTS release from https://nodejs.org, ' +
      'reopen your terminal, and run the command again.',
  );
}
