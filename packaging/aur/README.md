# Arch Linux packages

`voicestudio-git` builds the Electron app, native desktop bridge and pinned
OmniVoice GGUF runtime from source. It follows the upstream `main` branch;
`pkgver()` reads the root app version and appends the commit count and hash.
It conflicts with the stable `voicestudio-bin` package.

Build on x86-64 Arch Linux as a regular user:

```sh
cd voicestudio-git
makepkg --syncdeps --cleanbuild
makepkg --printsrcinfo > .SRCINFO
```

The build needs network access for Git sources, locked JavaScript/Rust
dependencies and Electron. The desktop package contains no model weights or
preinstalled Python engine environment: first-run setup remains explicit and
uses the upstream locked dependency graph. The launcher disables in-app
application updates so the AUR helper and pacman own package updates.

Publish only the recipe and generated `.SRCINFO` to
`ssh://aur@aur.archlinux.org/voicestudio-git.git`; keep build outputs out of the
AUR Git repository. A new Git-package build picks up current `main` without a
new upstream release. Use an AUR helper's development-package update option to
check for commits between recipe updates.
