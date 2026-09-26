# VoiceStudio brand

VoiceStudio uses a waveform-and-spark mark. The waveform identifies audio; the
spark identifies creation. The product voice is clear, calm, and direct.

## Assets

| Surface | Source |
|---|---|
| Primary vector mark | `docs/logo.svg` |
| README mark | `docs/logo.png` and `docs/logo-256.png` |
| Browser icon | `electron/public/favicon.svg` |
| In-app mark | `frontend/src/components/brand/VoiceStudioMark.jsx` |
| Desktop/platform icons | `electron/build/icons/` |
| Electron sidebar and browser icon | `electron/public/favicon.svg` via `electron/src/renderer/src/lib/brand.ts` |
| Sidebar/launchpad artwork | `electron/public/signal-field.webp` |

Regenerate every desktop icon from the canonical vector after changing the
mark:

Export the canonical vector to the required PNG, ICO, and ICNS sizes under
`electron/build/icons/`, then run the Electron packaging contract.

Do not redraw the waveform per screen. Use `VoiceStudioMark` in React and the
canonical SVG elsewhere so the silhouette stays recognizable at 16–512 px.

## Palette

| Role | Color |
|---|---|
| Voice rose | `#F47FA3` |
| Soft highlight | `#FFD0DB` |
| Creative amber | `#F7AE73` |
| Spark | `#FFE5B5` |
| Plum tile | `#211B2B` → `#120F18` |

The mark may render in one color inside app chrome. Keep the waveform and spark
together; do not put text inside the icon or add another enclosing ring.

## Name and copy

- Product name: **VoiceStudio**—one word, capital V and S.
- Voice: concise, professional, warm, and direct.
- Promise: local-first creation without a subscription or usage meter.
- Avoid absolute privacy claims: network-backed engines, downloads, analytics,
  and cloud integrations are explicit opt-ins, not nonexistent.
- Attribute the bundled default model as **k2-fsa/OmniVoice** where model lineage
  matters. OmniVoice is an upstream model/runtime name, not the product name.

## Compatibility names

The rebrand must not break existing installations. Keep these identifiers until
a separately tested migration exists:

- `omnivoice` Python imports and package name
- `omnivoice-studio` binary/package and published container coordinates
- `OMNIVOICE_*` environment variables and `X-OmniVoice-*` API headers
- existing OmniVoice data/cache directories and uninstall aliases
- upstream repositories, model IDs, classes, and engine IDs

Visible copy can explain those compatibility names, but must not silently rename
them on disk or over the wire.

Electron uses the shared multi-resolution ICO for Windows window/taskbar and tray
icons, the shared PNG for Linux and macOS runtime icons, and the ICNS for the macOS
bundle. The tray icon restores the window; closing the app retains its existing
quit behavior. Installed executable icons are applied when building the installer.
