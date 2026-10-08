import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { repairSourceNativeLibraries } from "../../electron/scripts/native-compat.mjs";

test("Linux source setup repairs CTranslate2 before native imports, including custom venvs", async () => {
  const project = await mkdtemp(join(tmpdir(), "vs-source-native-"));
  try {
    const sitePackages = join(project, "site-packages");
    const libraryDir = join(sitePackages, "ctranslate2.libs");
    await mkdir(libraryDir, { recursive: true });
    const library = join(libraryDir, "libctranslate2-test.so.4.4.0");
    const elf = Buffer.alloc(120);
    elf.set([0x7f, 0x45, 0x4c, 0x46, 2, 1]);
    elf.writeBigUInt64LE(64n, 32);
    elf.writeUInt16LE(56, 54);
    elf.writeUInt16LE(1, 56);
    elf.writeUInt32LE(0x6474e551, 64);
    elf.writeUInt32LE(7, 68);
    for (const environment of [".venv", "custom-venv", join(project, "absolute-venv")]) {
      await writeFile(library, elf);
      const env = { UV_PROJECT_ENVIRONMENT: environment };
      await repairSourceNativeLibraries(project, "linux", {
        env,
        run: (python, args, options, callback) => {
          assert.equal(python, join(resolve(project, environment), "bin", "python"));
          assert.match(args[1], /sysconfig/);
          assert.equal(options.env, env);
          callback(null, `${sitePackages}\n`);
        },
      });
      assert.equal((await readFile(library)).readUInt32LE(68), 6);
    }
  } finally {
    await rm(project, { recursive: true, force: true });
  }
});

test("native source repair does not invoke Python outside Linux", async () => {
  for (const platform of ["darwin", "win32"]) {
    await repairSourceNativeLibraries("/unused", platform, {
      run: () => {
        throw Error("unexpected probe");
      },
    });
  }
});

test("native source repair surfaces missing or failed interpreter probes", async () => {
  for (const [error, stdout, expected] of [
    [Error("broken Python"), "", /broken Python/],
    [null, "\n", /Could not locate Python native libraries/],
  ]) {
    await assert.rejects(
      repairSourceNativeLibraries("/unused", "linux", {
        run: (_python, _args, _options, callback) => callback(error, stdout),
      }),
      expected,
    );
  }
});
