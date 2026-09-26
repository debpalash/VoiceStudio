import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';

const sourceDirectory = new URL('../src/renderer/src/', import.meta.url);
const englishPath = new URL('../src/renderer/src/i18n/locales/en.json', import.meta.url);

function flatten(value, prefix = '', output = new Set()) {
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof child === 'string') output.add(path);
    else if (child && typeof child === 'object') flatten(child, path, output);
  }
  return output;
}

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const url = new URL(`${entry.name}${entry.isDirectory() ? '/' : ''}`, directory);
    if (entry.isDirectory()) files.push(...(await sourceFiles(url)));
    else if (/\.(?:ts|tsx)$/.test(entry.name) && !/\.test\.(?:ts|tsx)$/.test(entry.name))
      files.push(url);
  }
  return files;
}

const catalog = flatten(JSON.parse(await readFile(englishPath, 'utf8')));
const used = new Map();
const callPattern = /\b(?:t|tr)\(\s*(['"])([A-Za-z0-9_.:-]+)\1/g;
const transPattern = /\bi18nKey\s*=\s*(['"])([A-Za-z0-9_.:-]+)\1/g;

const sharedControls = ['SearchableSelect', 'VoiceSelector'].map((name) =>
  new URL(`../src/shared/components/${name}.jsx`, import.meta.url));
for (const file of [...await sourceFiles(sourceDirectory), ...sharedControls]) {
  const source = await readFile(file, 'utf8');
  for (const pattern of [callPattern, transPattern]) {
    pattern.lastIndex = 0;
    for (const match of source.matchAll(pattern)) {
      const key = match[2];
      // Concatenated prefixes such as t('batch.status_' + status) are checked
      // through their concrete catalog entries, not as literal keys.
      if (/[._:]$/.test(key)) continue;
      const line = source.slice(0, match.index).split('\n').length;
      const locations = used.get(key) ?? [];
      locations.push(`${file.pathname.split('/src/renderer/src/')[1] ?? file.pathname}:${line}`);
      used.set(key, locations);
    }
  }
}

const missing = [...used].filter(
  ([key]) => !catalog.has(key) && !(catalog.has(`${key}_one`) && catalog.has(`${key}_other`)),
);
assert.deepEqual(
  missing,
  [],
  `Renderer uses ${missing.length} i18n key(s) absent from en.json:\n${missing
    .map(([key, locations]) => `  ${key} (${locations.join(', ')})`)
    .join('\n')}`,
);
console.log(`Locale source keys: ${used.size} static renderer keys exist in en.json`);
