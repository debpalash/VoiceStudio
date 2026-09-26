# Visual-regression baseline

This is the **safety net for the CSS → Tailwind v4 migration**. Before a CSS
rule is converted to utility classes, these baseline screenshots capture how a
component renders today; after the conversion, `bun run test:visual` proves it
still renders pixel-for-pixel the same. If a conversion changes a pixel that
wasn't supposed to change, the diff fails the run.

## How it works

There is **no Python backend** involved. A tiny Vite-served harness
(`harness.html` → `harness.jsx`) renders ONE presentational leaf component in
isolation, wrapped in a theme, and Playwright snapshots just that element
(`#visual-root`). It runs through the project's real Vite 8 + Tailwind v4 +
token pipeline, so the snapshots reflect the actual build output.

- `harness.html` / `harness.jsx` — the isolated render target. Reads
  `?component=<Name>&theme=<theme>` from the URL, applies the theme via the
  `[data-theme]` selector (default = bare `:root` Gruvbox Dark), and renders
  the component from the registry. Loads the same fonts + token layers as the
  real app (`ui/tokens.css`, `ui/themes.css`, `index.css`).
- `specs.jsx` — the registry: each component → a small spread of its
  variants/states. Leaf entries are **pure** (no backend hooks, no i18n, no app
  context); page/panel entries opt into providers (see below).
- `providers.jsx` — the **opt-in** wrapper that lets a PAGE or PANEL render
  with no backend: it seeds the Zustand store, react-i18next, a react-query
  cache, and (optionally) `window.fetch`. Nothing here runs for a pure leaf
  spec, so leaf baselines are unaffected.
- `manifest.ts` — the list of `COMPONENTS` × `THEMES` the Playwright test
  iterates. Read by `baseline.visual.spec.ts`.
- `__screenshots__/` — committed baseline PNGs (`<Component>-<theme>.png`).
- `../../../playwright.visual.config.ts` — dedicated Playwright config
  (separate from the e2e config). Starts its own Vite dev server on
  `VISUAL_PORT` (default 3902), disables animations, hides the caret.

## Commands

```bash
bun run test:visual          # run snapshots against the committed baselines
bun run test:visual:update   # regenerate baselines (after an INTENTIONAL change)
```

## Adding a component to the suite

1. Add an entry to `SPECS` in `specs.jsx` keyed by the component name, rendering
   a representative spread of its variants/states. Keep it pure.
2. Add that same name to `COMPONENTS` in `manifest.ts` (a name present in the
   manifest but missing from `specs.jsx` renders an error state, which itself
   fails the snapshot — so drift is caught, not silent).
3. Run `bun run test:visual:update` to generate the new baselines, eyeball the
   PNGs, and commit them.

## Rendering a PAGE or PANEL (provider-wrapped)

Leaf components render bare. Real pages and settings panels instead reach for
the Zustand store (`useAppStore`), react-i18next, react-query
(`QueryClientProvider` + `useQuery` hooks), and direct `api/*` fetches — so
they can't render against "nothing". To snapshot one deterministically with
**no Python backend**, a spec declares a `providers` block; the harness then
seeds that infrastructure (in `providers.jsx`) with representative data before
the first paint. This is the safety net for converting page/panel CSS to
Tailwind, same as for leaves.

A `providers` block (every field optional) seeds exactly what the target uses:

```jsx
MyPanel: {
  width: 640,                       // wider, deterministic frame (default 720)
  providers: {
    // 1. Zustand — merged into the live store. Object, or (ctx) => object,
    //    where ctx = { theme } so you can align the store's active theme with
    //    the rendered data-theme variant.
    store: ({ theme }) => ({ locale: 'en', theme: theme === 'default' ? 'gruvbox' : theme }),

    // 2. react-query — pre-fill the cache so useQuery() hooks resolve to data
    //    instead of a spinner. Use the real keys from src/api/hooks.ts.
    query: (qc) => qc.setQueryData(queryKeys.systemInfo, { app_version: '0.3.6', ffmpeg_ok: true }),

    // 3. window.fetch — for components that call api/* directly (no react-query),
    //    e.g. a GET on mount. Return a body object for a match, or undefined to
    //    let an un-mocked URL reject loudly.
    fetch: (url) => url.includes('/api/settings/storage/models-dir')
      ? { configured: '', effective: '~/.cache/huggingface', default: '~/.cache/huggingface' }
      : undefined,
  },
  render: () => <MyPanel />,
},
```

Then add the spec name to `COMPONENTS` in `manifest.ts` (same as a leaf) and
run `bun run test:visual:update`. i18n is forced to English so a headless
navigator locale can't drift the snapshot; the snapshot QueryClient disables
retries/refetching so a seeded value never gets overwritten.

Three worked examples ship today: **AppearancePanel** (store + i18n only),
**GeneralTab** (store + i18n + a seeded `useSystemInfo` query), and
**StoragePanel** (a `fetch`-stubbed GET on mount).

### Limitation: deeply backend-coupled pages aren't harness-able (yet)

Seeding covers store reads, one-shot/polling `useQuery` hooks, and request/
response `api/*` calls. It does **not** fake a *live* stream or native bridge.
A page that needs a running `EventSource`/WebSocket SSE feed, Tauri `invoke`
commands that must return real data, or a `@tanstack/react-virtual` list whose
row heights are measured from real backend rows, won't render a representative,
deterministic frame from seeding alone — don't force it. **`ModelStoreTab`** is
the current example: it opens a `/setup/download-stream` `EventSource` on mount,
virtualizes its model rows, and takes required `info`/`modelBadge` props, so it
is deliberately left out until those couplings are mockable. Prefer a more
tractable target over a flaky baseline.

## Updating baselines after an intentional change

When you deliberately change a component's appearance (including a CSS →
Tailwind conversion that is *meant* to look identical but the diff flags
sub-pixel noise):

1. Run `bun run test:visual:update`.
2. **Review the regenerated PNGs in the diff** — confirm only the intended
   pixels moved. This review is the whole point; don't blind-accept.
3. Commit the updated `__screenshots__/*.png` alongside the code change.

## Why this is local/manual, not a CI gate (yet)

Screenshots are sensitive to font rendering and sub-pixel anti-aliasing, which
differ between macOS and the Linux CI runners. Committed baselines are correct
for the machine that generated them; running them unchanged on a different OS
would produce spurious diffs. So this suite is run **locally/manually** during
the migration as a developer safety net.

CI-gating can come later once the baselines are stabilized for the CI platform
— e.g. by generating them inside the same Linux container CI uses (pin
`PLAYWRIGHT_CHROMIUM` / the Playwright image), or by committing per-platform
baselines. Until then, do **not** wire `test:visual` into a blocking workflow.
