import { readFile, readdir } from 'node:fs/promises';

const directory = new URL('../src/renderer/src/i18n/locales/', import.meta.url);
function flatten(value, prefix = '', output = {}) {
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof child === 'string') output[path] = child;
    else flatten(child, path, output);
  }
  return output;
}
const english = flatten(JSON.parse(await readFile(new URL('en.json', directory), 'utf8')));
// Locales may carry CLDR plural forms English lacks (ar `_few`, ru `_many`),
// but only for keys English pluralizes; anything else is a stale orphan.
const PLURAL = /^(.*)_(?:zero|one|two|few|many|other)$/;
const isOrphan = (key) => {
  if (key in english) return false;
  const base = PLURAL.exec(key)?.[1];
  return !base || !(`${base}_other` in english);
};
const missing = {};
const orphans = {};
const namespaces = {};
for (const file of (await readdir(directory)).filter(
  (name) => name.endsWith('.json') && name !== 'en.json',
)) {
  const translated = flatten(JSON.parse(await readFile(new URL(file, directory), 'utf8')));
  missing[file.slice(0, -5)] = Object.keys(english).filter((key) => !(key in translated));
  const extra = Object.keys(translated).filter(isOrphan);
  if (extra.length) orphans[file.slice(0, -5)] = extra;
  for (const key of missing[file.slice(0, -5)]) {
    const namespace = key.split('.')[0];
    namespaces[namespace] = (namespaces[namespace] ?? 0) + 1;
  }
}
const missingEntries = Object.values(missing).reduce((sum, keys) => sum + keys.length, 0);
console.log(
  JSON.stringify(
    {
      missingEntries,
      byNamespace: Object.fromEntries(Object.entries(namespaces).sort((a, b) => b[1] - a[1])),
      orphans,
      ...(process.argv.includes('--details') ? { missing } : {}),
    },
    null,
    2,
  ),
);
if (missingEntries > 0 || Object.keys(orphans).length > 0) process.exitCode = 1;
