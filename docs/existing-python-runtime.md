# Using an existing Python runtime

VoiceStudio can use an existing local Python interpreter instead of installing its managed runtime. This is intended for development and for hosts that already have the required model dependencies.

## Requirements

- Python 3.11 or newer.
- The selected interpreter must be an executable file.
- The validation probe performs real imports (including FastAPI, Uvicorn, OmniVoice, Torch, and the speech engine dependencies). `find_spec` is not sufficient.
- Keep the environment in place. Searching and validation do not install packages. Dependency installation is a separate, explicitly confirmed action, limited to eligible virtual environments.

## Selecting an interpreter

On the first-run backend screen, choose **Choose interpreter**, or use **Settings → Performance → Python runtime**. Select the interpreter itself (for example, `.venv/bin/python` or `.venv\\Scripts\\python.exe`). VoiceStudio validates it before saving:

1. It checks the path and Python version.
2. It runs the import probe with offline model/download settings.
3. It reports missing modules or the import that failed.
4. It shows a successful validation without changing the saved selection. Choose **Confirm use and continue** to revalidate, save the absolute path and restart the backend.

If modules are missing, choose another interpreter or review the dependency installation plan. Only reviewed package specifications are offered; unknown imports do not become arbitrary pip arguments. The plan shows the exact interpreter and packages. Installation requires both UI consent and a native confirmation dialog, uses no sudo or system-package override, and is not offered for system/protected environments. Cancellation leaves the environment and saved selection unchanged. After installation, validation runs again and still requires **Confirm use and continue** before saving. Installation errors retain diagnostic output. Existing environments are not backed up automatically; use a dedicated virtual environment if other applications share your current one.

Use **Use managed runtime** to remove the selection and restart with the bundled/managed environment.

## Quick search

First-run setup and Settings also provide **Quick search**. It searches common PATH/system installations, pyenv/Conda/uv locations and bounded project folders. It lists absolute interpreter paths and versions when environment metadata provides them. Discovery does not run Python or install packages; a listed interpreter is not yet known to be compatible. Click a result to validate it; the saved selection and backend remain unchanged until **Confirm use and continue**.

The search is not a full-disk inventory. A four-second deadline, directory/depth limits and excluded dependency/cache folders keep it responsive. Results are marked potentially incomplete when access is denied, another filesystem error occurs or scan limits are reached. Missing optional search roots are not errors. For permission-denied locations, allow access in the operating system if appropriate, or use **Choose interpreter** to select the file directly. Manual selection remains available even when no candidates are found.

Separate virtual environments are kept separate even when their Python executables link to the same base interpreter. Duplicate aliases within one environment are consolidated. Searching never changes the current saved selection.

## Overrides and troubleshooting

An `OMNIVOICE_BACKEND_CMD` override remains authoritative and is shown in Settings. It is not replaced by a selected interpreter. Clear the override if the selected interpreter is not being used.

A Python 3.10 environment is rejected before backend imports. Install the missing dependencies into a separate Python 3.11+ environment rather than modifying a user environment that VoiceStudio did not create. If validation succeeds but startup fails, check the backend log for the import marker and restart after correcting the environment.
