import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setupApi } from "../../scripts/setup-api.mjs";
import { selectTorchVariant } from "../../electron/scripts/torch-variant.mjs";

test("Windows x64 ROCm is explicit and never queries NVIDIA", async () => {
  assert.equal(
    await selectTorchVariant({
      platform: "win32",
      arch: "x64",
      env: { OMNIVOICE_TORCH_VARIANT: " ROCm " },
      nvidiaAvailable: () => {
        throw Error("unexpected probe");
      },
    }),
    "rocm",
  );
});

test("Windows ARM64 refuses the native ROCm recipe before sync", async () => {
  await assert.rejects(
    selectTorchVariant({
      platform: "win32",
      arch: "arm64",
      env: { OMNIVOICE_TORCH_VARIANT: "rocm" },
    }),
    /Windows.*x64/,
  );
});

test("source ROCm sync protects the overlay and installs before native repair", async () => {
  const calls = [];
  const env = { OMNIVOICE_TORCH_VARIANT: " ROCm ", UV_PROJECT_ENVIRONMENT: "H:/Voice Env" };
  const status = await setupApi({
    platform: "win32",
    arch: "x64",
    env,
    log: () => {},
    repairNativeLibraries: async () => {
      calls.push(["repair"]);
    },
    run: (command, args, options) => {
      calls.push([command, args, options]);
      return { status: 0 };
    },
  });
  assert.equal(status, 0);
  const sync = calls[0][1];
  assert.ok(sync.includes("--inexact"));
  assert.equal(sync[sync.indexOf("--python") + 1], "cpython-3.12-windows-x86_64");
  const excluded = sync.flatMap((arg, i) => (arg === "--no-install-package" ? [sync[i + 1]] : []));
  assert.ok(
    ["torch", "torchaudio", "torchvision", "ctranslate2", "nvidia-cudnn-cu12"].every((name) =>
      excluded.includes(name),
    ),
  );
  assert.ok(!excluded.includes("nvidia-ml-py"));
  assert.deepEqual(calls[1][1], [
    "run",
    "--no-sync",
    "python",
    "scripts/setup.py",
    "--windows-prerequisites-only",
  ]);
  assert.deepEqual(calls[2][1], [
    "run",
    "--no-sync",
    "python",
    "-m",
    "backend.core.windows_rocm.install",
    "--uv",
    "uv",
  ]);
  assert.equal(calls[2][2].env.OMNIVOICE_TORCH_VARIANT, "rocm");
  assert.equal(calls[2][2].env.UV_PROJECT_ENVIRONMENT, env.UV_PROJECT_ENVIRONMENT);
  assert.deepEqual(calls[3], ["repair"]);
  assert.deepEqual(calls[4][1], ["run", "--no-sync", "python", "scripts/setup.py"]);
  assert.equal(env.OMNIVOICE_TORCH_VARIANT, " ROCm ");
});

test("a failed Windows overlay stops setup before native imports", async () => {
  let repaired = false;
  const calls = [];
  assert.equal(
    await setupApi({
      platform: "win32",
      arch: "x64",
      env: { OMNIVOICE_TORCH_VARIANT: "rocm" },
      log: () => {},
      repairNativeLibraries: async () => {
        repaired = true;
      },
      run: (_command, args) => {
        calls.push(args);
        return { status: args.includes("backend.core.windows_rocm.install") ? 1 : 0 };
      },
    }),
    1,
  );
  assert.equal(repaired, false);
  assert.equal(calls.length, 3);
});

test("packaging carries the recipe and all shared native helpers", async () => {
  const { default: config } = await import("../../electron/electron-builder.config.mjs");
  assert.ok(config.extraResources.some((item) => item.to === "scripts/windows-rocm-recipe.json"));
  assert.ok(config.extraResources.some((item) => item.to === "scripts/smoke_windows_rocm.py"));
  const backend = config.extraResources.find((item) => item.to === "backend");
  assert.ok(backend.filter.includes("**/*"));
  assert.ok(
    readFileSync(
      new URL("../../backend/core/windows_rocm/install.py", import.meta.url),
      "utf8",
    ).includes("def main("),
  );
});
