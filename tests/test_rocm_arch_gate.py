"""#1228: ROCm hosts were force-routed to CPU on *every* AMD GPU.

The SM-arch compatibility gate compared a CUDA-namespace tag (``sm_115``,
derived from ``get_device_capability()``) against ``torch.cuda.get_arch_list()``
— which on a ROCm wheel returns **gfx names** (``gfx1100``, ``gfx1201``, …).
The two namespaces can never intersect, so ``check_device_compatibility()``
returned False for every ROCm build and ``get_best_device()`` silently returned
``"cpu"``. The reporter's Radeon 8060S (Strix Halo, gfx1151) was visible to
torch, reported by ``torch.cuda.is_available()``, and still ran on the CPU.

These tests pin the CUDA/ROCm-aware comparison (``core.device_caps
.arch_unsupported``), its three consumers, and the narrowed HSA override.
"""
from __future__ import annotations

import types

import pytest

from core import device_caps
from core.device_caps import KERNEL_RISK_MARKER

# A rocm7.2 wheel's real arch list shape; gfx1151 is natively supported there.
ROCM_ARCHS = ["gfx900", "gfx906", "gfx90a", "gfx942", "gfx1030",
              "gfx1100", "gfx1101", "gfx1102", "gfx1151", "gfx1200"]


def _torch(*, hip=None, capability=(11, 5), gcn_arch="gfx1151:xnack-",
           arch_list=None, device_name="Radeon 8060S Graphics",
           cuda_available=True):
    """Minimal torch mock: CUDA build when ``hip`` is None, else a ROCm build."""
    cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: 1,
        get_device_name=lambda i=0: device_name,
        mem_get_info=lambda: (4 * 1024 ** 3, 8 * 1024 ** 3),
        get_device_capability=lambda i=0: capability,
        get_device_properties=lambda i=0: types.SimpleNamespace(gcnArchName=gcn_arch),
        get_arch_list=lambda: list(arch_list if arch_list is not None else []),
    )
    version = types.SimpleNamespace()
    if hip is not None:
        version.hip = hip
    return types.SimpleNamespace(
        cuda=cuda,
        version=version,
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: False)
        ),
    )


@pytest.fixture(autouse=True)
def _no_hsa_override(monkeypatch):
    # Bind the real cached probe up front — a test may monkeypatch the module
    # attribute, and this fixture's teardown runs before monkeypatch's undo.
    clear = device_caps.detect_host_caps.cache_clear
    monkeypatch.setattr("sys.platform", "linux")  # Linux HSA remapping contract.
    # _configure_rocm_if_needed writes os.environ directly. Registering the
    # variable first makes teardown remove whatever a test leaves behind;
    # delenv on an absent variable records no undo.
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "")
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION")
    clear()
    yield
    clear()


# ── the comparison itself ────────────────────────────────────────────────


def test_rocm_gfx_in_build_is_supported():
    """The regression: sm_115 vs a gfx list used to read as a mismatch."""
    torch = _torch(hip="7.2.4", arch_list=ROCM_ARCHS)
    assert device_caps.arch_unsupported(torch) is None


def test_rocm_feature_suffixes_are_ignored():
    """``gfx90a:xnack+`` on either side must still match ``gfx90a``."""
    torch = _torch(hip="6.2", capability=(9, 0), gcn_arch="gfx90a:sramecc+:xnack-",
                   arch_list=["gfx908", "gfx90a:xnack+"])
    assert device_caps.arch_unsupported(torch) is None


def test_rocm_genuine_mismatch_still_detected():
    """A real ROCm arch mismatch must not be papered over by the fix."""
    torch = _torch(hip="6.2", capability=(10, 1), gcn_arch="gfx1010",
                   arch_list=["gfx1030", "gfx1100"])
    assert device_caps.arch_unsupported(torch) == ("gfx1010", ("gfx1030", "gfx1100"))


def test_rocm_gpu_we_can_remap_is_not_a_mismatch():
    """gfx1102 is absent from this build but _configure_rocm_if_needed() remaps
    it to gfx1100, which this build DOES ship."""
    torch = _torch(hip="6.2", capability=(11, 2), gcn_arch="gfx1102",
                   arch_list=["gfx1030", "gfx1100"])
    assert device_caps.arch_unsupported(torch) is None


def test_a_remap_target_the_build_lacks_is_still_a_mismatch():
    """Review finding (#1230): being IN the override map was treated as proof
    of compatibility. If the wheel ships neither the native arch nor the remap
    target, the override only changes which kernel is missing — the host must
    still fall back to CPU rather than launch into a guaranteed failure."""
    torch = _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
                   arch_list=["gfx900", "gfx1030"])  # no gfx1100
    assert device_caps.arch_unsupported(torch) == ("gfx1151", ("gfx900", "gfx1030"))


def test_hsa_override_string_is_derived_from_the_target():
    assert device_caps.hsa_override_for("gfx1100") == "11.0.0"
    assert device_caps.hsa_override_for("gfx1030") == "10.3.0"
    assert device_caps.hsa_override_for("gfx906") == "9.0.6"
    assert device_caps.hsa_override_for("gfx900") == "9.0.0"


# The hand-kept table this derivation replaced. Every remap it made must still
# be made, on a wheel that ships the target but not the card.
_FORMER_TABLE = {
    "gfx1150": "gfx1100", "gfx1151": "gfx1100",
    "gfx1101": "gfx1100", "gfx1102": "gfx1100", "gfx1103": "gfx1100",
    "gfx1031": "gfx1030", "gfx1032": "gfx1030", "gfx1034": "gfx1030",
    "gfx902": "gfx900",
}
_LEGACY_WHEEL = ["gfx900", "gfx906", "gfx908", "gfx90a", "gfx1030", "gfx1100"]


@pytest.mark.parametrize("source,target", sorted(_FORMER_TABLE.items()))
def test_derived_override_reproduces_the_former_table(source, target):
    assert device_caps.rocm_override_target(source, _LEGACY_WHEEL) == target


def test_every_derived_target_has_a_valid_hsa_form():
    for source in [*_FORMER_TABLE, "gfx1152", "gfx1035", "gfx90c"]:
        target = device_caps.rocm_override_target(source, _LEGACY_WHEEL)
        assert target and target.startswith("gfx"), source
        device_caps.hsa_override_for(target)  # must not raise


@pytest.mark.parametrize("gfx,archs,expected", [
    # #3418 class: parts no table listed still get their family's target.
    ("gfx1152", ["gfx1030", "gfx1100", "gfx1101"], "gfx1100"),  # Krackan Point
    ("gfx1035", ["gfx1030", "gfx1100"], "gfx1030"),             # Radeon 680M
    ("gfx1036", ["gfx1030", "gfx1100"], "gfx1030"),
    ("gfx1201", ["gfx1100", "gfx1200"], "gfx1200"),             # RX 9070 on a gfx1200-only wheel
    ("gfx1200", ["gfx1100", "gfx1201"], "gfx1201"),             # RX 9060
    # Closest first: the same minor revision beats the family base.
    ("gfx1151", ["gfx1100", "gfx1150"], "gfx1150"),
    # Never across a generation, never onto a newer minor revision.
    ("gfx1201", ["gfx900", "gfx1030", "gfx1100"], None),
    ("gfx1012", ["gfx1030", "gfx1100"], None),
    ("gfx1102", ["gfx1030"], None),
    # Native, unknown build, not a gfx name: nothing to remap.
    ("gfx1201", ["gfx1100", "gfx1201"], None),
    ("gfx1151", [], None),
    ("sm_89", ["gfx1100"], None),
    # Targets HSA cannot express (gfx90a) are never chosen.
    ("gfx90c", ["gfx90a"], None),
    # gfx9 is three ISAs: CDNA never lands on Vega kernels, nor Vega on CDNA.
    ("gfx942", _LEGACY_WHEEL, None),
    ("gfx908", ["gfx900", "gfx906"], None),
    ("gfx950", ["gfx900", "gfx942"], None),
    ("gfx906", ["gfx908"], None),
    ("gfx909", ["gfx900", "gfx908"], "gfx900"),
    # gfx10 is RDNA1 (101x) and RDNA2 (103x): no remap across them.
    ("gfx1030", ["gfx1010", "gfx1012"], None),
    ("gfx1036", ["gfx1010", "gfx1030"], "gfx1030"),
])
def test_rocm_override_target_is_derived_from_the_wheel(gfx, archs, expected):
    assert device_caps.rocm_override_target(gfx, archs) == expected


@pytest.mark.parametrize("gfx,archs,hsa", [
    ("gfx1152", ["gfx1030", "gfx1100"], "11.0.0"),
    ("gfx1201", ["gfx1100", "gfx1200"], "12.0.0"),
])
def test_a_card_no_table_listed_still_runs_on_the_gpu(gfx, archs, hsa):
    """Regression (#3418): the remap came from a hand-kept table, so a newer
    card the wheel could run through its family target was reported as a
    kernel mismatch and routed to the CPU."""
    import os

    import services.model_manager as mm

    torch = _torch(hip="6.4", gcn_arch=gfx, device_name="AMD Radeon", arch_list=archs)
    assert device_caps.arch_unsupported(torch) is None
    mm._configure_rocm_if_needed(torch)
    assert os.environ.get("HSA_OVERRIDE_GFX_VERSION") == hsa


def test_rocm_user_hsa_override_is_trusted_when_the_build_has_the_target(monkeypatch):
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "11.0.0")
    torch = _torch(hip="6.2", capability=(10, 1), gcn_arch="gfx1010",
                   arch_list=["gfx1030", "gfx1100"])
    assert device_caps.arch_unsupported(torch) is None


def test_a_stale_hsa_override_does_not_buy_a_free_pass(monkeypatch):
    """Review finding (#1228): ANY override was treated as proof of
    compatibility. The reporter had HSA_OVERRIDE_GFX_VERSION=11.0.0 set from
    older advice — if the installed build ships no gfx1100, honouring that
    blindly routes them into kernels that don't exist instead of falling back
    to CPU."""
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "11.0.0")
    torch = _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
                   arch_list=["gfx900", "gfx1030"])  # no gfx1100
    result = device_caps.arch_unsupported(torch)
    assert result is not None
    assert "gfx1100" in result[0]
    assert "HSA_OVERRIDE_GFX_VERSION=11.0.0" in result[0]


def test_an_unparseable_hsa_override_is_left_alone(monkeypatch):
    """The user asked for something we don't understand; guessing is worse
    than trusting them."""
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "something-custom")
    torch = _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
                   arch_list=["gfx900"])
    assert device_caps.arch_unsupported(torch) is None


def test_hsa_override_parsing_round_trips():
    for target in ("gfx1100", "gfx1030", "gfx906", "gfx900"):
        assert device_caps.gfx_for_hsa_override(
            device_caps.hsa_override_for(target)
        ) == target
    for junk in ("garbage", "11.0", "", "11.0.0.0", "a.b.c"):
        assert device_caps.gfx_for_hsa_override(junk) is None


def test_cuda_path_unchanged():
    """Blackwell sm_120 on a ≤sm_90 build is still reported unsupported (#756)."""
    torch = _torch(capability=(12, 0), arch_list=["sm_80", "sm_86", "sm_90"])
    assert device_caps.arch_unsupported(torch) == ("sm_120", ("sm_80", "sm_86", "sm_90"))
    ok = _torch(capability=(8, 6), arch_list=["sm_80", "sm_86", "sm_90"])
    assert device_caps.arch_unsupported(ok) is None


def test_compute_tag_still_matches():
    torch = _torch(capability=(12, 0), arch_list=["sm_90", "compute_120"])
    assert device_caps.arch_unsupported(torch) is None


def test_empty_arch_list_and_broken_metadata_fail_open():
    assert device_caps.arch_unsupported(_torch(hip="6.2", arch_list=[])) is None

    broken = _torch(hip="6.2", arch_list=ROCM_ARCHS)
    broken.cuda.get_device_properties = lambda i=0: (_ for _ in ()).throw(
        RuntimeError("HIP driver died")
    )
    assert device_caps.arch_unsupported(broken) is None


# ── consumer 1: the probe's kernel-risk note ─────────────────────────────


def test_probe_emits_no_kernel_risk_note_on_supported_rocm_host(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "torch",
                        _torch(hip="7.2.4", arch_list=ROCM_ARCHS))
    caps = device_caps.refresh()
    assert caps.family == "rocm"
    assert not any(KERNEL_RISK_MARKER in n for n in caps.notes), caps.notes


# ── consumer 2: get_best_device() / check_device_compatibility() ─────────


def test_rocm_host_routes_to_gpu_not_cpu(monkeypatch):
    """End-to-end: the reporter's host must resolve to "cuda", not "cpu"."""
    import services.model_manager as mm

    torch = _torch(hip="7.2.4", arch_list=ROCM_ARCHS)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: types.SimpleNamespace(family="rocm"),
    )
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)

    assert mm.check_device_compatibility() == (True, None)
    assert mm.get_best_device() == "cuda"


def test_rocm_warning_names_the_rocm_remedy(monkeypatch):
    """A genuine ROCm mismatch must not tell the user to install a CUDA wheel."""
    import services.model_manager as mm

    torch = _torch(hip="6.2", capability=(10, 1), gcn_arch="gfx1010",
                   arch_list=["gfx1030", "gfx1100"])
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    compatible, warning = mm.check_device_compatibility()
    assert compatible is False
    assert "HSA_OVERRIDE_GFX_VERSION" in warning
    assert "cu128" not in warning


# ── consumer 3: the HSA override is a fallback, not a rewrite ───────────


def test_native_support_skips_the_hsa_override(monkeypatch):
    """gfx1151 is native on ROCm 7.x — overriding it onto gfx1100 would force a
    supported GPU onto foreign kernels."""
    import services.model_manager as mm

    mm._configure_rocm_if_needed(_torch(hip="7.2.4", arch_list=ROCM_ARCHS))
    assert "HSA_OVERRIDE_GFX_VERSION" not in __import__("os").environ


def test_override_applied_when_build_lacks_the_arch(monkeypatch):
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(hip="6.2", capability=(11, 2), gcn_arch="gfx1102",
               device_name="AMD Radeon RX 7600",
               arch_list=["gfx1030", "gfx1100"])
    )
    assert os.environ.get("HSA_OVERRIDE_GFX_VERSION") == "11.0.0"


def test_no_override_when_the_target_is_also_missing():
    """Review finding (#1230): pointing HSA_OVERRIDE_GFX_VERSION at an arch the
    build doesn't ship is not a fix. Leave it unset so the compatibility check
    reports the real mismatch and the CPU fallback engages."""
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
               device_name="AMD Radeon 8060S", arch_list=["gfx900", "gfx1030"])
    )
    assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ


def test_unknown_arch_metadata_never_triggers_a_remap():
    """Review finding (#1230): an EMPTY arch list means the build's metadata is
    unavailable, not that the GPU is unsupported. Treating unknown as a
    confirmed mismatch would push a natively-supported gfx1151 onto foreign
    gfx1100 kernels. Fail open."""
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(hip="7.2.4", capability=(11, 5), gcn_arch="gfx1151",
               device_name="AMD Radeon 8060S", arch_list=[])
    )
    assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ


def test_nvidia_never_gets_an_hsa_override():
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(capability=(8, 9), device_name="NVIDIA GeForce RTX 4090",
               arch_list=["sm_89"])
    )
    assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ


# ── #3418: RDNA4 (RX 9060 / 9070, gfx1200 / gfx1201) ────────────────────

# torch 2.8.0's ROCm wheels (the pinned rocm6.4 index) are built with
# PYTORCH_ROCM_ARCH including gfx1200;gfx1201 (.ci/docker/manywheel/build.sh).
ROCM64_ARCHS = ["gfx900", "gfx906", "gfx908", "gfx90a", "gfx942", "gfx1030",
                "gfx1100", "gfx1101", "gfx1102", "gfx1200", "gfx1201"]
PRE_RDNA4_ARCHS = ["gfx900", "gfx906", "gfx90a", "gfx1030", "gfx1100"]


@pytest.mark.parametrize("gfx", ["gfx1200", "gfx1201"])
def test_rdna4_is_native_on_the_pinned_rocm_build(gfx):
    torch = _torch(hip="6.4", capability=(12, int(gfx[-1])), gcn_arch=gfx,
                   device_name="AMD Radeon RX 9070 XT", arch_list=ROCM64_ARCHS)
    assert device_caps.arch_unsupported(torch) is None


@pytest.mark.parametrize("gfx", ["gfx1200", "gfx1201"])
def test_rdna4_never_gets_an_hsa_override(gfx):
    """No remap exists for gfx12: neither a native build nor an old one may
    point an RX 9070 at gfx1100 kernels."""
    import os

    import services.model_manager as mm

    for archs in (ROCM64_ARCHS, PRE_RDNA4_ARCHS):
        mm._configure_rocm_if_needed(
            _torch(hip="6.4", capability=(12, 1), gcn_arch=gfx,
                   device_name="AMD Radeon RX 9070 XT", arch_list=archs)
        )
        assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ


def test_rdna4_on_an_old_build_is_a_mismatch_with_no_remap_advice(monkeypatch):
    """The reported failure mode: a build without gfx12 must say "install a build
    that lists it", never "set HSA_OVERRIDE_GFX_VERSION=11.0.0"."""
    import services.model_manager as mm

    torch = _torch(hip="6.2", capability=(12, 1), gcn_arch="gfx1201",
                   device_name="AMD Radeon RX 9070 XT", arch_list=PRE_RDNA4_ARCHS)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    assert device_caps.arch_unsupported(torch) == ("gfx1201", tuple(PRE_RDNA4_ARCHS))
    compatible, warning = mm.check_device_compatibility()
    assert compatible is False
    assert "11.0.0" not in warning
    assert "No HSA_OVERRIDE_GFX_VERSION remap can help" in warning
    assert "gfx1201" in warning and "rocm6.4" in warning


def test_remap_advice_names_a_same_generation_target_the_build_ships(monkeypatch):
    import services.model_manager as mm

    torch = _torch(hip="6.2", capability=(10, 1), gcn_arch="gfx1010",
                   arch_list=["gfx906", "gfx1030", "gfx1100"])
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    _, warning = mm.check_device_compatibility()
    assert "HSA_OVERRIDE_GFX_VERSION=10.3.0" in warning
    assert "11.0.0" not in warning


def test_every_override_stays_within_its_gfx_generation():
    """Recurrence guard: a cross-generation remap (gfx1201 → gfx1100) cannot run,
    whatever the wheel ships."""
    every = [f"gfx{n}" for n in (900, 902, 906, 908, 1010, 1012, 1030, 1031, 1035,
                                 1100, 1102, 1103, 1150, 1151, 1152, 1200, 1201)]
    for gfx in every:
        target = device_caps.rocm_override_target(gfx, [a for a in every if a != gfx])
        if target is not None:
            assert device_caps.gfx_generation(gfx) == device_caps.gfx_generation(target), (
                gfx, target,
            )
    assert device_caps.rocm_override_target("gfx1201", PRE_RDNA4_ARCHS) is None


def test_gfx_generation_and_siblings():
    assert device_caps.gfx_generation("gfx1201:xnack-") == "12"
    assert device_caps.gfx_generation("gfx90a") == "9"
    assert device_caps.gfx_generation("sm_89") == ""
    assert device_caps.same_generation_targets("gfx1201", PRE_RDNA4_ARCHS) == ()
    assert device_caps.same_generation_targets("gfx1200", ROCM64_ARCHS) == ("gfx1201",)


# ── #3804: the override must land before HIP initialises ─────────────────
# HSA reads HSA_OVERRIDE_GFX_VERSION once, at HIP init. Reproduced on gfx1034:
# set after torch.cuda.is_available(), the card keeps gfx1034 and every kernel
# fails with "invalid device function".


def _kfd_nodes(root, *versions):
    """A KFD topology: 0 is a CPU node, as the kernel reports it."""
    for index, version in enumerate(versions):
        node = root / str(index)
        node.mkdir()
        (node / "properties").write_text(f"cpu_cores_count 8\ngfx_target_version {version}\n")
    return str(root)


def _uninitialised_rocm_torch(arch_flags="gfx1030 gfx1100", hip="6.4"):
    """A ROCm torch that records the override HIP would see when it initialises."""
    import os

    seen = []

    def is_available():
        seen.append(os.environ.get("HSA_OVERRIDE_GFX_VERSION"))
        return False

    torch = _torch(hip=hip)
    torch.cuda.is_available = is_available
    torch.cuda.get_arch_list = lambda: [] if not is_available() else arch_flags.split()
    torch._C = types.SimpleNamespace(_cuda_getArchFlags=lambda: arch_flags)
    return torch, seen


@pytest.mark.parametrize(
    "version, gfx",
    [(100304, "gfx1034"), (110501, "gfx1151"), (90012, "gfx90c"), (90010, "gfx90a"), (0, None)],
)
def test_kfd_target_version_names_the_architecture(version, gfx):
    assert device_caps._gfx_from_target_version(version) == gfx


def test_probe_sets_the_override_before_hip_initialises(monkeypatch, tmp_path):
    """The reported card: gfx1034 on a wheel that ships gfx1030."""
    monkeypatch.setattr(device_caps, "_KFD_NODES", _kfd_nodes(tmp_path, 0, 100304))
    torch, seen = _uninitialised_rocm_torch()
    monkeypatch.setitem(__import__("sys").modules, "torch", torch)

    device_caps._probe()

    assert seen, "the probe never asked torch for the GPU"
    assert seen[0] == "10.3.0"


def test_reading_the_override_inputs_does_not_initialise_hip(monkeypatch, tmp_path):
    monkeypatch.setattr(device_caps, "_KFD_NODES", _kfd_nodes(tmp_path, 0, 100304))
    torch, seen = _uninitialised_rocm_torch()

    assert device_caps.apply_rocm_override_before_init(torch) == "10.3.0"
    assert seen == []


@pytest.mark.parametrize(
    "versions, arch_flags, why",
    [
        ((0, 110000), "gfx1030 gfx1100", "the wheel has this card"),
        ((0, 120001), "gfx1030 gfx1100", "no target in this ISA generation"),
        ((0, 100304), "", "unknown build: never remap on a guess"),
        ((0, 100304, 110001), "gfx1030 gfx1100", "one override cannot describe two cards"),
        ((0,), "gfx1030 gfx1100", "no AMD GPU"),
    ],
)
def test_no_override_when_it_would_not_help(monkeypatch, tmp_path, versions, arch_flags, why):
    import os

    monkeypatch.setattr(device_caps, "_KFD_NODES", _kfd_nodes(tmp_path, *versions))
    torch, _ = _uninitialised_rocm_torch(arch_flags=arch_flags)

    assert device_caps.apply_rocm_override_before_init(torch) is None, why
    assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ


def test_a_user_override_wins_over_the_early_remap(monkeypatch, tmp_path):
    import os

    monkeypatch.setattr(device_caps, "_KFD_NODES", _kfd_nodes(tmp_path, 0, 100304))
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "10.3.1")
    torch, _ = _uninitialised_rocm_torch()

    assert device_caps.apply_rocm_override_before_init(torch) is None
    assert os.environ["HSA_OVERRIDE_GFX_VERSION"] == "10.3.1"


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_the_early_remap_is_linux_only(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(device_caps, "_KFD_NODES", _kfd_nodes(tmp_path, 0, 100304))
    monkeypatch.setattr("sys.platform", platform)
    torch, _ = _uninitialised_rocm_torch()

    assert device_caps.apply_rocm_override_before_init(torch) is None


def test_a_cuda_build_never_takes_the_early_remap(monkeypatch, tmp_path):
    monkeypatch.setattr(device_caps, "_KFD_NODES", _kfd_nodes(tmp_path, 0, 100304))
    torch = _torch(hip=None)
    torch._C = types.SimpleNamespace(_cuda_getArchFlags=lambda: "sm_90")

    assert device_caps.apply_rocm_override_before_init(torch) is None


def test_startup_applies_the_override_before_torchaudio_loads():
    """`main.py` imports torchaudio in `ml_imports`; many modules probe the GPU after."""
    import ast
    from pathlib import Path

    source = (Path(__file__).parent.parent / "backend" / "main.py").read_text(encoding="utf-8")
    startup = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_phase_a_build_inner"
    )
    nodes = list(ast.walk(startup))
    calls = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "apply_rocm_override_before_init"
    ]
    imports = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Import) and any(a.name == "torchaudio" for a in node.names)
    ]
    # prepare_audio_imports can import torchaudio itself on newer versions.
    prepares = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "prepare_audio_imports"
    ]
    assert calls and imports and prepares
    assert min(calls) < min(imports)
    assert min(calls) < min(prepares)


def test_a_launch_choice_beats_the_saved_rocm_choice(monkeypatch, tmp_path):
    """Electron saves ROCm in the env file; otherwise `=cpu` is lost in the backend."""
    from core.user_env import load_into_environ

    env_file = tmp_path / "env"
    env_file.write_text("OMNIVOICE_TORCH_VARIANT=rocm\nHF_HOME=/models\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "cpu")
    monkeypatch.setenv("HF_HOME", "/launcher-default")

    assert load_into_environ(str(env_file))

    assert __import__("os").environ["OMNIVOICE_TORCH_VARIANT"] == "cpu"
    # Everything else keeps the file's documented precedence over the launcher.
    assert __import__("os").environ["HF_HOME"] == "/models"


def test_a_saved_rocm_choice_applies_when_the_launch_sets_none(monkeypatch, tmp_path):
    from core.user_env import load_into_environ

    env_file = tmp_path / "env"
    env_file.write_text("OMNIVOICE_TORCH_VARIANT=rocm\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT")

    assert load_into_environ(str(env_file))

    assert __import__("os").environ["OMNIVOICE_TORCH_VARIANT"] == "rocm"
