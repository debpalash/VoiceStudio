"""Canonical host compute-capability probe — the single source of truth for
"what can this machine actually accelerate on."

Every routing decision (the engine compatibility matrix, ``/setup/preflight``,
``/system/diagnose``, and the synth-time no-silent-fallback gating) reads from
``detect_host_caps()`` so the probe and the model loader can never disagree.

Design contract (load-bearing):
  - **Never raises** to a caller. A broken torch / driver crash degrades to a
    cached CPU-only ``probe_ok=False`` result; every endpoint stays responsive
    (local-first: the app must work with no GPU and even with a broken torch).
  - **No network call** — driver/sysctl reads only, no tensor allocation, so it
    stays kernel-free on cold start.
  - **No new regex** on any driver/device string (CodeQL py/polynomial-redos):
    the only string parse is the ``int(driver.split(".")[0])`` shape reused
    from the wizard, and arch comparison is plain list membership.
  - Distinguishes **ROCm from CUDA** (unlike the gguf ``hardware_probe``):
    ROCm-on-HIP presents through ``torch.cuda`` but is reported ``family="rocm"``.

The ``get_best_device()`` loader (``services.model_manager``) delegates its
*family* decision here while keeping its own DirectML branch and the ROCm
``HSA_OVERRIDE_GFX_VERSION`` env side-effect — the probe **reads**, the loader
**writes**. (The gguf ``hardware_probe.detect_capabilities()`` rebase onto this
module is a deliberate follow-up: it has its own torch-mocked test suite and a
VRAM-driven quant table that is unaffected by the family rename, so it is kept
out of this backend-only slice.)
"""
from __future__ import annotations

import functools
import os
import platform as _platform
import re
import sys
from dataclasses import dataclass
from typing import Literal

DeviceFamily = Literal["cuda", "rocm", "mps", "xpu", "npu", "cpu"]
ACCELERATOR_PRIORITY = ("cuda", "rocm", "xpu", "npu", "mps")

# Stable substring stamped onto notes that represent a real kernel-launch risk
# (arch/driver mismatch) — as opposed to advisory notes (multi-GPU, VRAM query
# failed, DirectML present). ``engine_routing`` keys the "accelerated, but…"
# caveat off this marker so advisory notes never downgrade an accelerated badge.
KERNEL_RISK_MARKER = "may fail at kernel launch"

# Substring marking a DirectML-present (Windows GPU) host. The probe reports
# such hosts as ``family="cpu"`` (DirectML is not a torch device family); the
# router reads this marker to explain the neutral badge instead of "no GPU".
DIRECTML_MARKER = "DirectML device present"

# Stamped onto the note for a host whose OS reports a discrete GPU that the
# installed PyTorch build cannot drive (an AMD card + the NVIDIA-CUDA wheel,
# an NVIDIA card + a CPU wheel). ``engine_routing`` reads it so such a host's
# GPU-capable engines say "cpu_fallback, and here is why" instead of the
# neutral "cpu_only" a GPU-less machine gets - a Radeon owner must not be told
# their machine simply has no GPU.
UNUSABLE_GPU_MARKER = "present but PyTorch cannot use it"

# NOTE: the NVIDIA driver-version check (CUDA 12.x minor-compat floor: R525.60.13 Linux / R528.33 Windows)
# is intentionally NOT done here — it requires shelling to ``nvidia-smi``, which
# would put a subprocess on the cold-start probe path. That check stays in
# ``wizard._detect_gpu`` (preflight), which already runs it. The probe only
# emits the torch-visible SM-arch caveat (cheap, metadata-only).

# ── ROCm GFX version overrides ───────────────────────────────────────────
# AMD GPUs on ROCm present through ``torch.cuda`` but some consumer parts have
# GFX IDs the installed ROCm build wasn't compiled for. Setting
# ``HSA_OVERRIDE_GFX_VERSION`` runs them on the closest supported architecture.
# Applied (with side effects) by ``model_manager._configure_rocm_if_needed``;
# read here so ``arch_unsupported()`` doesn't flag a GPU we know how to remap.
#
# Values are the TARGET gfx name, not the HSA version string, so callers can
# check whether the installed wheel actually contains that target before
# treating the remap as a solution (``hsa_override_for`` derives the env-var
# form). Remapping onto an architecture the build doesn't ship is not a fix —
# it just moves the failure from "no kernel for gfx1151" to "no kernel for
# gfx1100".
ROCM_GFX_OVERRIDES = {
    # RDNA 3.5 (Strix Point / Strix Halo APUs) — override to gfx1100
    "gfx1150": "gfx1100", "gfx1151": "gfx1100",
    # RDNA 3 (RX 7000 series) — override to gfx1100
    "gfx1101": "gfx1100", "gfx1102": "gfx1100", "gfx1103": "gfx1100",
    # RDNA 2 (RX 6000 series) — override to gfx1030
    "gfx1031": "gfx1030", "gfx1032": "gfx1030", "gfx1034": "gfx1030",
    # Vega (RX Vega / Radeon VII) — override to gfx900 / gfx906
    "gfx902": "gfx900", "gfx906": "gfx906",
}


def hsa_override_for(target_gfx: str) -> str:
    """``"gfx1100"`` → ``"11.0.0"``, the form HSA_OVERRIDE_GFX_VERSION wants.

    The digits are major / minor / step, with the last two characters always
    one digit each: gfx1100 → 11.0.0, gfx1030 → 10.3.0, gfx906 → 9.0.6.
    """
    digits = _normalize_arch(target_gfx).removeprefix("gfx")
    if len(digits) < 3 or not digits.isdigit():
        raise ValueError(f"not a gfx architecture name: {target_gfx!r}")
    return f"{digits[:-2]}.{digits[-2]}.{digits[-1]}"


def _normalize_arch(tag: str) -> str:
    """``"gfx90a:xnack+"`` → ``"gfx90a"``. Feature flags dropped, lowercased."""
    return str(tag).split(":")[0].strip().lower()


def build_arch_list(torch) -> list[str]:
    """This torch build's compiled architecture list, or ``[]`` if unknown.

    Prefers the public ``get_arch_list`` and falls back to the private
    ``_get_arch_list`` (older wheels only expose the latter).
    """
    for name in ("get_arch_list", "_get_arch_list"):
        fn = getattr(torch.cuda, name, None)
        if callable(fn):
            try:
                return [str(a) for a in (fn() or [])]
            except Exception:
                return []
    return []


_CUDA_ARCH_TAG = re.compile(r"^(sm|compute)_(\d+)([a-z]?)$")


def cuda_build_covers(arch_list, major: int, minor: int) -> bool:
    """Can a torch build compiled for ``arch_list`` run on CC ``major.minor``?

    NOT an exact-tag match, because NVIDIA's compatibility rules are not exact
    and PyTorch depends on that (#1285):

    * **SASS (``sm_XY``) is binary-compatible upward within a major version** —
      a cubin built for 8.6 runs on any 8.x device with minor ≥ 6. This is why
      the official wheels ship ``sm_80``/``sm_86`` and **no ``sm_89``**: the
      8.6 kernels already cover Ada. An exact-match gate therefore declared
      every RTX 40-series card (4060…4090, all sm_89) unsupported and
      force-routed it to CPU, which is exactly what #1285 reported.
    * **PTX (``compute_XY``) JIT-compiles forward** to any newer architecture,
      so embedded PTX at or below the device's capability is a valid path.
    * **An ``a``/``f`` suffix (``sm_90a``) is architecture-SPECIFIC** — those
      cubins deliberately do not forward-run, so they only count on an exact
      capability match.

    Unparseable entries are skipped rather than guessed at.
    """
    device_cc = major * 10 + minor
    for entry in arch_list or ():
        m = _CUDA_ARCH_TAG.match(str(entry).strip())
        if not m:
            continue
        kind, digits, suffix = m.group(1), m.group(2), m.group(3)
        try:
            cc = int(digits)
        except ValueError:
            continue
        e_major, e_minor = divmod(cc, 10)
        if suffix:
            # Arch-specific: exact capability only, whatever the kind.
            if cc == device_cc:
                return True
            continue
        if kind == "sm":
            if e_major == major and e_minor <= minor:
                return True
        elif cc <= device_cc:
            return True
    return False


def gfx_for_hsa_override(value: str) -> str | None:
    """``"11.0.0"`` → ``"gfx1100"``. The inverse of :func:`hsa_override_for`.

    ``None`` for anything that isn't a three-part numeric version — the user
    set something we don't understand, and a guess is worse than leaving it be.
    """
    parts = str(value).strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    major, minor, step = parts
    if len(minor) != 1 or len(step) != 1:
        return None
    return f"gfx{int(major)}{minor}{step}"


#: The ROCm kernel driver interface. Its absence, or its presence without
#: permission, are the two commonest reasons a ROCm host silently runs on CPU.
_KFD_DEVICE = "/dev/kfd"
_DXG_DEVICE = "/dev/dxg"
_DXG_RUNTIME_PATHS = (
    "/usr/lib/libdxcore.so",
    "/usr/lib/librocdxg.so",
    "/usr/share/rocdxg/dids.conf",
)


def _rocm_requires_dxg_detection(version: object) -> bool:
    """Whether WSL's ROCDXG bridge still needs its explicit opt-in."""
    try:
        parts = str(version).split(".")
        return (int(parts[0]), int(parts[1])) < (7, 13)
    except (IndexError, TypeError, ValueError):
        # Unknown versions get the conservative advice. The variable is
        # harmless on newer runtimes and necessary on every older one.
        return True


def _unusable_gpu_note(torch, gpus) -> str | None:
    """Note for the commonest silent-CPU host: hardware the torch build cannot
    drive. ``None`` when no discrete GPU is known or torch can drive it."""
    from core.gpu_inventory import pick_for_build

    try:
        hip = getattr(torch.version, "hip", None)
        cuda = getattr(torch.version, "cuda", None)
    except Exception:  # noqa: BLE001
        hip = cuda = None
    gpu = pick_for_build(tuple(gpus or ()), "rocm" if hip else "cuda" if cuda else "cpu")
    if gpu is None:
        return None
    build = f"ROCm {hip}" if hip else (f"CUDA {cuda}" if cuda else "CPU-only")
    return f"{gpu.name} ({gpu.vendor.upper()}) {UNUSABLE_GPU_MARKER}: this is a {build} PyTorch build"


def why_no_gpu(torch, gpus=None) -> tuple[str, ...]:
    """Why ``torch.cuda.is_available()`` said no, as user-facing advisories.

    This branch used to produce **nothing** (#1274/#1228). A host with a GPU
    the app could not use reported "Compute device: cpu / GPU active: no" and
    stopped there — true, useless, and indistinguishable from a machine that
    has no GPU at all. Two rounds of back-and-forth per report followed, and
    the reporter still ended up guessing (numeric ``--group-add`` values
    copied from another host, an ``HSA_OVERRIDE_GFX_VERSION`` that may or may
    not have been needed).

    The distinctions worth making are cheap, and the probe already knows them:

    * the wheel has no GPU support compiled in at all — no amount of
      device-passing or env vars will change that;
    * it is a ROCm wheel and ``/dev/kfd`` is absent — in a container that is a
      missing ``--device`` flag, not a driver problem;
    * ``/dev/kfd`` is there but this process cannot open it — a group
      membership problem, which is the one that bites hardest in Docker
      because the ``render``/``video`` GIDs differ between hosts and the
      numbers are usually copied from somewhere else;
    * everything is present and the runtime still enumerated nothing — the
      GPU is likely newer than this build's ROCm.

    Never raises, and returns ``()`` rather than guessing when it cannot tell.
    """
    # Metadata access itself can raise: `torch.version` is a module attribute
    # on a real torch, but a partially-initialised or shimmed torch-like object
    # can expose it as a property that throws. This function's contract is that
    # it never raises — it is called from the diagnostics path, where an
    # exception would take out the very report meant to explain the problem
    # (CodeRabbit, #1425).
    try:
        version = getattr(torch, "version", None)
        hip = getattr(version, "hip", None)
        cuda = getattr(version, "cuda", None)
    except Exception:  # noqa: BLE001 - never raise from a diagnostic
        return ()

    if not hip and not cuda:
        # A build with no GPU support compiled in. Deliberately silent: this
        # is also every macOS wheel (MPS is probed separately, below) and
        # every CPU Docker image, so a note here would fire on hosts that are
        # working exactly as intended. The situations worth explaining are the
        # ones where the build clearly meant to use a GPU and could not.
        return ()

    if hip:
        # /dev/kfd only exists on Linux; on any other platform its absence
        # says nothing, so don't invent a reason.
        if sys.platform.startswith("linux"):
            if not os.path.exists(_KFD_DEVICE) and os.path.exists(_DXG_DEVICE):
                if not os.access(_DXG_DEVICE, os.R_OK | os.W_OK):
                    return (
                        f"ROCm {hip} is installed and {_DXG_DEVICE} exists, "
                        "but this process cannot open it — pass "
                        "--device /dev/dxg to the WSL container",
                    )
                dxg_detection = os.environ.get("HSA_ENABLE_DXG_DETECTION", "").strip()
                if dxg_detection == "0":
                    return (
                        f"ROCm {hip} is installed and {_DXG_DEVICE} is reachable, "
                        "but HSA_ENABLE_DXG_DETECTION=0 explicitly disables the "
                        "WSL GPU bridge; remove it or set it to 1",
                    )
                if _rocm_requires_dxg_detection(hip) and dxg_detection != "1":
                    return (
                        f"ROCm {hip} is installed and {_DXG_DEVICE} is "
                        "reachable, but this pre-7.13 runtime requires "
                        "HSA_ENABLE_DXG_DETECTION=1 inside WSL containers",
                    )
                missing = [
                    path for path in _DXG_RUNTIME_PATHS if not os.path.exists(path)
                ]
                if missing:
                    return (
                        f"ROCm {hip} can reach {_DXG_DEVICE}, but the WSL "
                        "ROCDXG runtime mounts are incomplete; missing: "
                        f"{', '.join(missing)}",
                    )
                return (
                    f"ROCm {hip} and the WSL ROCDXG bridge are reachable, "
                    "but no GPU was enumerated — verify the AMD Windows "
                    "driver, librocdxg/ROCm compatibility, and host `rocminfo`",
                )
            if not os.path.exists(_KFD_DEVICE):
                return (
                    f"ROCm {hip} is installed but {_KFD_DEVICE} is not "
                    "present — the amdgpu kernel driver isn't loaded, or (in "
                    "Docker) the container was started without "
                    "--device /dev/kfd --device /dev/dri",
                )
            if not os.access(_KFD_DEVICE, os.R_OK | os.W_OK):
                return (
                    f"ROCm {hip} is installed and {_KFD_DEVICE} exists, but "
                    "this process cannot open it — add the groups that own "
                    "it (`ls -l /dev/kfd /dev/dri/render*`; in Docker pass "
                    "--group-add with THAT host's render/video GIDs, which "
                    "differ between machines)",
                )
        override = (os.environ.get("HSA_OVERRIDE_GFX_VERSION") or "").strip()
        if override:
            # Checked BEFORE blaming the ROCm version, because it is the more
            # likely cause and the cheaper thing to test. An override remaps
            # the GPU onto a different architecture, and pointing a natively
            # supported card at one the runtime cannot match to the physical
            # agent can leave HSA with no usable agents at all — which is not
            # "a kernel failed" but "there is no device", exactly what the
            # #1274 reporter saw. Their card (gfx1151) is natively supported
            # by the ROCm this image ships, so the override they set is very
            # likely what hid it.
            return (
                f"ROCm {hip} is installed and the device nodes are reachable, "
                f"but no GPU was enumerated while HSA_OVERRIDE_GFX_VERSION="
                f"{override} is set. Try removing that override first — this "
                "ROCm supports most current cards natively, and remapping one "
                "it already supports can leave the runtime with no usable "
                "device. VoiceStudio sets the override itself when a card "
                "genuinely needs it",
            )
        return (
            f"ROCm {hip} is installed and the device nodes are reachable, but "
            "no GPU was enumerated — most often a card newer than this "
            "build's ROCm. Check `rocminfo` on the host",
        )

    # A CUDA wheel on a machine whose only discrete GPU is not NVIDIA is not a
    # driver problem - blaming the NVIDIA driver sent Radeon owners hunting for
    # a driver they do not have. ``gpus`` is None -> read the OS inventory
    # (registry/sysfs, no subprocess).
    if gpus is None:
        try:
            from core.gpu_inventory import detect_host_gpus

            gpus = detect_host_gpus()
        except Exception:  # noqa: BLE001
            gpus = ()
    if gpus and not any(g.vendor == "nvidia" for g in gpus):
        note = _unusable_gpu_note(torch, gpus)
        if note:
            return (
                f"{note}. A CUDA build only drives NVIDIA GPUs; this machine "
                "has none, so PyTorch engines run on the CPU",
            )
        # Only GPUs not worth a marker (a plain Intel iGPU): still not a
        # driver problem, so name the hardware instead of sending the user
        # after an NVIDIA driver (#2620). No UNUSABLE_GPU_MARKER — routing for
        # such a host stays the benign ``cpu_only``.
        names = ", ".join(dict.fromkeys(g.name for g in gpus if g.name))
        return (
            f"this is a CUDA {cuda} build but no CUDA device was found — this "
            f"machine has no NVIDIA GPU (installed graphics: {names or 'none'}). "
            "A CUDA build only drives NVIDIA GPUs, so PyTorch engines run on "
            "the CPU",
        )
    return (
        f"this is a CUDA {cuda} build but no CUDA device was found — the "
        "NVIDIA driver is missing or too old, or (in Docker) the container "
        "was started without --gpus all",
    )


def arch_unsupported(torch) -> tuple[str, tuple[str, ...]] | None:
    """``(device_arch, build_archs)`` when device 0's architecture is absent
    from this torch build's compiled arch list — i.e. kernels cannot launch
    ("no kernel image is available for execution"). ``None`` means supported,
    unknown, or not applicable.

    **CUDA and ROCm name architectures in different namespaces.** A CUDA build
    reports ``sm_89`` / ``compute_89``; a ROCm build reports ``gfx1100``. The
    check must therefore branch on the build — comparing a CUDA ``sm_`` tag
    against a ROCm ``gfx`` list can never match, which made *every* ROCm host
    look unsupported and silently force-routed it to CPU (#1228). Callers must
    get the verdict from here rather than re-deriving a tag.

    Never raises: any missing/odd metadata degrades to ``None`` (compatible),
    matching the pre-existing fail-open contract.
    """
    try:
        if not torch.cuda.is_available():
            return None
        arch_list = build_arch_list(torch)
        if not arch_list:
            return None

        if getattr(getattr(torch, "version", None), "hip", None) is not None:
            # ── ROCm / HIP: arch_list holds gfx names ─────────────────────
            override = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
            if override:
                # An override remaps the device onto some other gfx target, so
                # the native gfx name no longer describes what will run — but
                # the remap is only valid if this build SHIPS that target. A
                # stale or copy-pasted value (the #1228 reporter had set
                # 11.0.0 on a card that no longer needs it) must not buy a free
                # pass into kernels that don't exist. Unparseable values are
                # left alone: the user asked for something we don't understand,
                # and guessing would be worse than trusting them.
                target = gfx_for_hsa_override(override)
                if target is None or _normalize_arch(target) in {
                    _normalize_arch(a) for a in arch_list
                }:
                    return None
                return f"{target} (HSA_OVERRIDE_GFX_VERSION={override})", tuple(arch_list)
            props = torch.cuda.get_device_properties(0)
            gfx = _normalize_arch(getattr(props, "gcnArchName", "") or "")
            if not gfx:
                return None
            build = {_normalize_arch(a) for a in arch_list}
            if gfx in build:
                return None
            # _configure_rocm_if_needed() can remap this GPU onto a supported
            # target before any kernel launches — but only counts as a fix if
            # the build actually SHIPS that target. Remapping gfx1151 onto
            # gfx1100 in a wheel that has neither just relocates the failure.
            target = ROCM_GFX_OVERRIDES.get(gfx)
            if target and _normalize_arch(target) in build:
                return None
            return gfx, tuple(arch_list)

        # ── CUDA: arch_list holds sm_/compute_ tags ──────────────────────
        major, minor = torch.cuda.get_device_capability(0)
        sm_tag = f"sm_{major}{minor}"
        if cuda_build_covers(arch_list, major, minor):
            return None
        return sm_tag, tuple(arch_list)
    except Exception:
        # Arch metadata unavailable on this torch build — treat as compatible.
        return None


@dataclass(frozen=True)
class HostCaps:
    """Snapshot of the host's accelerator capability. Immutable + cached."""

    family: DeviceFamily
    """Best available accelerator family, else ``"cpu"``."""

    available_families: tuple[DeviceFamily, ...]
    """Everything usable; **always includes** ``"cpu"`` (invariant)."""

    device_name: str = ""
    """Device 0's name, e.g. ``"NVIDIA RTX 4090"`` / ``"Apple Silicon (MPS)"``."""

    vram_gb: float = 0.0
    """CUDA/ROCm total VRAM in GB; MPS = system RAM / 2; 0 for cpu/xpu."""

    driver: str | None = None
    """Raw ROCm HIP version string (``torch.version.hip``) or ``None``. The
    NVIDIA driver-version check is owned by ``wizard._detect_gpu`` (it already
    shells to ``nvidia-smi``); the probe stays subprocess-free."""

    notes: tuple[str, ...] = ()
    """Author-controlled English advisories (never user input). Empty on a
    clean accelerated host."""

    probe_ok: bool = True
    """``False`` only when torch could not be imported (degraded CPU-only)."""

    requested_family: str = "auto"
    """The user's compute-device override as requested — ``"auto"`` when none.
    ``family`` reflects what was actually honored: an override that names a
    family this host doesn't have is noted and ignored, never obeyed blindly."""


#: Every value the compute-device override accepts. "auto" = today's
#: priority pick; "cpu" is always honorable (invariant: cpu is always
#: available); accelerator names are honored only when detected.
DEVICE_OVERRIDE_CHOICES: tuple[str, ...] = ("auto", "cuda", "rocm", "xpu", "mps", "cpu")


def requested_device_override() -> str:
    """The user's compute-device pick: ``OMNIVOICE_DEVICE`` env > the Settings
    choice (``compute_device`` in prefs.json) > ``"auto"``. Env wins so
    power-users can pin a device without the UI silently undoing it (same
    resolution order as engine selection, #981). Unknown values normalize to
    ``"auto"`` — the probe must never raise."""
    try:
        from core import prefs

        raw = prefs.resolve("compute_device", env="OMNIVOICE_DEVICE", default="auto")
    except Exception:
        raw = os.environ.get("OMNIVOICE_DEVICE", "auto")
    val = str(raw or "auto").strip().lower()
    return val if val in DEVICE_OVERRIDE_CHOICES else "auto"


def _probe() -> HostCaps:
    """Run the probe once. Enumerates every failure branch from the spec's
    degradation contract; never raises."""
    try:
        import torch
    except Exception:
        return HostCaps(
            family="cpu",
            available_families=("cpu",),
            notes=("torch not importable; treating host as CPU-only",),
            probe_ok=False,
            requested_family=requested_device_override(),
        )

    notes: list[str] = []
    # Probe EVERY accelerator independently into this list (don't short-circuit
    # after the first hit) so `available_families` is honest on hybrid hosts
    # (e.g. an NVIDIA GPU + an Intel iGPU exposed via IPEX). The preferred
    # `family` is chosen by priority at the end.
    detected: list[DeviceFamily] = []
    device_name = ""
    vram_gb = 0.0
    driver: str | None = None

    # ── CUDA / ROCm (both present through torch.cuda) ────────────────────
    cuda_ok = False
    cuda_probe_failed = False
    try:
        cuda_ok = bool(torch.cuda.is_available())
    except Exception as exc:  # broken CUDA init (forked process / driver crash)
        cuda_probe_failed = True
        notes.append(f"CUDA init raised: {type(exc).__name__}")

    if cuda_ok:
        try:
            count = int(torch.cuda.device_count())
        except Exception:
            count = 0
        if count == 0:
            notes.append("CUDA reports available but device_count==0")
        else:
            is_rocm = getattr(torch.version, "hip", None) is not None
            detected.append("rocm" if is_rocm else "cuda")
            if is_rocm:
                driver = getattr(torch.version, "hip", None)
            if count > 1:
                notes.append(f"{count} GPUs detected; routing reflects device 0")
            try:
                device_name = torch.cuda.get_device_name(0)
            except Exception:
                device_name = ""
            try:
                _free, total = torch.cuda.mem_get_info()
                vram_gb = float(total) / (1024 ** 3)
            except Exception:
                notes.append("VRAM query failed")
            # Arch mismatch — sm_ tags on CUDA, gfx names on ROCm. Shared with
            # model_manager.check_device_compatibility() so probe and loader
            # can never disagree (they used to, on every ROCm host — #1228).
            mismatch = arch_unsupported(torch)
            if mismatch is not None:
                device_arch, archs = mismatch
                notes.append(
                    f"{device_name or 'GPU'} ({device_arch}) not in this torch "
                    f"build's archs ({', '.join(archs)}) — {KERNEL_RISK_MARKER}"
                )

    elif not cuda_probe_failed:
        # A GPU-capable build that found nothing must say why (#1274/#1228).
        # Silence here is what made "Compute device: cpu" indistinguishable
        # from a machine with no GPU at all.
        #
        # Only when the probe actually completed, though. If
        # `torch.cuda.is_available()` RAISED we know nothing about the host's
        # devices, and `why_no_gpu()` would report its findings as fact —
        # "no CUDA device was found" beside "CUDA init raised", which reads as
        # a diagnosis when it is an unfinished probe. The exception note above
        # is the whole truth in that case (CodeRabbit, #1425).
        notes.extend(why_no_gpu(torch))

    # Older builds register XPU through IPEX; modern torch exposes it directly.
    try:
        import intel_extension_for_pytorch  # noqa: F401
    except Exception:
        # Optional IPEX may be absent or incompatible; still probe native torch XPU.
        pass
    try:
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            detected.append("xpu")
            if not device_name:
                try:
                    device_name = torch.xpu.get_device_name(0)
                except Exception:
                    # XPU present but unnamed — family classification still holds.
                    pass
            notes.append("XPU VRAM not queried (unreliable across IPEX versions)")
    except Exception:
        # XPU probe failed — no usable XPU on this host.
        pass

    # Vendor extensions may register an NPU with torch. Probe only an already
    # registered backend; never install or import an optional vendor package.
    try:
        if hasattr(torch, "npu") and torch.npu.is_available():
            detected.append("npu")
            if not device_name:
                try:
                    device_name = torch.npu.get_device_name(0)
                except Exception:
                    # An unavailable display name does not invalidate a usable NPU.
                    pass
            notes.append("NPU VRAM not queried")
    except Exception:
        # Missing or broken vendor backends mean no usable NPU; continue probing.
        pass

    # ── Apple Silicon MPS ────────────────────────────────────────────────
    try:
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            detected.append("mps")
            if not device_name:
                device_name = "Apple Silicon (MPS)"
            if not vram_gb:
                try:
                    import psutil
                    vram_gb = float(psutil.virtual_memory().total) / (1024 ** 3) / 2
                except Exception:
                    notes.append("psutil unavailable; MPS VRAM unknown")
    except Exception:
        # MPS probe raised on a non-Apple/old torch — treat as no MPS.
        pass

    # ── DirectML — Windows GPU, NOT a torch device family ────────────────
    try:
        import torch_directml
        if torch_directml.device_count() > 0:
            notes.append(
                f"{DIRECTML_MARKER} (Windows GPU); torch-family probe treats "
                f"as non-accelerated"
            )
    except Exception:
        # torch_directml absent (the common case) — no DirectML on this host.
        pass

    # Hardware the OS can see but this torch build cannot drive. Only when
    # nothing accelerated was detected: with a working GPU, an extra unused
    # iGPU is noise. (Windows AMD + the shipped CUDA wheel lands here.)
    if not detected and not any(UNUSABLE_GPU_MARKER in n for n in notes):
        try:
            from core.gpu_inventory import detect_host_gpus

            unusable = _unusable_gpu_note(torch, detect_host_gpus())
        except Exception:  # noqa: BLE001 - advisory only
            unusable = None
        if unusable:
            notes.append(unusable)

    # Preferred family by priority; cpu when nothing accelerated was detected.
    family: DeviceFamily = "cpu"
    for pref in ACCELERATOR_PRIORITY:
        if pref in detected:
            family = pref  # type: ignore[assignment]
            break
    # available_families: every detected accelerator + cpu, deduped, cpu last.
    available: tuple[DeviceFamily, ...] = tuple(dict.fromkeys([*detected, "cpu"]))

    # User override (Settings → Performance, or OMNIVOICE_DEVICE): honored
    # only when the named family actually exists on this host — an override
    # can steer, it cannot invent hardware. Applied here, at the single
    # choke point, so routing, model loads (get_best_device delegates its
    # family decision here), and every badge inherit it for free.
    requested = requested_device_override()
    if requested != "auto":
        if requested in available:
            if requested != family:
                notes.append(
                    f"compute device pinned to '{requested}' by user override "
                    f"(auto would pick '{family}')"
                )
            family = requested  # type: ignore[assignment]
        else:
            notes.append(
                f"requested compute device '{requested}' is not available on "
                f"this host (have: {', '.join(available)}) — using '{family}'"
            )

    return HostCaps(
        family=family,
        available_families=available,
        device_name=device_name,
        vram_gb=vram_gb,
        driver=driver,
        notes=tuple(notes),
        probe_ok=True,
        requested_family=requested,
    )


@functools.lru_cache(maxsize=1)
def detect_host_caps() -> HostCaps:
    """Cached per-process host capabilities. Never raises, makes no network
    call, kernel-free on cold start. Host compute capability does not change at
    runtime in any supported desktop flow (no GPU hot-plug; switching the active
    engine does not re-probe — routing is recomputed from these same caps), so
    a single probe per process is correct. ``probe_ok=False`` is cached too."""
    return _probe()


def refresh() -> HostCaps:
    """Clear the cache and re-probe. **TEST-ONLY** — nothing in the running app
    calls this (host caps are immutable per process)."""
    detect_host_caps.cache_clear()
    return detect_host_caps()


# ── CPU-host precision + Windows-on-ARM detection ───────────────────────────

# ── Windows-on-ARM detection ─────────────────────────────────────────────────


def is_windows_on_arm() -> bool:
    """True on a Windows-on-ARM machine, including an x64 interpreter running
    under its Prism emulation layer (the only supported runtime there).

    ``platform.machine()`` reports ``AMD64`` inside the emulated interpreter, so
    it cannot tell a Snapdragon laptop from an ordinary x64 PC. Windows sets
    ``PROCESSOR_ARCHITEW6432=ARM64`` for emulated processes; a native ARM64
    process reports ``PROCESSOR_ARCHITECTURE=ARM64`` instead (also treated as
    ARM — the answer to "is this an ARM Windows machine", not "is Python
    native"). Never raises; always False off Windows.
    """
    if sys.platform != "win32":
        return False
    for key in ("PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE"):
        if os.environ.get(key, "").strip().upper() == "ARM64":
            return True
    return _platform.machine().strip().upper() == "ARM64"


def mlx_supported() -> tuple[bool, str]:
    """``(ok, reason)``. ``ok=True`` **only** on Apple Silicon
    (``sys.platform == "darwin"`` and ``platform.machine() == "arm64"``) with
    torch MPS available — the shared gate for MLX-Audio / MLX-Whisper (#390).

    Gates on exact-string equality (no regex → no CodeQL surface). On any
    non-Apple host it returns ``False`` **before** any package import, so a
    stray ``mlx_*`` wheel on Linux/Windows never reports available.
    """
    if sys.platform != "darwin" or _platform.machine() != "arm64":
        if sys.platform == "darwin":
            return (False, "MLX requires Apple Silicon; this Mac is Intel")
        return (
            False,
            f"MLX requires Apple Silicon; this host is "
            f"{sys.platform}/{_platform.machine()}",
        )
    try:
        import torch
    except Exception:
        return (False, "torch not importable; cannot confirm MPS")
    try:
        if torch.backends.mps.is_available():
            return (True, "")
    except Exception:
        # MPS query raised — fall through to the conservative unavailable path.
        pass
    return (
        False,
        "Apple Silicon detected but torch MPS unavailable; "
        "reinstall torch with MPS support",
    )


__all__ = [
    "DeviceFamily",
    "HostCaps",
    "detect_host_caps",
    "refresh",
    "mlx_supported",
    "is_windows_on_arm",
    "arch_unsupported",
    "gfx_for_hsa_override",
    "hsa_override_for",
    "build_arch_list",
    "ROCM_GFX_OVERRIDES",
    "KERNEL_RISK_MARKER",
    "DIRECTML_MARKER",
    "UNUSABLE_GPU_MARKER",
]
