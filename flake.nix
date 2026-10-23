{
  description = "VoiceStudio — Open-source, local-first voice cloning and speech generation studio";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      forAllSystems = f: nixpkgs.lib.genAttrs supportedSystems (system: f (import nixpkgs {
        inherit system;
        config = {
          allowUnfree = true;
        };
      }));
    in
    {
      devShells = forAllSystems (pkgs:
        let
          env = self.packages.${pkgs.stdenv.hostPlatform.system} or {};

          linuxLibs = with pkgs; [
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

          darwinLibs = if pkgs.stdenv.hostPlatform.isDarwin then [
            pkgs.apple-sdk
          ] else [];

          nativeDeps = if pkgs.stdenv.hostPlatform.isDarwin then darwinLibs else linuxLibs;

          commonPackages = with pkgs; [
            bun
            nodejs_22
            uv
            rustc
            cargo
            pkg-config
            ffmpeg
            git
          ] ++ nativeDeps;
        in
        {
          default = pkgs.mkShell {
            packages = commonPackages;

            shellHook = pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
              export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath linuxLibs}''${LD_LIBRARY_PATH:+:}$LD_LIBRARY_PATH"
              export PKG_CONFIG_PATH="${pkgs.lib.makeSearchPathOutput "dev" "lib/pkgconfig" commonPackages}''${PKG_CONFIG_PATH:+:}$PKG_CONFIG_PATH"
            '';
          };
        } // pkgs.lib.optionalAttrs (env ? fhs) {
          fhs = env.fhs.env;
        }
      );

      packages = forAllSystems (pkgs:
        let
          linuxLibs = with pkgs; [
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

          commonPackages = with pkgs; [
            bun
            nodejs_22
            uv
            rustc
            cargo
            pkg-config
            ffmpeg
            git
          ] ++ (if pkgs.stdenv.hostPlatform.isLinux then linuxLibs else [ pkgs.apple-sdk ]);

          fhsEnv = if pkgs.stdenv.hostPlatform.isLinux then
            pkgs.buildFHSEnv {
              name = "voicestudio-fhs";
              targetPkgs = pkgs: commonPackages ++ (with pkgs; [
                gcc
                gnumake
                binutils
                zsync
                zlib
              ]);
              profile = ''
                export OMNIVOICE_PORT="''${OMNIVOICE_PORT:-3900}"
                export UV_PYTHON_PREFERENCE="only-managed"
              '';
              runScript = "bash";
            }
          else
            null;
        in
        pkgs.lib.optionalAttrs (fhsEnv != null) {
          default = fhsEnv;
          fhs = fhsEnv;
        }
      );
    };
}
