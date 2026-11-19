/**
 * The interpreter the Windows ROCm recipe pins, in the form uv asks for it.
 *
 * `scripts/windows-rocm-recipe.json` is the source of truth: it carries the
 * interpreter version, ABI and platform that the wheels and the HIP runtime were
 * reviewed against. Both the setup script and the desktop readiness check have to
 * agree with it, so neither of them spells the version out again.
 */

/** uv's target-triple name for the platform names the recipe uses. */
const UV_PLATFORM = { "win-amd64": "windows-x86_64", "win-arm64": "windows-aarch64" };

/** `cpython-<version>-<target>` for the recipe, or an error naming the missing field. */
export function windowsRocmPythonRequest(recipe) {
  const python = recipe?.python ?? {};
  const target = UV_PLATFORM[python.platform];
  if (!python.version || !python.abi || !target) {
    throw new Error(
      "scripts/windows-rocm-recipe.json must pin python.version, python.abi and python.platform",
    );
  }
  return `cpython-${python.version}-${target}`;
}

/** The human-readable interpreter version the recipe pins, for messages. */
export function windowsRocmPythonVersion(recipe) {
  const version = recipe?.python?.version;
  if (!version) throw new Error("scripts/windows-rocm-recipe.json must pin python.version");
  return version;
}
