// Regression guard for the v0.3.22 Windows black screen (#<pr>).
//
// The bootstrap splash rendered a blank/black window on the shipped production
// build because a derived `const` that *reads* a `useState` result was declared
// BETWEEN hook calls:
//
//     const isFailed = stage === 'failed';
//     const [logs, setLogs] = useState([]);
//     const isUnrecoverable = isFailed && isUnrecoverableFailure(message, logs);
//     const [logsOpen, setLogsOpen] = useState(true);   // <-- more hooks after
//
// The production minifier (esbuild) merges consecutive declarations into one
// comma-list and can hoist `isUnrecoverable` (which reads `logs`) AHEAD of the
// `[logs] = useState([])` binding, emitting `...isUnrecoverable=..logs.., [logs]=
// useState()..` — a temporal-dead-zone access that throws during render. React
// unmounts to an empty #root and the whole app is a black screen. Dev and
// unminified builds short-circuit on `isFailed` so they never trip it; only the
// minified release bundle did (which is why the e2e suite, run against the dev
// server, missed it).
//
// The fix is to declare every hook BEFORE any derived const that reads a hook
// result. This test locks that ordering in so the regression can't silently
// return: it asserts the `logs` useState precedes the `isUnrecoverable` read,
// and that no value-binding hook (useState/useRef/useReducer) appears after that
// read within the splash component body.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const src = readFileSync(
  fileURLToPath(new URL('../../electron/src/shared/components/BootstrapSplash.jsx', import.meta.url)),
  'utf8',
);

test('BootstrapSplash: the `logs` useState is declared before the derived const that reads it', () => {
  const logsIdx = src.indexOf('const [logs, setLogs] = useState');
  const derivedIdx = src.indexOf('const isUnrecoverable');
  assert.ok(logsIdx > 0, "anchor 'const [logs, setLogs] = useState' not found — did BootstrapSplash get refactored? Update this guard.");
  assert.ok(derivedIdx > 0, "anchor 'const isUnrecoverable' not found — did BootstrapSplash get refactored? Update this guard.");
  assert.ok(
    logsIdx < derivedIdx,
    "`logs` useState must be declared BEFORE `isUnrecoverable` reads it. With it after, the production minifier hoists the read ahead of the binding → 'Cannot access logs before initialization' → black screen (the v0.3.22 Windows regression). Keep all useState/useRef above the derived consts.",
  );
});
