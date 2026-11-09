// Debian maintainer scripts: electron-builder's own, plus the pre-rename
// terminal name. Releases before the `voicestudio` command named the Linux
// executable `voicestudio-electron`; scripts that call it keep working.
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

export const LEGACY_COMMAND = '/usr/bin/voicestudio-electron';

// `${executable}` / `${sanitizedProductName}` are electron-builder template
// macros; any other `${...}` would fail the build, so the shell uses `$var`.
export const LEGACY_INSTALL = `
# Pre-rename terminal name. Replace only a missing or dangling link (the old
# package's alternative), never a working command someone else installed.
if [ ! -e '${LEGACY_COMMAND}' ]; then
    ln -sfn '/opt/\${sanitizedProductName}/\${executable}' '${LEGACY_COMMAND}'
fi
`;

export const LEGACY_REMOVE = `
# Remove the pre-rename terminal name only when it is this package's link.
if [ "$(readlink '${LEGACY_COMMAND}' 2>/dev/null)" = '/opt/\${sanitizedProductName}/\${executable}' ]; then
    rm -f '${LEGACY_COMMAND}'
fi
`;

function upstreamTemplate(name) {
  // The templates electron-builder itself would use (its own app-builder-lib).
  const builder = createRequire(import.meta.url).resolve('electron-builder/package.json');
  const root = dirname(createRequire(builder).resolve('app-builder-lib/package.json'));
  return readFileSync(join(root, 'templates/linux', name), 'utf8');
}

let generated;
/** Template paths for `deb.afterInstall` / `deb.afterRemove`, written on first use. */
export function debMaintainerScripts() {
  if (!generated) {
    const dir = mkdtempSync(join(tmpdir(), 'voicestudio-deb-'));
    const afterInstall = join(dir, 'after-install.tpl');
    const afterRemove = join(dir, 'after-remove.tpl');
    writeFileSync(afterInstall, upstreamTemplate('after-install.tpl') + LEGACY_INSTALL);
    writeFileSync(afterRemove, upstreamTemplate('after-remove.tpl') + LEGACY_REMOVE);
    generated = { afterInstall, afterRemove };
  }
  return generated;
}
