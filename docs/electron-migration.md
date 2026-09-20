# Moving to Electron

Electron is the only maintained desktop app. Tauri reached its final release at
v0.5.3 and is archived; existing Tauri downloads remain available for migration.

1. Find your Tauri data directory in Settings and back up the entire directory
   while the app is closed. Keep reference audio stored outside it too.
2. Install Electron for your platform. Keep Tauri and its data until verification.
   Do not run both apps against the same data directory.
3. Check Electron's configured data location before generating. Use its supported
   storage/backend configuration to select the existing data directory.
4. Verify voices, projects, history, and model locations. Generate a short test
   clip before removing the old app.

The shells share the backend, but shell preferences and credentials are not
guaranteed to migrate. Recheck devices, shortcuts, theme, backend address and
permissions. No automatic installer-to-installer migration is provided.

The final Tauri updater feeds retain signed Tauri payloads at immutable URLs.
A Tauri updater must never receive an Electron installer.

Electron checks required Python imports before reusing an existing runtime. An
incomplete environment opens setup instead of repeatedly crashing; installation
still requires your explicit action. Automatic selection skips broken legacy
runtimes and uses the Electron runtime location, leaving Tauri data intact.

An explicitly selected runtime is not silently replaced during startup. If that
location contains an incomplete environment Electron does not own, choosing
setup creates a separate runtime in Electron’s default location instead of
modifying or taking ownership of the existing environment.

A new custom runtime destination remains selectable. Setup creates and owns it
only when that directory does not already exist; existing unowned roots stay
untouched even if their `project` subdirectory is missing.

Dependency checks ignore inherited `PYTHONPATH` and `PYTHONHOME`, matching backend
startup. If repair switches away from an unowned environment and a healthy
Electron runtime already exists, it is reused without reinstalling dependencies.
