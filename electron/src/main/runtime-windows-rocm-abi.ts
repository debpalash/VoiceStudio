/** Shared ABI guard, importable without Electron launch dependencies. */
export const WINDOWS_ROCM_ABI_PROBE =
  'from backend.core.windows_rocm.recipe import validate_target; validate_target()';
