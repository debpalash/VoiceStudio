/**
 * Dynamic `t()` families: every namespace used by a template-literal call must be
 * declared, and the closed ones must be complete.
 *
 * `locale-source-keys.mjs` checks static keys only -- it deliberately skips
 * `t(`ns.${x}`)` because the value is not a literal. That leaves two holes this
 * file closes:
 *
 * 1. a *new* dynamic family can appear with nobody deciding that its values are
 *    covered, and
 * 2. a family whose values come from a closed union in this codebase can drift
 *    from the catalog: add a member to the union and the screen renders a raw key
 *    like `failureCause.newthing.title`.
 *
 * The union members are parsed from the source rather than listed here, so adding
 * one fails this check until the catalog carries it.
 */
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';

const rendererRoot = new URL('../src/renderer/src/', import.meta.url);
const englishPath = new URL('../src/renderer/src/i18n/locales/en.json', import.meta.url);

/**
 * Namespace -> where its values come from. A namespace appears here only because
 * its values are *not* enumerable from this repository (they arrive from the
 * backend, a saved document, or a generated catalogue), so completeness cannot be
 * asserted mechanically. Deciding that is the point of this list: a new dynamic
 * namespace fails the check below until someone makes the same decision.
 */
const DYNAMIC_NAMESPACES = new Map([
  ['about', 'self-check statuses come from the diagnostics probe'],
  ['account', 'notice keys come from the account service'],
  ['asr_missing', 'model tags come from the ASR catalogue'],
  ['audioQuality', 'warning kinds come from the audio-quality report'],
  ['audiobook', 'blocker ids come from the audiobook planner'],
  ['backend', 'startup stages and failure causes come from the backend payload'],
  ['batch', 'batch states come from the client queue'],
  ['bootstrap', 'setup phases come from the backend status payload'],
  ['calls', 'example ids come from the telephony integration catalogue'],
  ['clone', 'recents keys and preset ids come from the preset catalogue'],
  ['cloneQuality', 'quality presets and bit depths are generated from the matrix'],
  ['cloudsync', 'collection ids and toggle states come from the sync engine'],
  ['dub', 'translation modes are a closed set owned by the dub pipeline'],
  ['failureAction', 'action targets come from the failure payload'],
  ['failureCause', 'checked below against the FailureCause union'],
  ['firstrun', 'storage kinds come from the setup payload'],
  ['generationQueue', 'queue statuses come from the client queue'],
  ['integrationCatalog', 'capability ids come from the integration catalogue'],
  ['languagePicker', 'capability states are computed locally'],
  ['modelInstall', 'checked below against the InstallStage union'],
  ['modelLicense', 'licence verdicts and hints come from the licence service'],
  ['preferences', 'section ids come from the settings registry'],
  ['proPage', 'plan fields come from the licence payload'],
  ['recording', 'channel counts are numbers'],
  ['reportBug', 'report field names are local'],
  ['settings', 'codes and stages come from the backend, scopes from settings'],
  ['stories', 'split tones and values come from the story parser'],
  ['themeAppearance', 'appearance ids are a closed set owned by the theme'],
  ['twilioIntegration', 'step and outcome ids come from the telephony flow'],
  ['update', 'channel names come from the updater'],
  ['workflowRun', 'validation issue codes come from the workflow validator'],
  ['workflows', 'step kinds and template ids come from saved workflows'],
]);

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

function flatten(value, prefix = '', output = new Set()) {
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === 'object') flatten(child, path, output);
    else output.add(path);
  }
  return output;
}

/** Literal members of an exported string union, in source order. */
function unionMembers(source, typeName) {
  const match = source.match(new RegExp(`export type ${typeName} =([^;]+);`, 's'));
  assert.ok(match, `could not read the ${typeName} union`);
  return [...match[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
}

const catalog = flatten(JSON.parse(await readFile(englishPath, 'utf8')));
const namespaces = new Map();
for (const file of await sourceFiles(rendererRoot)) {
  const source = await readFile(file, 'utf8');
  for (const match of source.matchAll(/\bt\(\s*`([^`]*)`/g)) {
    const texture = match[1];
    const namespace = texture.split(/[.{]/)[0];
    if (!namespace) continue;
    const seen = namespaces.get(namespace) ?? new Set();
    seen.add(texture);
    namespaces.set(namespace, seen);
  }
}

const undeclared = [...namespaces.keys()].filter((name) => !DYNAMIC_NAMESPACES.has(name));
assert.deepEqual(
  undeclared,
  [],
  `These namespaces are used through a dynamic t() call but are not declared in\n` +
    `tests/locale-dynamic-families.mjs:\n${undeclared
      .map((name) => `  ${name}: ${[...namespaces.get(name)].map((t) => `t(\`${t}\`)`).join(', ')}`)
      .join('\n')}\nCheck how their values stay covered, then add them to DYNAMIC_NAMESPACES.`,
);

const emptyNamespaces = [...DYNAMIC_NAMESPACES.keys()].filter(
  (name) => ![...catalog].some((key) => key === name || key.startsWith(`${name}.`)),
);
assert.deepEqual(
  emptyNamespaces,
  [],
  `A dynamically-used namespace has no catalog entry at all, so it renders raw keys:\n${emptyNamespaces
    .map((name) => `  ${name}`)
    .join('\n')}`,
);

const failureCause = unionMembers(
  await readFile(new URL('lib/api/failure-cause.ts', rendererRoot), 'utf8'),
  'FailureCause',
);
const installStage = unionMembers(
  await readFile(new URL('lib/model-install-state.ts', rendererRoot), 'utf8'),
  'InstallStage',
);

const missing = [];
for (const cause of failureCause) {
  for (const key of [`failureCause.${cause}.title`, `failureCause.${cause}.fix1`]) {
    if (!catalog.has(key)) missing.push(key);
  }
}
for (const stage of installStage) {
  if (!catalog.has(`modelInstall.${stage}`)) missing.push(`modelInstall.${stage}`);
}
assert.deepEqual(
  missing,
  [],
  `A closed union gained a member with no catalog entry, so that state renders a raw key:\n${missing
    .map((key) => `  ${key}`)
    .join('\n')}`,
);

console.log(
  `Dynamic i18n families: ${namespaces.size} namespaces declared across ` +
    `${[...namespaces.values()].reduce((total, set) => total + set.size, 0)} call sites; ` +
    `${failureCause.length} failure causes and ${installStage.length} install stages complete`,
);
