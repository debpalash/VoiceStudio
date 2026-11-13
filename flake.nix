{
  description = "VoiceStudio — Electron desktop shell, FastAPI/PyTorch backend, Rust dictation helper";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;

      # VoiceStudio publishes Linux desktop artifacts for x86_64 only
      # (docs/install/linux.md, "Requirements"). The Linux shells are offered
      # on aarch64 too, because building from source works there; Apple
      # Silicon gets a plain toolchain shell.
      linuxSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      devSystems = linuxSystems ++ [ "aarch64-darwin" ];
      buildSystems = [ "x86_64-linux" ];

      forDevSystems = f: lib.genAttrs devSystems (system: f nixpkgs.legacyPackages.${system});

      # ── Source ───────────────────────────────────────────────────────────
      # self is the flake's own tree. Stripping .git keeps the store copy small
      # and stops git metadata from churning the hash.
      src = lib.cleanSourceWith {
        src = lib.cleanSource self;
        # cleanSource already drops .git and result; node_modules and .venv are
        # gitignored but `nix build path:.` would otherwise copy them.
        filter =
          path: type:
          let
            name = baseNameOf (toString path);
          in
          name != "node_modules"
          && name != ".venv"
          && name != "result"
          && !(lib.hasPrefix "result-" name);
      };

      # Only what `bun install` reads: the root manifest and lockfile, each
      # workspace's package.json, the patches named in patchedDependencies and
      # any bun/npm config. The node-deps fixed-output derivation is built from
      # this, so ordinary source edits cannot reach its pinned hash. Both lists
      # are read from package.json, so a new workspace or patch is picked up
      # without touching this file.
      rootManifest = lib.importJSON ./package.json;
      nodeDepsSrc = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions (
          [
            ./package.json
            ./bun.lock
            (lib.fileset.maybeMissing ./bunfig.toml)
            (lib.fileset.maybeMissing ./.npmrc)
          ]
          ++ map (
            ws:
            assert lib.assertMsg (!lib.hasInfix "*" ws)
              "flake.nix: workspace glob \"${ws}\" in package.json; list workspaces explicitly.";
            ./. + "/${ws}/package.json"
          ) (rootManifest.workspaces or [ ])
          ++ map (patch: ./. + "/${patch}") (lib.attrValues (rootManifest.patchedDependencies or { }))
        );
      };

      # ── libxdo ───────────────────────────────────────────────────────────
      # enigo (a dependency of the Rust dictation helper) enables its default
      # `xdo` feature, whose src/linux/xdo.rs declares `#[link(name = "xdo")]`,
      # so linking the helper needs libxdo.so.
      #
      # No nixpkgs attribute provides it:
      #   * `pkgs.xdo` (baskerville/xdo 0.5.7) builds only the `xdo` binary and
      #     links the library in statically — there is no libxdo.so to link.
      #   * `pkgs.xdotool` ships the same sources and has an `installlib` target,
      #     but its install phase installs bin/ and man/ only.
      # So build just the library from xdotool's own pinned source, reusing
      # nixpkgs' hashes so this tracks upstream automatically.
      libxdo =
        pkgs:
        let
          xdotool = pkgs.xdotool;
        in
        pkgs.stdenv.mkDerivation {
          name = "libxdo";
          pname = "libxdo";

          inherit (xdotool) src version;

          nativeBuildInputs = [ pkgs.pkg-config pkgs.perl ];

          buildInputs = [
            pkgs.libx11
            pkgs.libxtst
            pkgs.xorgproto
            pkgs.libxi
            pkgs.libxinerama
            pkgs.libxkbcommon
            pkgs.libxext
          ];

          strictDeps = true;

          dontConfigure = true;

          # `installlib` is the upstream target that installs libxdo.so with the
          # versioned SONAME plus the unversioned symlink; `installheader`
          # installs xdo.h. Using them keeps the SONAME exactly what upstream
          # ships, which is what enigo's #[link(name = "xdo")] resolves against.
          makeFlags = [
            "installlib"
            "installheader"
            "PREFIX=$(out)"
          ];

          doCheck = false;

          meta = {
            description = "libxdo shared library (built from the xdotool sources)";
            homepage = "https://github.com/jordansissel/xdotool";
            license = pkgs.lib.licenses.bsd3;
            platforms = pkgs.lib.platforms.linux;
          };
        };

      # ── Toolchain ────────────────────────────────────────────────────────
      # Mirrors the native prerequisites in docs/install/linux.md.
      #
      # The helper links TWO C libraries, not zero:
      #   * libxdo   — enigo 0.3's default feature is ["xdo"], and its
      #                src/linux/xdo.rs carries #[link(name = "xdo")].
      #   * libwayland-client — arboard's wayland-data-control, via wayland-sys'
      #                pkg-config probe.
      # libxdo is provided by the derivation above; it belongs on the library
      # search path so cargo's linker finds -lxdo.
      toolchain =
        pkgs:
          with pkgs;
          [
            bun
            nodejs_22
            uv
            python311
            cargo
            rustc
            clippy
            rustfmt
            cmake
            ninja
            pkg-config
            clang
            gnumake
            binutils
            wayland
            openssl
            alsa-lib
            libx11
            libXtst
            libxkbcommon
            libGL
            nss
            nspr
            at-spi2-atk
            libdrm
            libgbm
            libxshmfence
            libpulseaudio
            libjack2
            dbus
            git
            curl
            ripgrep
            which
          ];

      devShell =
        pkgs:
        pkgs.mkShell {
          name = "voicestudio";

          nativeBuildInputs = toolchain pkgs;

          packages = with pkgs; [
            # ffmpeg is one of four tiers in
            # backend/services/ffmpeg_utils.py::find_ffmpeg; having it on PATH
            # avoids the ~142 MB first-run download from zackees/ffmpeg_bins.
            ffmpeg
            uv
            python311
          ];

          # uv resolves `.python-version` (3.11) against the interpreter already
          # in the shell instead of downloading python-build-standalone. The venv
          # still lands in the repo's gitignored .venv, so `bun run setup:api`
          # behaves exactly as it does on Debian/Fedora/Arch.
          UV_PYTHON_PREFERENCE = "only-system";

          # PyTorch's wheels ship a C++ runtime dependency they expect the host
          # to provide: importing torch, torchvision or torchaudio fails with
          # "libstdc++.so.6: cannot open shared object file" without it, and
          # NixOS has no /usr/lib to fall back on. libgomp comes from the same
          # GCC runtime and is needed by the same extensions.
          LD_LIBRARY_PATH = lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.gnumake
          ];

          # enigo's #[link(name = "xdo")] is resolved by the linker, which does
          # not consult pkg-config, so the search path has to be set explicitly
          # for `cargo build` of the dictation helper to succeed here.
          NIX_LDFLAGS = "-L${libxdo pkgs}/lib -rpath ${libxdo pkgs}/lib";

          # `bun run dev` would otherwise launch the generic Electron binary
          # the npm package downloads, which needs /lib64/ld-linux-*.so and
          # FHS library paths that NixOS lacks. Point the npm package at
          # nixpkgs' patched build of the same major instead (electron's
          # index.js honours ELECTRON_OVERRIDE_DIST_PATH) and skip the
          # download it would never use.
          ELECTRON_OVERRIDE_DIST_PATH = "${pkgs.electron_44-bin}/libexec/electron";
          ELECTRON_SKIP_BINARY_DOWNLOAD = "1";

          shellHook = ''
            echo "VoiceStudio dev shell"
            echo "  1. bun install"
            echo "  2. bun run setup:api    # creates .venv via uv"
            echo "  3. bun run dev          # Electron + supervised backend"
            echo ""
            echo "Installer production (bun run dist) is NOT supported here: electron-builder"
            echo "downloads its own tool binaries from GitHub at build time. For a Nix"
            echo "build of the unpacked Linux app use: nix build"
          '';
        };

      # ── FHS environment ──────────────────────────────────────────────────
      # The fallback for NixOS hosts without nix-ld: an FHS sandbox in which
      # the npm-downloaded Electron, uv's managed CPython and arbitrary
      # manylinux wheels find /lib64 and /usr/lib exactly as on other
      # distributions. Kept as `.#fhs` so existing instructions keep working.
      fhsLibs =
        pkgs: with pkgs; [
          alsa-lib
          at-spi2-atk
          at-spi2-core
          atk
          cairo
          cups
          dbus
          expat
          glib
          gtk3
          libGL
          libdrm
          libgbm
          libpulseaudio
          libx11
          libxkbcommon
          libxcomposite
          libxdamage
          libxext
          libxfixes
          libxrandr
          libxcb
          libxtst
          mesa
          nspr
          nss
          openssl
          pango
          systemd
          vulkan-loader
          wayland
          xdotool
          zlib
        ];

      fhsEnv =
        pkgs:
        pkgs.buildFHSEnv {
          name = "voicestudio-fhs";
          targetPkgs =
            p:
            (with p; [
              bun
              nodejs_22
              uv
              rustc
              cargo
              pkg-config
              ffmpeg
              git
              gcc
              gnumake
              binutils
              zsync
            ])
            ++ fhsLibs p
            # xdotool ships no shared libxdo; the helper links it.
            ++ [ (libxdo p) ];
          # uv must use its own managed CPython, not a Nix store Python.
          # No OMNIVOICE_PORT here: an explicit port disables the app's
          # fallback when the default backend port is reserved.
          profile = ''
            export UV_PYTHON_PREFERENCE="only-managed"
          '';
          # Headers and .pc files for native builds inside the FHS env.
          extraOutputsToInstall = [ "dev" ];
          runScript = "bash";
        };

      # macOS needs none of the Linux loader work: the downloaded Electron
      # and uv's managed CPython run as they do outside Nix.
      darwinShell =
        pkgs:
        pkgs.mkShell {
          name = "voicestudio";
          packages = with pkgs; [
            bun
            nodejs_22
            uv
            rustc
            cargo
            pkg-config
            ffmpeg
            git
            apple-sdk
          ];
        };

      # ── node dependencies ────────────────────────────────────────────────
      # Fixed-output derivations are how Nix expresses "reach the network here,
      # and only here, then verify the result by hash". The sandbox itself stays
      # hermetic, so no --impure flag is needed anywhere.
      #
      # First build prints the correct hash; paste it over the outputHash below.
      nodeDeps =
        { pkgs }:
        pkgs.runCommand "voicestudio-node-deps"
          {
            nativeBuildInputs = [
              pkgs.bun
              pkgs.nodejs_22
            ];
            outputHashMode = "recursive";
            outputHash = "sha256-bqxnoXhbM2HK+xW+pBOy71WjSuAhsbjbr429b+uT6WU=";
            sourceProvenance = null;
          } ''
          export HOME=$TMPDIR
          export BUN_INSTALL="$TMPDIR/.bun"
          export PATH="$BUN_INSTALL/bin:$PATH"

          # Skip the electron postinstall: this build uses nixpkgs' electron
          # runtime, and the download is both redundant and a network call we
          # would rather not make.
          export ELECTRON_SKIP_BINARY_DOWNLOAD=1

          # Install into ./source from the dependency manifests alone, then
          # drop what bun does not write deterministically:
          #   * the .cache tree (timestamps and generated binaries);
          #   * the per-package .bin directories inside the isolated store
          #     (node_modules/.bun/<pkg>/node_modules/.bin). bun links them
          #     concurrently, and for peer-dependency cycles (vite-plus and
          #     oxlint/oxfmt, vitest and @vitest/ui) which links exist varies
          #     from run to run on identical input, which flipped this
          #     derivation's hash between PRs. Nothing here runs a dependency
          #     through them; every consumer calls entrypoints by path.
          # The top-level node_modules/.bin trees are deterministic and kept.
          install_deps() {
            cp -r ${nodeDepsSrc} ./source
            chmod -R u+w ./source
            (cd ./source && bun install --frozen-lockfile)
            rm -rf source/node_modules/.cache source/electron/node_modules/.cache
            find source/node_modules/.bun -type d -name .bin -prune -exec rm -rf {} +
          }

          # Install twice and require identical trees, so any new
          # nondeterminism fails here with the differing paths instead of as
          # an unexplained hash mismatch on some later, unrelated PR.
          install_deps
          mv source first
          install_deps
          if ! diff -r --no-dereference first source > $TMPDIR/determinism.diff 2>&1; then
            echo "bun install is not deterministic; differing paths:" >&2
            head -50 $TMPDIR/determinism.diff >&2
            exit 1
          fi
          rm -rf first
          cd ./source

          # bun writes .bin entries as absolute symlinks into the build tree.
          # Copy, then re-point any absolute symlink so the result is
          # relocatable into a different store path.
          mkdir -p $out/electron
          # Preserve the directory NAMES bun used. Its isolated layout writes
          # relative symlinks that walk out of electron/node_modules and back
          # into the root node_modules, so renaming either tree breaks them.
          cp -r node_modules $out/node_modules
          cp -r electron/node_modules $out/electron/node_modules

          chmod -R u+w $out

          # bun's .bin shims are JavaScript entrypoints whose first line is
          # `#!/usr/bin/env node`. /usr/bin/env exists in neither a Nix sandbox
          # nor on NixOS, so the kernel would reject them with "bad
          # interpreter". Two fixes are available and the shebang must not name
          # an interpreter by store path, because a fixed-output derivation may
          # not reference store paths:
          #
          #   * drop the shebang  — correct only when the file is run as
          #     `node <file>`, which is how every consumer below invokes them.
          #   * rewrite to an absolute path — illegal here, and it would also
          #     bake an interpreter path into a content-addressed output.
          #
          # So strip the first line and leave the body byte-exact.
          for tree in $out/node_modules $out/electron/node_modules; do
            if [ -d "$tree/.bin" ]; then
              for shim in "$tree"/.bin/*; do
                [ -f "$shim" ] || continue
                case $(head -c 2 "$shim") in
                  '#!')
                    tail -n +2 "$shim" > "$shim.tmp"
                    chmod +x "$shim.tmp"
                    mv "$shim.tmp" "$shim"
                    ;;
                esac
              done
            fi
          done

          # bun records absolute symlinks into the build tree; make any that
          # survived the copy relative to $out so it stays relocatable. The
          # source tree's node_modules and electron/node_modules sit at the
          # same relative paths under $out, so map the target there and link
          # to it relative to the link's own directory (-s keeps the logical
          # path bun's layout relies on instead of resolving through links).
          prefix=$PWD
          find $out -type l | while read -r link; do
            target=$(readlink "$link")
            case "$target" in
              "$prefix"/*)
                mapped="$out/''${target#"$prefix"/}"
                ln -sfn "$(realpath -ms --relative-to="$(dirname "$link")" "$mapped")" "$link"
                if [ ! -e "$link" ]; then
                  echo "rewritten symlink does not resolve: $link -> $target" >&2
                  exit 1
                fi
                ;;
            esac
          done

          # Every link must stay inside the two dependency trees. A link to a
          # workspace package would resolve to its manifest-only stub here
          # rather than to the checkout, so fail instead of shipping it.
          find $out -type l | while read -r link; do
            case "$(realpath -m "$link")" in
              "$out"/node_modules | "$out"/node_modules/* | "$out"/electron/node_modules | "$out"/electron/node_modules/*) ;;
              *)
                echo "symlink leaves the dependency trees: $link -> $(readlink "$link")" >&2
                exit 1
                ;;
            esac
          done
        '';

      # ── omnivoice-tts (OmniVoice GGUF runtime) ───────────────────────────
      # The installers ship the runtime build-omnivoice-tts.yml builds with
      # scripts/build-omnivoice-tts.sh as resources/bin/, and the backend
      # refuses it unless every file matches bin/checksums.sha256. Build the
      # same pinned omnivoice.cpp the same way (Vulkan + CPU backends loaded at
      # runtime) and write the same manifest.
      #
      # The rev must equal quant_map.json's runtime_commit_sha (asserted
      # below); the ggml rev is that commit's submodule gitlink. When the pin
      # moves, update both revs and both hashes.
      omnivoiceRuntimeRev = "040c8b344d8c670ce1475194751d119b5ef82c78";
      omnivoiceGgmlRev = "d5152bf4348453068aa8de0d479caef40a613b9d";
      pinnedRuntimeRev =
        (lib.importJSON ./backend/engines/omnivoice_gguf/quant_map.json)._meta.runtime_commit_sha;

      omnivoiceTts =
        pkgs:
        assert lib.assertMsg (omnivoiceRuntimeRev == pinnedRuntimeRev) ''
          flake.nix pins omnivoice.cpp ${omnivoiceRuntimeRev}, but
          backend/engines/omnivoice_gguf/quant_map.json pins ${pinnedRuntimeRev}.
          Update omnivoiceRuntimeRev, omnivoiceGgmlRev and both hashes.
        '';
        let
          ggml = pkgs.fetchFromGitHub {
            owner = "ServeurpersoCom";
            repo = "ggml";
            rev = omnivoiceGgmlRev;
            hash = "sha256-qRyucqwOppbd0ZjjrX48Bbdh9yyljYHuUwk3sQFXLnU=";
          };
        in
        pkgs.stdenv.mkDerivation {
          pname = "omnivoice-tts";
          version = "0-unstable-${builtins.substring 0 7 omnivoiceRuntimeRev}";

          src = pkgs.fetchFromGitHub {
            owner = "ServeurpersoCom";
            repo = "omnivoice.cpp";
            rev = omnivoiceRuntimeRev;
            hash = "sha256-9XE5WZo55hB4HCXaYV1FknOy0A0yVKqurgfVfaE1GhI=";
          };

          # GitHub archives leave the ggml submodule as an empty directory.
          postUnpack = ''
            rmdir "$sourceRoot/ggml"
            cp -r ${ggml} "$sourceRoot/ggml"
            chmod -R u+w "$sourceRoot/ggml"
          '';

          nativeBuildInputs = [
            pkgs.cmake
            (lib.getBin pkgs.shaderc) # glslc
          ];
          buildInputs = [
            pkgs.vulkan-headers
            pkgs.vulkan-loader
            pkgs.spirv-headers
          ];

          # scripts/build-omnivoice-tts.sh's Linux Vulkan configuration.
          dontUseCmakeConfigure = true;
          buildPhase = ''
            runHook preBuild
            cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON \
              -DGGML_BACKEND_DL=ON -DBUILD_SHARED_LIBS=ON \
              -DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON
            cmake --build build --config Release -j "$NIX_BUILD_CORES"
            runHook postBuild
          '';

          # Flat layout, as the build script copies it: the binary next to every
          # libggml* it loads, so ggml finds its backends beside the executable.
          installPhase = ''
            runHook preInstall
            mkdir -p "$out"
            cp -v build/omnivoice-tts "$out/omnivoice-tts-linux-x86_64"
            cp -v build/tts-server "$out/tts-server-linux-x86_64"
            find build -name 'libggml*.so*' -type f -exec cp -v {} "$out/" \;
            find build -name 'libggml*.so*' -type l -exec cp -vL {} "$out/" \;
            for file in "$out"/*; do
              isELF "$file" || continue
              rpath=$(patchelf --print-rpath "$file" | tr ':' '\n' |
                { grep -v -e "^$NIX_BUILD_TOP" -e '^$' || true; } | paste -sd: -)
              patchelf --set-rpath "\$ORIGIN''${rpath:+:$rpath}" "$file"
            done
            runHook postInstall
          '';

          # The manifest must describe the bytes that ship, so it is written
          # after fixup; shrinking the RPATH would drop $ORIGIN.
          dontPatchELF = true;
          postFixup = ''
            (cd "$out" && sha256sum -- * | sort -k2) > "$TMPDIR/checksums.sha256"
            mv "$TMPDIR/checksums.sha256" "$out/checksums.sha256"
          '';

          meta = {
            description = "OmniVoice GGUF inference runtime (omnivoice.cpp)";
            homepage = "https://github.com/ServeurpersoCom/omnivoice.cpp";
            platforms = [ "x86_64-linux" ];
          };
        };

      # ── Package ──────────────────────────────────────────────────────────
      mkPackage =
        pkgs:
        let
          electron = pkgs.electron_44-bin;

          # electron/package.json pins electron to ^44.3.0; the nixpkgs pinned by
          # flake.lock ships electron_44-bin 44.5.1, which satisfies that range
          # and is the same upstream binary CI packages. Its dist is already
          # patchelf'd (generic.nix sets the interpreter and an RPATH), so the
          # copy below needs no loader work — only a rename, because Electron
          # derives app.isPackaged from the executable's basename.
          electronDist = electron + "/libexec/electron";

          nodeDepsDev = nodeDeps { inherit pkgs; };

          # The one place the build touches the network: a fixed-output
          # derivation that installs the workspace's dependencies from bun.lock.
          # Everything after this point is hermetic.
          appBundle = pkgs.runCommand "voicestudio-app-bundle"
            {
              nativeBuildInputs = with pkgs; [ nodejs_22 ];
            } ''
            cp -r ${src} ./source
            chmod -R u+w ./source
            sourceRoot="$PWD/source"
            cd ./source

            export ELECTRON_SKIP_BINARY_DOWNLOAD=1
            ln -s ${nodeDepsDev}/node_modules node_modules
            ln -s ${nodeDepsDev}/electron/node_modules electron/node_modules

            # bun's .bin shims have their shebang stripped in the fixed-output
            # derivation (a FOD may not name an interpreter by store path, and
            # /usr/bin/env does not exist here), so call the entrypoints
            # through node explicitly instead of relying on exec.
            electronVite="$PWD/electron/node_modules/electron-vite/bin/electron-vite.js"
            viteCli="$PWD/electron/node_modules/vite/bin/vite.js"

            # The locale checks the repo's own build script runs first.
            node electron/tests/locale-encoding.mjs
            node electron/tests/locale-source-keys.mjs
            node electron/tests/locale-coverage.mjs

            # Both vite configs live in electron/ and resolve their own imports
            # (electron-vite, vite, plugins) relative to that directory, so run
            # from inside electron/ the way `bun run --cwd electron build` does.
            cd electron

            # electron/out (main, preload, renderer).
            node "$electronVite" build --config electron.vite.config.ts

            # ../frontend/dist, the browser UI the backend serves to LAN devices.
            node "$viteCli" build --config vite.web.config.ts

            cd "$sourceRoot"
            mkdir -p "$out/electron" "$out/frontend"
            cp -r electron/out "$out/electron/out"
            cp -r frontend/dist "$out/frontend/dist"
            cp electron/package.json "$out/electron/package.json"

            # Vite/Rolldown records source module paths as `//#region`
            # comments. With node_modules coming from a store path those
            # comments would make the output reference the whole
            # voicestudio-node-deps derivation, keeping a duplicate
            # node_modules in the runtime closure. The comments are inert, so
            # drop the store path and keep the bundle self-contained.
            find "$out/electron/out" "$out/frontend/dist" -type f \
              \( -name '*.js' -o -name '*.mjs' -o -name '*.cjs' \) -print0 |
              xargs -0 -r sed -i "s|${nodeDepsDev}||g"

            # Ship only the modules the main process loads at runtime; the
            # renderer is bundled and the dev toolchain is build-only.
            node "$sourceRoot/scripts/prune-runtime-node-modules.mjs" \
              "$sourceRoot" "$out/runtime-node-modules"

            # electron/package.json carries the placeholder 0.0.0-electron;
            # electron-builder substitutes the real version through
            # extraMetadata (electron-builder.config.mjs), and
            # tests/test_app_version.py pins the root package.json as the single
            # source of truth. Do the same substitution here.
            # The rest of extraMetadata matters too: Electron names the
            # userData folder after productName (the AppImage's
            # ~/.config/VoiceStudio) and takes the Linux app id from
            # desktopName.
            node --input-type=module -e '
              import fs from "node:fs";
              import { pathToFileURL } from "node:url";
              const [file, root] = process.argv.slice(1);
              const load = (rel) => import(pathToFileURL(root + "/" + rel).href);
              const { PRODUCT_NAME } = await load("config/app-config.mjs");
              const { DISTRIBUTION_TARGET, DISTRIBUTION_PROFILE } = await load("config/distributions.mjs");
              const pkg = JSON.parse(fs.readFileSync(file, "utf8"));
              Object.assign(pkg, {
                version: JSON.parse(fs.readFileSync(root + "/package.json", "utf8")).version,
                productName: PRODUCT_NAME,
                desktopName: PRODUCT_NAME,
                voicestudioDistribution: DISTRIBUTION_TARGET,
                voicestudioUpdateOwner: DISTRIBUTION_PROFILE.applicationUpdates,
              });
              fs.writeFileSync(file, JSON.stringify(pkg, null, 2) + "\n");
            ' "$out/electron/package.json" "$sourceRoot"
          '';

          # The same cargo build electron-builder's afterPack hook performs,
          # with crates resolved from the store instead of crates.io.
          libXdo = libxdo pkgs;

          # electron-builder.config.mjs sets syncDesktopName, keeping the
          # window identity and the desktop entry on one name. The helper reads
          # XDG_DATA_DIRS and never rewrites a system entry
          # (native/desktop-bridge/src/wayland_shortcut_core.rs), so installing
          # this to $out/share/applications is enough for dictation to bind.
          desktopEntry = pkgs.makeDesktopItem {
            name = "VoiceStudio";
            desktopName = "VoiceStudio";
            # makeDesktopItem is its own derivation, so a placeholder here
            # would name the desktop item's output, which has no bin/. The
            # install phase rewrites this to the package's absolute launcher.
            exec = "voicestudio";
            icon = "VoiceStudio";
            comment = "Local voice cloning studio";
            categories = [ "AudioVideo" "Audio" ];
          };

          desktopBridge = pkgs.rustPlatform.buildRustPackage {
            pname = "voicestudio-desktop-bridge";
            version = "0.0.0";

            src = lib.cleanSource ./native/desktop-bridge;

            # Every crate in this lockfile comes from the crates.io registry, and each
            # carries a checksum, so importCargoLock resolves them without any
            # outputHashes (that option only applies to git dependencies).
            cargoLock.lockFile = ./native/desktop-bridge/Cargo.lock;

            # nixpkgs' cargo setup hook exports AWS_LC_SYS_USE_SYSTEM=1 for
            # every buildRustPackage, demanding a system AWS-LC. The matching
            # per-crate override (default-crate-overrides.nix) only feeds the
            # legacy buildRustCrate path, so buildRustPackage gets the "use the
            # system library" flag without the buildInputs that would provide
            # it, and aws-lc-sys (pulled in by reqwest's rustls feature) panics
            # in its build script. This tree has no other -sys crate that the
            # hook helps, so let aws-lc-sys build its vendored copy instead.
            dontCargoUseSystemLibs = true;

            nativeBuildInputs = [
              pkgs.pkg-config
              pkgs.cmake
            ];

            # enigo links libxdo via #[link(name = "xdo")], which the linker
            # resolves directly rather than through pkg-config.
            NIX_LDFLAGS = "-L${libxdo pkgs}/lib -rpath ${libxdo pkgs}/lib";

            buildInputs = [
              pkgs.wayland
              libXdo
            ];

            meta = {
              description = "VoiceStudio dictation and global-shortcut helper";
              platforms = pkgs.lib.platforms.linux;
            };
          };

        in
        pkgs.stdenv.mkDerivation {
          name = "voicestudio";
          inherit src;

          nativeBuildInputs = [
            # The shell wrapper, for its --run hook (the NixOS-only GCC
            # runtime path below); the binary wrapper has no equivalent.
            pkgs.makeShellWrapper
            # nixpkgs' electron_44-bin launcher passes wrapGAppsHook3's
            # environment (GSettings schemas, GIO modules, pixbuf loaders,
            # XDG_DATA_DIRS); without it GTK file dialogs can abort the app.
            pkgs.wrapGAppsHook3
            pkgs.patchelf
            pkgs.installShellFiles
            pkgs.desktop-file-utils
            # Deliberately no autoPatchelfHook: the Electron runtime arrives
            # already patched from nixpkgs, and the hook would additionally try
            # to rewrite the JavaScript .bin shims in node_modules, which are
            # not ELF ("cannot find section .dynamic").
            pkgs.ffmpeg
            pkgs.ripgrep
            pkgs.uv
            pkgs.git
          ];

          # The Electron runtime from nixpkgs is already patchelf'd with the
          # correct interpreter and RPATH, and the dictation helper is patched
          # by its own buildRustPackage. Re-running autoPatchelf over the
          # unpacked Electron dist would only rewrite identical values.
          dontPatchELF = true;

          # Same reasoning for stdenv's shebang pass: node_modules/.bin holds
          # JavaScript entrypoints, and rewriting their (already stripped)
          # shebangs only produces noise on files that are never exec'd here.
          dontPatchShebangs = true;

          # The strip pass likewise fails on the JavaScript entrypoints in
          # node_modules ("patchelf: cannot find section .dynamic"). Debug
          # symbols in the shipped app are not worth a failing build.
          dontStrip = true;

          # stdenv's fixupPhase walks $out and runs patchelf/strip over
          # everything, including the JavaScript entrypoints in node_modules,
          # which are not ELF. Nothing here needs fixing: the Electron runtime
          # arrives patched from nixpkgs, the dictation helper is patched by
          # its own buildRustPackage, and the wrapper created below is a shell
          # script. mimeinfo.cache is stripped separately.
          dontFixup = true;

          # What electron_44-bin's own wrapper builds its GApps arguments
          # from; gsettings-desktop-schemas supplies the schemas GTK reads.
          buildInputs = [
            pkgs.glib
            pkgs.gtk3
            pkgs.gtk4
            pkgs.gsettings-desktop-schemas
          ];
          # The wrapper is written by hand below (fixup is off), with the
          # arguments the hook collects.
          dontWrapGApps = true;

          dontConfigure = true;
          dontBuild = true;

          installPhase = ''
            runHook preInstall

            staging="$TMPDIR/app"
            resources="$staging/resources"
            mkdir -p "$staging"

            # ── Electron runtime ───────────────────────────────────────────
            # Electron derives resources/ and locales/ from its own location,
            # and nixpkgs' wrapper resolves the binary relative to itself, so
            # the layout has to mirror electron_44-bin exactly:
            #
            #   $out/libexec/electron/…   the dist (binary, locales, .so files)
            #   $out/bin/voicestudio      the wrapper
            #
            # Everything is assembled in the build directory and copied into
            # $out once: paths under $out are read-only for anything but
            # mkdir/install, so renaming entries in place is not possible.
            mkdir -p "$staging/libexec"
            cp -r ${electronDist} "$staging/libexec/electron"
            chmod -R u+w "$staging"

            resources="$staging/libexec/electron/resources"

            # ── App payload ───────────────────────────────────────────────
            # resources/app mirrors what electron-builder packs into app.asar:
            # out/ + package.json. electron-vite's externalizeDepsPlugin leaves
            # the main process importing its dependencies at runtime, so the
            # runtime node_modules ships as a sibling of app/ (Node resolves it
            # by walking up from app/out/main) rather than inside an asar.
            mkdir -p "$resources/app"
            cp -r ${appBundle}/electron/. "$resources/app/"

            # appBundle already reduced bun's isolated layout to the modules
            # the main process loads; copy it verbatim so the relative symlinks
            # into .bun stay intact.
            cp -a ${appBundle}/runtime-node-modules "$resources/node_modules"

            # ── extraResources (electron-builder.config.mjs) ──────────────
            mkdir -p "$resources/brand"
            install -Dm644 electron/build/icons/icon.png "$resources/brand/icon.png"
            install -Dm644 electron/build/icons/icon.ico "$resources/brand/icon.ico"
            install -Dm644 electron/build/icons/32x32.png "$resources/brand/32x32.png"
            install -Dm644 electron/build/icons/tray-recording.png "$resources/brand/tray-recording.png"
            install -Dm644 electron/build/icons/icon-macos.png "$resources/brand/icon-macos.png"
            install -Dm644 electron/build/icons/trayTemplate.png "$resources/brand/trayTemplate.png"
            install -Dm644 electron/build/icons/trayRecordingTemplate.png "$resources/brand/trayRecordingTemplate.png"

            # The OmniVoice GGUF runtime with its checksum manifest.
            mkdir -p "$resources/bin"
            cp ${omnivoiceTts pkgs}/* "$resources/bin/"

            cp -r backend "$resources/backend"
            cp -r omnivoice "$resources/omnivoice"
            mkdir -p "$resources/frontend"
            cp -r ${appBundle}/frontend/dist "$resources/frontend/dist"

            install -Dm644 pyproject.toml "$resources/pyproject.toml"
            install -Dm644 uv.lock "$resources/uv.lock"
            install -Dm644 README.md "$resources/README.md"
            install -Dm644 LICENSE "$resources/LICENSE"
            install -Dm644 LICENSE-NOTICE.md "$resources/LICENSE-NOTICE.md"
            install -Dm644 electron/T3CODE-LICENSE.txt "$resources/electron/T3CODE-LICENSE.txt"
            install -Dm644 config/app.json "$resources/config/app.json"
            install -Dm644 config/analytics.json "$resources/config/analytics.json"
            # stageRuntimeSources (runtime-project.ts SOURCES) copies these on
            # every platform and fails setup when one is missing.
            install -Dm644 scripts/windows-rocm-recipe.json "$resources/scripts/windows-rocm-recipe.json"
            install -Dm644 scripts/smoke_windows_rocm.py "$resources/scripts/smoke_windows_rocm.py"

            # runtime-project.ts looks for <resources>/tools/uv before falling
            # back to a download, so ship nixpkgs' uv and skip the installer
            # fetch on first run.
            install -Dm755 ${pkgs.uv}/bin/uv "$resources/tools/uv"

            # backend.ts spawns this from resources/native.
            install -Dm755 ${desktopBridge}/bin/voicestudio-desktop-bridge \
              "$resources/native/voicestudio-desktop-bridge"

            find "$resources" -name '__pycache__' -type d -prune -exec rm -rf {} +
            find "$resources" -name '*.pyc' -delete

            # ── Desktop integration ───────────────────────────────────────
            install -Dm644 ${desktopEntry}/share/applications/VoiceStudio.desktop \
              "$out/share/applications/VoiceStudio.desktop"
            substituteInPlace "$out/share/applications/VoiceStudio.desktop" \
              --replace-fail "Exec=voicestudio" "Exec=$out/bin/voicestudio"

            # icon.png is 512x512; ship it at the sizes desktops actually ask
            # for rather than re-encoding, which would need imagemagick.
            for size in 32 48 64 128 256 512; do
              install -Dm644 electron/build/icons/icon.png \
                "$out/share/icons/hicolor/$size"x"$size"/apps/VoiceStudio.png
            done

            # ── Wrapper ───────────────────────────────────────────────────
            # The wrapper must exec OUR dist, not electron_44-bin's.
            # electron_44-bin/bin/electron is a makeCWrapper stub with its
            # exec target baked in as an absolute store path, so copying it
            # here silently launches nixpkgs' dist, whose resources/ holds only
            # default_app.asar — Electron then pops the "electron <path-to-app>"
            # error instead of starting the app.
            #
            # Wrapping $out/libexec/electron/electron instead also makes Electron
            # resolve resources/ from its own location, which is what puts our
            # app at resources/app and gives the renderer the process.resourcesPath
            # that backend.ts and the native helper are addressed by. No app
            # argument is needed, and none is wanted: passing one would leave
            # resourcesPath pointing back at the wrong dist.
            #
            # Electron itself needs no LD_LIBRARY_PATH: nixpkgs patchelf'd the
            # binary with an RPATH (see the patchelf call below). The backend
            # it spawns does: PyTorch's wheels load libstdc++/libgomp, which
            # NixOS has no /usr/lib to provide. Only add the GCC runtime on
            # NixOS: on other distributions the host's own libstdc++ serves the
            # uv-managed Python, and a newer Nix one could demand glibc symbols
            # the host lacks.
            #
            # The store path is read-only, so Nix, not the in-app updater,
            # updates this package (same as the AUR launcher).
            #
            # This runs in installPhase because dontFixup skips the fixup phase
            # that would normally host it.
            mkdir -p "$out/bin"
            cp -r "$staging/libexec" "$out/libexec"

            # Electron derives app.isPackaged from the executable's BASENAME and
            # nothing else (App::IsPackaged in
            # shell/browser/api/electron_api_app.cc):
            #
            #   return base_name != FILE_PATH_LITERAL("electron");
            #
            # so a dist whose binary is still called "electron" reports itself
            # unpackaged however complete resources/ is. backend.ts then takes
            # its development branch and demands a repo checkout with a .venv
            # (#resolveSpawnPlan), which cannot work: resources/ lives in a
            # read-only store path and the runtime has to be installed under
            # app.getPath('userData'). electron-builder renames the binary for
            # the same reason; do it here.
            mv "$out/libexec/electron/electron" "$out/libexec/electron/voicestudio"

            # The copied binary still carries nixpkgs' RUNPATH, whose last entry
            # is electron_44-bin's own dist. Point it at ours so the bundled
            # .so files resolve inside this output instead of another package's
            # store path, and so the package is self-contained.
            patchelf --set-rpath \
              "$(
                patchelf --print-rpath "$out/libexec/electron/voicestudio" |
                  tr ':' '\n' |
                  grep -v -e '/libexec/electron$' -e '^$' |
                  tr '\n' ':'
              )$out/libexec/electron" \
              "$out/libexec/electron/voicestudio"
            patchelf --set-rpath \
              "$(patchelf --print-rpath "$out/libexec/electron/chrome_crashpad_handler" |
                tr ':' '\n' |
                grep -v -e '/libexec/electron$' -e '^$' |
                tr '\n' ':'
              )$out/libexec/electron" \
              "$out/libexec/electron/chrome_crashpad_handler"

            # dontFixup skips the preFixup hook that normally collects these.
            gappsWrapperArgsHook
            makeShellWrapper "$out/libexec/electron/voicestudio" "$out/bin/voicestudio" \
              "''${gappsWrapperArgs[@]}" \
              --prefix PATH : ${lib.makeBinPath [
                pkgs.ffmpeg
                pkgs.ripgrep
                pkgs.uv
                pkgs.git
                pkgs.which
              ]} \
              --set-default VOICESTUDIO_DISABLE_UPDATER 1 \
              --run 'if [ -e /etc/NIXOS ]; then export LD_LIBRARY_PATH="${
                lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ]
              }''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; fi'

            runHook postInstall
          '';

          meta = {
            description = "VoiceStudio — local voice cloning studio";
            longDescription = ''
              The Electron desktop shell for VoiceStudio. The Python backend is
              bootstrapped with uv into the user's data directory on first run —
              the same behaviour as the published AppImage — so no Python
              environment is stored in the Nix store.
            '';
            homepage = "https://voicestudio.sh";
            license = lib.licenses.agpl3Only;
            platforms = pkgs.lib.platforms.linux;
            mainProgram = "voicestudio";
          };
        };
    in
    {
      devShells = forDevSystems (
        pkgs:
        let
          isLinux = pkgs.stdenv.hostPlatform.isLinux;
        in
        {
          default = if isLinux then devShell pkgs else darwinShell pkgs;

          # Backend-only: no Electron runtime, no Rust. bun + Node run the
          # setup and supervisor scripts the hint below names.
          backend = pkgs.mkShell (
            {
              name = "voicestudio-backend";
              packages = with pkgs; [
                bun
                nodejs_22
                python311
                uv
                ffmpeg
                git
                curl
                ripgrep
                which
              ];
              UV_PYTHON_PREFERENCE = "only-system";
              shellHook = ''
                echo "VoiceStudio backend shell"
                echo "  bun run setup:api && bun run dev:api"
              '';
            }
            // lib.optionalAttrs isLinux {
              # See the default shell: the PyTorch wheels need the GCC runtime.
              LD_LIBRARY_PATH = lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
            }
          );
        }
        // lib.optionalAttrs isLinux {
          fhs = (fhsEnv pkgs).env;
        }
      );

      packages = lib.genAttrs linuxSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          fhs = fhsEnv pkgs;
        }
        // lib.optionalAttrs (lib.elem system buildSystems) {
          default = mkPackage pkgs;
        }
      );

      # Guards for the ways this flake could silently regress: a helper that
      # no longer resolves its shared libraries (the libxdo case), a package
      # missing a resource the backend or updater expects at runtime, and a
      # launcher that starts Electron instead of this app.
      checks = lib.genAttrs buildSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          app = mkPackage pkgs;
          bridge = app + "/libexec/electron/resources/native/voicestudio-desktop-bridge";
          runtime = app + "/libexec/electron/resources/bin/omnivoice-tts-linux-x86_64";
          nodeDepsDev = nodeDeps { inherit pkgs; };
        in
        {
          # Every NEEDED entry must resolve inside the Nix store. A missing
          # libxdo surfaces here as "not found" rather than as a runtime
          # failure in the packaged app.
          helperLinks = pkgs.runCommand "voicestudio-check-helper-links"
            {
              # ldd ships in glibc's bin output, not binutils.
              nativeBuildInputs = [ pkgs.glibc.bin ];
            } ''
            missing=$(ldd ${bridge} 2>/dev/null | grep "not found" || true)
            if [ -n "$missing" ]; then
              echo "voicestudio-desktop-bridge has unresolved libraries:"
              echo "$missing"
              exit 1
            fi
            ldd ${bridge} | grep -q "libxdo" || {
              echo "expected the helper to link libxdo (enigo's default xdo feature)"
              exit 1
            }
            # The GGUF runtime resolves its libggml* through $ORIGIN.
            missing=$(ldd ${runtime} 2>/dev/null | grep "not found" || true)
            if [ -n "$missing" ]; then
              echo "omnivoice-tts has unresolved libraries:"
              echo "$missing"
              exit 1
            fi
            touch $out
          '';

          # The resource contract electron/tests/packaging-contract.mjs asserts,
          # re-checked against the built output.
          packagedResources = pkgs.runCommand "voicestudio-check-resources"
            {
              nativeBuildInputs = [ pkgs.nodejs_22 ];
            } ''
            resources=${app}/libexec/electron/resources
            for path in \
              app/out/main/index.js \
              app/out/preload/index.mjs \
              app/out/renderer/index.html \
              app/out/renderer/early-error-capture.js \
              backend/main.py \
              omnivoice \
              frontend/dist/index.html \
              pyproject.toml \
              uv.lock \
              README.md \
              LICENSE \
              LICENSE-NOTICE.md \
              electron/T3CODE-LICENSE.txt \
              config/app.json \
              config/analytics.json \
              scripts/windows-rocm-recipe.json \
              scripts/smoke_windows_rocm.py \
              brand/icon.png \
              brand/icon.ico \
              brand/32x32.png \
              brand/tray-recording.png \
              brand/icon-macos.png \
              brand/trayTemplate.png \
              brand/trayRecordingTemplate.png \
              bin/omnivoice-tts-linux-x86_64 \
              bin/tts-server-linux-x86_64 \
              bin/checksums.sha256 \
              tools/uv \
              native/voicestudio-desktop-bridge \
              node_modules/@electron-toolkit/utils \
              node_modules/electron-updater \
              node_modules/proxy-agent \
              node_modules/word-extractor
            do
              if [ ! -e "$resources/$path" ]; then
                echo "missing packaged resource: $path"
                exit 1
              fi
            done

            # electron-builder substitutes the real version through
            # extraMetadata; the placeholder must not ship.
            version=$(node -p "require('$resources/app/package.json').version" 2>/dev/null || echo none)
            if [ "$version" = "0.0.0-electron" ] || [ "$version" = "none" ]; then
              echo "packaged app version is $version, expected the root package.json version"
              exit 1
            fi
            productName=$(node -p "require('$resources/app/package.json').productName" 2>/dev/null || echo none)
            if [ "$productName" != "VoiceStudio" ]; then
              echo "packaged productName is $productName; userData would leave the AppImage's folder"
              exit 1
            fi

            # The backend rejects the GGUF runtime unless every file in bin/
            # matches the manifest and the manifest covers every file.
            (cd "$resources/bin" && sha256sum --check --strict checksums.sha256)
            for file in "$resources"/bin/*; do
              name=$(basename "$file")
              [ "$name" = checksums.sha256 ] && continue
              if ! grep -q "  $name\$" "$resources/bin/checksums.sha256"; then
                echo "bin/$name is not listed in bin/checksums.sha256"
                exit 1
              fi
            done
            if [ ! -x "$resources/bin/omnivoice-tts-linux-x86_64" ]; then
              echo "bin/omnivoice-tts-linux-x86_64 is not executable"
              exit 1
            fi
            ls "$resources"/bin/libggml*.so* >/dev/null
            touch $out
          '';

          # The launcher must exec this package's own renamed binary. Both
          # halves of this broke silently before: a copy of electron_44-bin's
          # wrapper still exec'd nixpkgs' dist (Electron's "path-to-app" window),
          # and leaving the binary named "electron" makes app.isPackaged false,
          # so the app demands a repo .venv it can never have.
          launcherTargetsApp = pkgs.runCommand "voicestudio-check-launcher"
            {
              nativeBuildInputs = [ pkgs.binutils ];
            } ''
            dist=${app}/libexec/electron
            electron=${pkgs.electron_44-bin}

            # App::IsPackaged compares the executable basename against
            # "electron"; shipping that name reports the app as unpackaged.
            if [ -e "$dist/electron" ]; then
              echo "the Electron runtime is still named 'electron', so app.isPackaged will be false"
              exit 1
            fi
            if [ ! -x "$dist/voicestudio" ]; then
              echo "missing renamed Electron runtime at $dist/voicestudio"
              exit 1
            fi

            # The wrapper has to exec that binary, not a store path belonging
            # to electron_44-bin. Match its exact output path: nixpkgs names
            # that output "electron-<version>", so a name pattern never fires.
            if ! grep -q "$dist/voicestudio" ${app}/bin/voicestudio; then
              echo "bin/voicestudio does not exec $dist/voicestudio"
              exit 1
            fi
            if grep -qF "$electron" ${app}/bin/voicestudio; then
              echo "bin/voicestudio still points into the electron_44-bin output"
              exit 1
            fi

            # GTK dialogs need the schemas nixpkgs' electron wrapper provides.
            if ! grep -q 'gsettings-schemas' ${app}/bin/voicestudio; then
              echo "bin/voicestudio does not pass the GSettings schemas (wrapGAppsHook3)"
              exit 1
            fi

            # Nix owns updates for this read-only store path.
            if ! grep -q VOICESTUDIO_DISABLE_UPDATER ${app}/bin/voicestudio; then
              echo "bin/voicestudio does not disable the in-app updater"
              exit 1
            fi

            # The menu entry must launch this package, not a bare name or the
            # desktop item's own output (which has no bin/).
            if ! grep -qx "Exec=${app}/bin/voicestudio" ${app}/share/applications/VoiceStudio.desktop; then
              echo "VoiceStudio.desktop does not exec ${app}/bin/voicestudio"
              exit 1
            fi

            # Bundled .so files must resolve inside this output, not in
            # electron_44-bin's dist directory.
            for binary in "$dist/voicestudio" "$dist/chrome_crashpad_handler"; do
              if readelf -d "$binary" | grep -qF "$electron"; then
                echo "$binary still resolves libraries from the electron_44-bin dist"
                exit 1
              fi
            done

            touch $out
          '';

          # The runtime node_modules is pruned to what the main process
          # imports; this fails if a new import is added without extending
          # scripts/prune-runtime-node-modules.mjs.
          runtimeModulesResolve = pkgs.runCommand "voicestudio-check-runtime-modules"
            {
              nativeBuildInputs = [ pkgs.nodejs_22 ];
            } ''
            cd ${app}/libexec/electron/resources
            node -e '
              const fs = require("node:fs");
              const path = require("node:path");
              const { createRequire } = require("node:module");
              const requireFromApp = createRequire(path.resolve("app/out/main/index.js"));
              const files = [
                "app/out/main/index.js",
                "app/out/main/repair-mcp-server.js",
                "app/out/preload/index.mjs",
              ];
              // The .docx worker resolves this by name instead of importing it.
              const specifiers = new Set(["word-extractor"]);
              const pattern = /(?:require\(|from\s*)[\x22\x27]([^\x22\x27]+)[\x22\x27]/g;
              for (const file of files) {
                for (const match of fs.readFileSync(file, "utf8").matchAll(pattern)) {
                  const spec = match[1];
                  if (spec.startsWith(".") || spec.startsWith("node:") || spec === "electron") continue;
                  specifiers.add(spec);
                }
              }
              for (const spec of specifiers) requireFromApp.resolve(spec);
              console.log("resolved " + specifiers.size + " runtime module specifiers");
            '
            touch $out
          '';

          # Every symlink the package ships must resolve: the pruned runtime
          # node_modules and the dist are relocated copies, and a dangling
          # link there is a module that fails to load at runtime.
          symlinksResolve = pkgs.runCommand "voicestudio-check-symlinks" { } ''
            dangling=$(find ${app}/ -xtype l)
            if [ -n "$dangling" ]; then
              echo "dangling symlinks in the package:"
              echo "$dangling"
              exit 1
            fi
            touch $out
          '';

          # The bundle must not reference the node-deps derivation: that would
          # pull a duplicate node_modules into every install's closure.
          noNodeDepsReference = pkgs.runCommand "voicestudio-check-no-node-deps"
            {
              nativeBuildInputs = [ pkgs.gnugrep ];
            } ''
            if grep -rIl -- '${nodeDepsDev}' \
              ${app}/libexec/electron/resources/app/out \
              ${app}/libexec/electron/resources/frontend >&2; then
              echo "built bundles still reference the node deps store path" >&2
              exit 1
            fi
            touch $out
          '';
        });

      formatter = forDevSystems (pkgs: pkgs.nixpkgs-fmt);
    };
}
