import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { repairSourceNativeLibraries } from "../electron/scripts/native-compat.mjs";
import { asciiSafePthFiles } from "../electron/scripts/pth-ascii.mjs";
import {
  selectTorchVariant,
  torchSyncArgs,
  windowsRocmSyncArgs,
} from "../electron/scripts/torch-variant.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export async function setupApi({
  env = process.env,
  platform = process.platform,
  arch = process.arch,
  run = spawnSync,
  selectVariant = selectTorchVariant,
  log = console.log,
  repairNativeLibraries = repairSourceNativeLibraries,
  healPthFiles = asciiSafePthFiles,
} = {}) {
  // PyTorch publishes no Intel-Mac wheels, so the locked sync can only fail
  // part-way and leave an environment the app reports as incomplete (#3584).
  if (platform === "darwin" && arch === "x64")
    throw new Error(
      "Intel Macs cannot install the local backend: PyTorch publishes no Intel-Mac (macOS x86_64) wheels. " +
        "Connect the app to a VoiceStudio backend on another computer (docs/install/macos.md). " +
        "On an Apple Silicon Mac, run this from a native arm64 terminal with an arm64 Bun or Node.",
    );
  const variant = await selectVariant({ env, platform, arch });
  const windowsRocm = platform === "win32" && variant === "rocm";
  if (windowsRocm && arch !== "x64")
    throw new Error("Windows ROCm requires Windows x64 Python 3.12 (cp312).");
  const environment = windowsRocm ? { ...env, OMNIVOICE_TORCH_VARIANT: "rocm" } : env;
  // The matched torch stack has no native Windows ARM64 torchaudio wheel.
  const pythonArgs = windowsRocm
    ? ["--python", "cpython-3.12-windows-x86_64"]
    : platform === "win32" && arch === "arm64"
      ? ["--python", "cpython-3.11-windows-x86_64"]
      : [];
  const syncArgs = windowsRocm
    ? windowsRocmSyncArgs(readFileSync(resolve(root, "uv.lock"), "utf8"))
    : torchSyncArgs(variant);
  log(
    `[setup] PyTorch: ${variant}${variant === "cpu" ? " (GPU acceleration unavailable or disabled; inference will be slower)" : ""}`,
  );
  for (const args of [
    ["sync", "--locked", ...syncArgs, ...pythonArgs],
    ...(windowsRocm
      ? [
          ["run", "--no-sync", "python", "scripts/setup.py", "--windows-prerequisites-only"],
          ["run", "--no-sync", "python", "-m", "backend.core.windows_rocm.install", "--uv", "uv"],
        ]
      : []),
    ["run", "--no-sync", "python", "scripts/setup.py"],
  ]) {
    // A checkout under a non-ASCII folder gets its path written into the
    // editable `.pth` as UTF-8, which Python 3.11 cannot decode on a non-UTF-8
    // code page (#1783). uv queries the existing venv before syncing and every
    // sync rewrites the file, so heal it before each uv call.
    await healPthFiles(resolve(root, ".venv")).catch(() => []);
    const result = run("uv", args, { cwd: root, env: environment, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) return result.status ?? 1;
    if (
      (windowsRocm && args.includes("backend.core.windows_rocm.install")) ||
      (!windowsRocm && args[0] === "sync")
    ) {
      await repairNativeLibraries(root, platform, { env: environment });
    }
  }
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  setupApi()
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error) => {
      console.error(`[setup] ${error.message}`);
      process.exitCode = 1;
    });
}
