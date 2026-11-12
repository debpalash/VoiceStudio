import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

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

// Unused-key gate: every English key must be referenced by shipped code, or
// the catalog accumulates strings no screen renders (and every translator
// pays for them). Keys count as referenced when the whole key appears in
// non-test product code, in the mobile/scripts/backend trees that read the
// catalog, or when a dynamic key (`t(`ns.${x}`)`, `t('ns.' + x)`) names a
// prefix and the key's next segment appears in code too (longer or partial prefixes keep all
// their keys). Plural suffixes (_one/_other/...) are checked through their base key.
const repository = new URL('../../', import.meta.url);
// Prefixes consumed by code the scan cannot follow. `mobile.` is read by the
// native apps through generated catalogs, not through t('...') calls.
const ALLOWED_PREFIXES = ['mobile.'];
const codeFile = /\.(?:tsx?|jsx?|mjs|cjs|py|swift|kts?|xml|plist|json|ya?ml|html|strings|gradle)$/;
const skipDirectory = new Set([
  'node_modules', '.git', 'out', 'dist', 'build', 'release', '.venv', 'locales', 'tests',
  'Tests', 'test', 'docs', 'changelog.d', 'Pods', '.generated', 'omnivoice_data',
]);
const skipFile = /(?:\.test\.[jt]sx?|\.lock|package-lock\.json|strings[^/]*\.en\.json)$/;

async function corpusFiles(directory, output = []) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (skipDirectory.has(entry.name)) continue;
    const child = new URL(`${entry.name}${entry.isDirectory() ? '/' : ''}`, directory);
    if (entry.isDirectory()) await corpusFiles(child, output);
    else if (codeFile.test(entry.name) && !skipFile.test(entry.name)) output.push(child);
  }
  return output;
}

const CORPUS_TREES = ['electron/src/', 'mobile/', 'scripts/', 'backend/'];
// The Docker web build (and other partial trees) copy only electron/, so the code that
// reads the catalog is not all there and "unused" would be meaningless. Skip the gate
// outside CI; in CI a missing tree means the scan itself broke, so fail.
const missingTrees = [];
for (const tree of CORPUS_TREES) {
  await readdir(new URL(tree, repository)).catch((error) => {
    // Only an absent tree means a partial checkout; any other read failure is real.
    if (error.code !== 'ENOENT') throw error;
    missingTrees.push(tree);
  });
}
const inCI = !['', '0', 'false'].includes((process.env.CI ?? '').toLowerCase());
if (missingTrees.length) {
  assert.ok(
    !inCI,
    `Unused-key scan cannot find ${missingTrees.join(', ')}; update CORPUS_TREES`,
  );
  console.log(`Locale unused keys: skipped (partial checkout, no ${missingTrees.join(', ')})`);
  process.exit(0);
}

const chunks = [];
for (const tree of CORPUS_TREES) {
  for (const file of await corpusFiles(new URL(tree, repository))) {
    const text = await readFile(file, 'utf8').catch(() => '');
    if (text.length < 3_000_000) chunks.push(text);
  }
}
const corpus = chunks.join('\n');
// Whole identifiers in the corpus, so `example.title` is not counted as used just
// because `example.title_extra` appears.
const tokens = new Set(corpus.match(/[A-Za-z0-9_][A-Za-z0-9_.-]*/g) ?? []);
// Dynamic prefix → the literal suffixes that follow its placeholder ('' when none),
// so `t(`workflowRun.${id}_recipe`)` narrows by `id`, not by `id_recipe`. JS template
// literals (`ns.${x}`), concatenation ('ns.' + x) and Python f-strings
// (f"ns.{x}_name", which the backend sends as keys) all count.
const dynamicPrefixes = new Map();
const addDynamic = (prefix, suffix) => {
  const suffixes = dynamicPrefixes.get(prefix) ?? new Set();
  suffixes.add(suffix ?? '');
  dynamicPrefixes.set(prefix, suffixes);
};
for (const match of corpus.matchAll(
  /['"`]([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*[._]?)(?:['"`]\s*\+|\$\{[^}`]*\}([A-Za-z0-9_.-]*))/g,
))
  addDynamic(match[1], match[2]);
for (const match of corpus.matchAll(
  /\bf(['"])([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*[._]?)\{[^}'"]*\}([A-Za-z0-9_.-]*)\1/g,
))
  addDynamic(match[2], match[3]);
const baseKey = (key) => key.replace(/_(?:one|other|zero|two|few|many)$/, '');
const unused = [...catalog].filter((key) => {
  const base = baseKey(key);
  if (ALLOWED_PREFIXES.some((prefix) => key.startsWith(prefix))) return false;
  // A key may itself end in a plural-looking word (`reco_install_one`): try it whole first.
  if (tokens.has(key) || tokens.has(base)) return false;
  for (const [prefix, suffixes] of dynamicPrefixes) {
    if (!base.startsWith(prefix)) continue;
    // `ns.${x}`: only a one-segment namespace is narrowed by its next segment appearing
    // in code; any longer or partial prefix (`ns.sub.`, `ns.opt_`, `ns.short`) keeps every key.
    if (!/^[A-Za-z0-9_-]+\.$/.test(prefix)) return false;
    const rest = base.slice(prefix.length);
    for (const suffix of suffixes) {
      // `ns.${x}_suffix`: the part the placeholder fills is what must appear in code.
      const segment =
        suffix && rest.endsWith(suffix) ? rest.slice(0, -suffix.length) : rest.split('.')[0];
      if (segment.length >= 3 && tokens.has(segment)) return false;
    }
  }
  return true;
});
assert.deepEqual(
  unused,
  [],
  `en.json has ${unused.length} key(s) no source references; delete them from every ` +
    `catalog in electron/src/renderer/src/i18n/locales/ (or add a prefix to ALLOWED_PREFIXES ` +
    `in ${fileURLToPath(import.meta.url).split('/electron/')[1]} if code builds them dynamically):\n${unused
      .slice(0, 40)
      .map((key) => `  ${key}`)
      .join('\n')}${unused.length > 40 ? `\n  ... and ${unused.length - 40} more` : ''}`,
);
console.log(`Locale source keys: all ${catalog.size} en.json keys are referenced`);
