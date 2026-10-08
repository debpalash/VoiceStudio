# Uninstalling VoiceStudio

VoiceStudio is **fully local** — it has no accounts, no cloud state, and no
background services. Removing it is just deleting the app plus the folders it
wrote on your machine. This page lists every one of those folders per platform,
and ships a script that finds and removes them for you (with a dry-run first).

> **TL;DR (the space hogs):** the two folders worth deleting are the **model
> cache** (the Hugging Face weights — several GB) and the **managed Python
> environment** (`runtime/project/.venv` inside the app folder — a few GB).
> Everything else is small.

## In the app (easiest — no repo needed)

**Settings → Storage → Remove all data.** It lists every folder this install
owns with its real size, lets you opt in (separately) to the shared Hugging Face
model cache, asks you to type `DELETE`, then removes everything and quits.

The Electron build finishes deletion through its signed desktop helper after the
window exits, because Chromium keeps parts of its profile directory locked while
the app is open. The same ownership checks and model-cache opt-in still apply.

This is the right path if you installed the **.dmg / .exe / AppImage / .deb** —
you don't have the repo, so the script below isn't available to you.

> **You may not need to uninstall.** Right above it, **Reset & remove** does the
> same job at any scale you like — and leaves you with a working app instead of
> no app. See [Resetting](#resetting-instead-of-uninstalling) below.

## Resetting instead of uninstalling

**Settings → Storage → Reset & remove** puts part — or all — of VoiceStudio back to
how it shipped, without removing the app. Every option shows its real size before
you commit, and the app restarts itself when it's done.

| Option | What it removes | What it keeps |
| --- | --- | --- |
| **UI preferences only** | Theme, layout, language, dub settings | Everything on disk |
| **All settings** | The above, plus saved settings on disk (engine choices, voice defaults) | Voices, projects, audio, models |
| **Downloaded assets & models** | Model weights, sidecar engines, audio tools, caches | Everything you made |
| **Everything VoiceStudio did** | All of the above, plus voices, projects, generated audio, history, logs | The app itself, and the Python environment it runs on |

"Choose exactly what to remove" opens the same list as individual checkboxes, so
you can drop just the model weights, just a wedged sidecar engine, or just the
history — whatever is actually wrong.

Two things it deliberately does **not** touch:

- **Your storage locations.** If you pointed VoiceStudio at a custom data or model
  directory, a settings reset keeps that pointer. Clearing it would strand
  gigabytes of already-downloaded weights at a path the app no longer looks in.
- **The managed Python environment.** "Everything VoiceStudio did" still leaves you
  with a working app that restarts on the first-run screen. If you want the
  interpreter gone too, that's **Remove all data** — the section above.

The shared Hugging Face model cache is called out separately wherever it applies:
on macOS and Linux it's the standard cache other AI tools use too, so removing it
may delete models VoiceStudio never downloaded. (On Windows, and in portable
installs, the cache is VoiceStudio's own — there's nothing to share, and the app
says so.)

## The one-command uninstaller (from a clone)

From a clone or the source tarball:

```bash
# macOS / Linux — prints what it WOULD delete, with sizes, and stops:
scripts/uninstall.sh

# actually delete, after you've read the dry-run:
scripts/uninstall.sh --yes
```

```powershell
# Windows (PowerShell) — dry-run, then delete:
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Yes
```

**If you opted in to anonymous analytics** (and only then), the delete run
sends one last content-free `app_uninstalled` ping before removing your data —
app version, OS name, and the random per-install id, nothing else — and says
so on the console. Best-effort with a 2-second timeout: a dead network never
blocks the uninstall. If you never opted in (the default), nothing is sent and
nothing is printed; the dry-run never sends anything either way.

It covers the current desktop app and anything a final Tauri-era install
(`com.debpalash.omnivoice-studio`) left behind. Add `--app` (macOS/Linux) or
`-RemoveApp` (Windows) to also remove the installed app itself. On Windows,
run `-Yes -RemoveApp` from a normal PowerShell window, without administrator
rights. An elevated request stops before removing app data or launching an
uninstaller; a dry run still lists the plan. If the installed app needs
administrator approval, its own uninstaller asks for it. Alternatively, remove
VoiceStudio through **Settings → Apps**, then run `-Yes` without `-RemoveApp`
to clean up its remaining data.

The script honors your custom locations: if you set `OMNIVOICE_DATA_DIR`,
`OMNIVOICE_CACHE_DIR`, `HF_HOME`, or `HF_HUB_CACHE` (or picked custom
data/model folders during setup), export the same variables before running it
and it will target those instead of the defaults. It never touches anything
outside the VoiceStudio folders, and without `--app` / `-RemoveApp` it does
**not** delete the app itself (see "Remove the app" below) — so it's safe to run
even if you only want to reclaim disk space and keep the app installed. A
runtime you moved to a custom folder is removed only if the app created it
there; an existing environment it merely reused is left alone.

## What VoiceStudio writes, and where

Four kinds of data, in up to four locations:

| What | Size | Notes |
|---|---|---|
| **Model cache** (Hugging Face weights) | GBs | The big one. Shared HF cache — see the caveat below. |
| **Managed Python env** (`runtime/project/.venv` in the app folder) | GBs | Rebuilt automatically if you reinstall. |
| **App data** (voices, projects, DB, generated audio, sidecar engines, logs) | small–GBs | Your voice profiles and history live here. |
| **App folder** (window state, update settings, desktop logs, updater cache) | tiny | Desktop-shell state. |

### macOS

```
~/Library/Application Support/OmniVoice/      ← app data (voices, projects, omnivoice.db, outputs, omnivoice.log)
~/Library/Application Support/VoiceStudio/    ← app folder + the managed Python env (runtime/project/.venv)
~/Library/Logs/VoiceStudio/                   ← desktop-shell logs
~/Library/Caches/voicestudio-electron-updater/ ← downloaded updates
~/.config/omnivoice/                          ← saved env file (cache location, HF token)
~/.cache/huggingface/                         ← model weights (shared HF cache — see caveat)
```

### Linux (AppImage / .deb)

```
~/.omnivoice/                                 ← app data (voices, projects, omnivoice.db, outputs, omnivoice.log)
~/.config/VoiceStudio/                        ← app folder, desktop logs, AND the managed Python env (runtime/project/.venv)
~/.cache/voicestudio-electron-updater/        ← downloaded updates
~/.config/omnivoice/                          ← saved env file (cache location, HF token)
~/.cache/huggingface/                         ← model weights (shared HF cache — see caveat)
```

`$XDG_CONFIG_HOME` / `$XDG_CACHE_HOME` replace `~/.config` / `~/.cache` when set.

### Windows

```
%APPDATA%\OmniVoice\                           ← app data (voices, projects, omnivoice.db, outputs, omnivoice.log)
%APPDATA%\VoiceStudio\                         ← app folder, desktop logs, AND the managed Python env (runtime\project\.venv)
%LOCALAPPDATA%\voicestudio-electron-updater\   ← downloaded updates
%USERPROFILE%\.config\omnivoice\               ← saved env file (cache location, HF token)
%LOCALAPPDATA%\OmniVoice\hf_cache\             ← model weights (VoiceStudio uses a short path here to dodge MAX_PATH)
```

### Left behind by a Tauri-era install

If you used VoiceStudio before the Electron desktop app, these may also exist;
the script removes them too:

```
macOS:   ~/Library/Application Support/com.debpalash.omnivoice-studio/, ~/Library/Logs/OmniVoice/,
         ~/Library/Logs/com.debpalash.omnivoice-studio/
Linux:   ~/.local/share/com.debpalash.omnivoice-studio/, ~/.local/state/VoiceStudio/
Windows: %LOCALAPPDATA%\com.debpalash.omnivoice-studio\, %LOCALAPPDATA%\OmniVoice\Logs\
```

On Windows, if `HF_HOME` isn't set, VoiceStudio redirects the model cache to
`%LOCALAPPDATA%\OmniVoice\hf_cache` (instead of `~/.cache/huggingface`) so deep
model paths don't hit the 260-character `MAX_PATH` limit.

### Custom / portable locations

- **Custom folders:** if you chose a custom data or model folder in setup (or
  set `OMNIVOICE_DATA_DIR` / `OMNIVOICE_CACHE_DIR` / `HF_HOME` /
  `HF_HUB_CACHE`), your data is there instead of the defaults above. A Python
  runtime you moved to another drive lives in the `VoiceStudio` folder you
  picked.
- **Portable mode:** everything lives in an `OmniVoiceStudio-Data/` folder next
  to the app binary — delete that one folder and you're done.
- **App-managed engine sidecars:** if VoiceStudio installed IndexTTS 2.5,
  CosyVoice, or another sidecar, its isolated venv lives under `engines/` in the
  app-data folder above and is removed with it. A user-managed IndexTTS checkout
  set via `OMNIVOICE_INDEXTTS_DIR` is preserved only when its resolved path is
  outside the app-data folder. Verify the resolved path before cleanup: anything
  inside the app-data folder is removed with it; retain or delete an external
  checkout separately.

> **Shared HF cache caveat:** `~/.cache/huggingface/` is the **standard Hugging
> Face cache**, shared by any tool that uses `huggingface_hub` (other ML apps,
> `transformers`, etc.). If you use other Hugging Face tools, deleting the whole
> folder removes *their* cached models too. To remove only VoiceStudio's models,
> delete the `models--*` subfolders you recognize under
> `~/.cache/huggingface/hub/`, or just let it be — it's only a cache and any
> tool re-downloads what it needs. The uninstaller script prints the cache size
> and asks about it separately for this reason.

## Remove the app itself

The steps above clear the **data**; removing the installed **app** is the
normal per-platform step:

- **macOS:** drag **VoiceStudio.app** from `/Applications` to the Trash.
- **Windows:** **Settings → Apps → Installed apps → VoiceStudio →
  Uninstall** (or via "Add or remove programs"). The non-elevated artifact
  appears as **VoiceStudio (Current User)** and can be removed by that user
  without administrator approval.
- **Linux (AppImage):** delete the `.AppImage` file. If you integrated it into
  your menu (e.g. with AppImageLauncher or a hand-written `.desktop` file),
  also remove `~/.local/share/applications/*voicestudio*.desktop` (or `*omnivoice*.desktop` from an older install) and any icon
  under `~/.local/share/icons/`.
- **Linux (.deb):** `sudo apt remove voicestudio-electron` (this removes the
  program; your data folders above are user data and are left in place —
  delete them with the script or by hand). An older Tauri-era package may also
  be installed: `sudo apt remove voicestudio`, or `omnivoice-studio` from before
  the rename — they are separate packages.

## Reinstalling later

Nothing above is required before reinstalling — a fresh install rebuilds the
Python env and re-downloads models on demand. Keep `~/Library/Application
Support/OmniVoice/` (macOS) / `~/.omnivoice/` (Linux) / `%APPDATA%\OmniVoice\`
(Windows) if you want to preserve your **voice profiles and projects** across a
reinstall; delete it too for a truly clean slate.
