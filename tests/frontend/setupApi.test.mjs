import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  selectTorchVariant,
  hasNvidiaGpu,
  requestedTorchVariant,
  torchSyncArgs,
} from "../../electron/scripts/torch-variant.mjs";
import { setupApi } from "../../scripts/setup-api.mjs";
import { uvRunArgs } from "../../scripts/dev-backend.mjs";

for (const platform of ["linux", "win32"]) {
  for (const gpu of [false, true]) {
    test(`${platform} selects ${gpu ? "CUDA" : "CPU"} before torch installation`, async () => {
      const variant = await selectTorchVariant({
        env: {},
        platform,
        arch: "x64",
        nvidiaAvailable: async () => gpu,
      });
      assert.equal(variant, gpu ? "cuda" : "cpu");
      assert.deepEqual(torchSyncArgs(variant), gpu ? [] : ["--no-group", "cuda", "--group", "cpu"]);
    });
  }
}

test("macOS preserves MPS-capable native wheels without querying NVIDIA", async () => {
  assert.equal(
    await selectTorchVariant({
      env: {},
      platform: "darwin",
      arch: "arm64",
      nvidiaAvailable: () => {
        throw Error("unexpected probe");
      },
    }),
    "native",
  );
});

for (const variant of ["cpu", "cuda", "rocm"]) {
  test(`honors explicit ${variant} without probing`, async () => {
    assert.equal(
      await selectTorchVariant({
        env: { OMNIVOICE_TORCH_VARIANT: ` ${variant.toUpperCase()} ` },
        platform: "linux",
        arch: "x64",
        nvidiaAvailable: () => {
          throw Error("unexpected probe");
        },
      }),
      variant,
    );
  });
}

test("explicit compute CPU selects CPU wheels, with explicit wheel choice taking precedence", async () => {
  const options = {
    platform: "linux",
    arch: "x64",
    nvidiaAvailable: () => {
      throw Error("unexpected probe");
    },
  };
  assert.equal(await selectTorchVariant({ ...options, env: { OMNIVOICE_DEVICE: " cpu " } }), "cpu");
  assert.equal(
    await selectTorchVariant({
      ...options,
      env: { OMNIVOICE_DEVICE: "cpu", OMNIVOICE_TORCH_VARIANT: "cuda" },
    }),
    "cuda",
  );
  assert.throws(() => requestedTorchVariant({ OMNIVOICE_TORCH_VARIANT: "typo" }), /must be/);
});

test("source setup syncs the locked CPU graph and post-install cannot re-sync CUDA", async () => {
  const calls = [];
  let repaired = false;
  const env = { OMNIVOICE_TORCH_VARIANT: "cpu" };
  assert.equal(
    await setupApi({
      env,
      platform: "linux",
      arch: "x64",
      selectVariant: async () => "cpu",
      log: () => {},
      repairNativeLibraries: async () => {
        repaired = true;
      },
      run: (cmd, args, options) => {
        if (args[0] === "run") assert.equal(repaired, true);
        calls.push([cmd, args, options]);
        return { status: 0 };
      },
    }),
    0,
  );
  assert.deepEqual(
    calls.map(([cmd, args]) => [cmd, ...args]),
    [
      ["uv", "sync", "--locked", "--no-group", "cuda", "--group", "cpu"],
      ["uv", "run", "--no-sync", "python", "scripts/setup.py"],
    ],
  );
  assert.equal(calls[0][2].env, env);
  assert.equal(calls[0][2].cwd, calls[1][2].cwd);
});

test("failed dependency sync never continues into setup or reports success", async () => {
  let count = 0;
  assert.equal(
    await setupApi({
      selectVariant: async () => "cpu",
      log: () => {},
      repairNativeLibraries: async () => {},
      run: () => {
        count++;
        return { status: 9 };
      },
    }),
    9,
  );
  assert.equal(count, 1);
  await assert.rejects(
    setupApi({
      selectVariant: async () => "cpu",
      log: () => {},
      repairNativeLibraries: async () => {},
      run: () => ({ error: Error("uv missing") }),
    }),
    /uv missing/,
  );
});

test("root setup command and every dev restart preserve the chosen CPU environment", () => {
  const pkg = JSON.parse(readFileSync(new URL("../../package.json", import.meta.url), "utf8"));
  assert.equal(pkg.scripts["setup:api"], "node scripts/setup-api.mjs");
  for (const variant of ["", "auto", "cpu", "cuda", "rocm"]) {
    assert.ok(uvRunArgs({ OMNIVOICE_TORCH_VARIANT: variant }).includes("--no-sync"));
  }
});

for (const platform of ["darwin", "linux"]) {
  for (const variant of ["auto", "cpu", "cuda"]) {
    test(`${platform} ARM64 keeps native wheels with ${variant}`, async () => {
      assert.equal(
        await selectTorchVariant({
          env: { OMNIVOICE_TORCH_VARIANT: variant },
          platform,
          arch: "arm64",
          nvidiaAvailable: () => {
            throw Error("unexpected probe");
          },
        }),
        "native",
      );
    });
  }
}

test("Windows ROCm opt-in falls back to a supported CPU or CUDA wheel", async () => {
  for (const hasGpu of [false, true]) {
    assert.equal(
      await selectTorchVariant({
        env: { OMNIVOICE_TORCH_VARIANT: "rocm" },
        platform: "win32",
        arch: "x64",
        nvidiaAvailable: async () => hasGpu,
      }),
      hasGpu ? "cuda" : "cpu",
    );
  }
  assert.equal(requestedTorchVariant({ OMNIVOICE_TORCH_VARIANT: "default" }), "cuda");
});

test("Windows ARM64 selects CPU wheels and an emulated x64 Python", async () => {
  assert.equal(
    await selectTorchVariant({
      env: {},
      platform: "win32",
      arch: "arm64",
      nvidiaAvailable: () => {
        throw Error("unexpected probe");
      },
    }),
    "cpu",
  );
  const calls = [];
  await setupApi({
    env: {},
    platform: "win32",
    arch: "arm64",
    log: () => {},
    repairNativeLibraries: async () => {},
    run: (_command, args) => {
      calls.push(args);
      return { status: 0 };
    },
  });
  assert.deepEqual(calls[0], [
    "sync",
    "--locked",
    "--no-group",
    "cuda",
    "--group",
    "cpu",
    "--python",
    "cpython-3.11-windows-x86_64",
  ]);
  assert.deepEqual(calls[1], ["run", "--no-sync", "python", "scripts/setup.py"]);
});

test("native library repair failure stops before Python post-install", async () => {
  const calls = [];
  await assert.rejects(
    setupApi({
      selectVariant: async () => "cpu",
      log: () => {},
      repairNativeLibraries: async () => {
        throw Error("native repair failed");
      },
      run: (_command, args) => {
        calls.push(args);
        return { status: 0 };
      },
    }),
    /native repair failed/,
  );
  assert.equal(calls.length, 1);
});

test("an installed NVIDIA driver preserves CUDA when nvidia-smi is not on PATH", async () => {
  for (const platform of ["linux", "win32"]) {
    assert.equal(
      await hasNvidiaGpu({
        platform,
        exists: () => true,
        run: () => {
          throw Error("driver paths already proved NVIDIA");
        },
      }),
      true,
    );
  }
});

test("NVIDIA probe errors and empty GPU lists fall back to CPU", async () => {
  for (const [error, stdout, expected] of [
    [null, "NVIDIA test GPU\n", true],
    [null, "\n", false],
    [Error("not installed"), "", false],
    [Error("timed out"), "partial output", false],
  ]) {
    assert.equal(
      await hasNvidiaGpu({
        platform: "linux",
        exists: () => false,
        run: (_command, _args, _options, callback) => callback(error, stdout),
      }),
      expected,
    );
  }
});
