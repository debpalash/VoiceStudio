// Canonical's Electron Snap guidance uses strict Snap confinement instead of
// Chromium's inner sandbox. Apply this only to the packaged Snap command.sh.
// https://snapcraft.io/docs/reference/interfaces/browser-support-interface/
// electron-builder appends the flag itself, shell-quoted, when allow-sandbox is false.
const invocation = /^exec "\$SNAP\/desktop-init\.sh" "\$SNAP\/desktop-common\.sh" "\$SNAP\/desktop-gnome-specific\.sh" "\$SNAP\/voicestudio"( (?:--no-sandbox|'--no-sandbox'))? "\$@"[\t ]*$/gm;

function executableLine(command) {
  const matches = [...command.matchAll(invocation)];
  if (!command.startsWith('#!') || !command.includes('\n') || matches.length !== 1) {
    throw new Error('Snap launcher has an unexpected executable invocation');
  }
  return matches[0];
}

export function verifySnapLauncher(command) {
  const match = executableLine(command);
  if (!match[1] || command.includes('snapctl is-connected browser-support')) {
    throw new Error('Snap launcher must use strict confinement without a Chromium sandbox permission gate');
  }
}

export function configureSnapLauncher(command) {
  const match = executableLine(command);
  const configured = match[1]
    ? command
    : command.slice(0, match.index) + match[0].replace(' "$@"', ' --no-sandbox "$@"') + command.slice(match.index + match[0].length);
  verifySnapLauncher(configured);
  return configured;
}
