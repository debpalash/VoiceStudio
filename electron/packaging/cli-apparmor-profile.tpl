abi <abi/4.0>,
include <tunables/global>

# The public command launches this private Electron executable.
profile "${executable}" "/opt/${sanitizedProductName}/voicestudio-desktop" flags=(unconfined) {
  userns,
  include if exists <local/${executable}>
}
