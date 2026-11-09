"""#1804: a GPU below the engine's VRAM floor was budgeted as fast hardware.

Third report of one shape. #1226 (GTX 1650 Ti), #1222 (Quadro P2000) and now
#1804 (GTX 1650) are all 4 GB cards running the `omnivoice` engine, which
declares `min_vram_gb = 6.0` precisely because "below this the driver pages to
system RAM and a render that should take seconds runs for minutes".

The reporter's own breadcrumbs show the budget, not the hardware, ending the
job::

    11:53:02 generate:start → 11:59:14 stream-retryable-abort  = 372 s
    12:00:15 generate:start → 12:05:16 stream-retryable-abort  = 301 s

which is `generate_timeout_s()` exactly: 300 + max(0, len - 1200) / 40.

Everything downstream of the routing verdict already acted on it — the caveat
(`resolve_routing`), the preflight toast, the timeout message naming the card
(`test_low_vram_advisory.py`). The budget alone did not, so the slowest
supported configuration got **half** the 600 s a plain CPU host gets. These
tests pin the corrected floor, and the boundaries it must not cross.
"""
from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def clean_app_modules():
    for name in ("core.config", "core.device_caps", "services.engine_routing",
                 "services.tts_backend", "services.model_manager"):
        if name in sys.modules and getattr(sys.modules[name], "__file__", None) is None:
            sys.modules.pop(name)


@pytest.fixture
def engine():
    return importlib.import_module("services.tts_backend").OmniVoiceBackend


@pytest.fixture
def floor(engine):
    return engine.min_vram_gb


@pytest.fixture
def model_manager(fresh_model_manager):
    return fresh_model_manager()


def _gpu(vram_gb: float, *, family: str = "cuda",
         name: str = "NVIDIA GeForce GTX 1650"):
    return importlib.import_module("core.device_caps").HostCaps(
        family=family,
        available_families=(family, "cpu"),
        device_name=name,
        vram_gb=vram_gb,
    )


@pytest.fixture
def on_host(monkeypatch, model_manager):
    """Pin the host probe and the two budgets to their shipped defaults."""
    caps = importlib.import_module("core.device_caps")

    def _pin(host):
        monkeypatch.setattr(caps, "detect_host_caps", lambda: host)
        monkeypatch.setattr(model_manager, "GPU_JOB_TIMEOUT_S", 300.0)
        monkeypatch.setattr(model_manager, "CPU_JOB_TIMEOUT_S", 600.0)
        return model_manager

    return _pin


# ── the reported bug ─────────────────────────────────────────────────────


def test_a_4gb_card_gets_the_cpu_budget_not_half_of_it(on_host, floor):
    """The regression. A card that pages to system RAM performs like a CPU, so
    it must not be budgeted like a 24 GB one."""
    mm = on_host(_gpu(4.0))
    assert mm.generate_timeout_s(
        "A short render", execution_device="cuda", min_vram_gb=floor,
    ) == 600.0


def test_the_engine_alone_is_enough_to_derive_the_floor(on_host, engine):
    """Callers that pass `engine=` (tts_stream, batch, dub, openai_compat,
    archetypes) need no change — the floor is read off the engine."""
    mm = on_host(_gpu(4.0))
    assert mm.generate_timeout_s(
        "A short render", execution_device="cuda", engine=engine,
    ) == 600.0


def test_length_scaling_still_applies_on_top_of_the_raised_floor(on_host, floor):
    """The reporter's longer take (4080 chars ⇒ 372 s before) keeps its bonus;
    the floor moved, the slope did not."""
    mm = on_host(_gpu(4.0))
    assert mm.generate_timeout_s(
        "x" * 4080, execution_device="cuda", min_vram_gb=floor,
    ) == 600.0 + (4080 - 1200) / 40.0


def test_the_whole_class_not_just_cuda(on_host, floor):
    """ROCm is the other dedicated-VRAM family; the same paging happens there."""
    mm = on_host(_gpu(4.0, family="rocm", name="AMD Radeon RX 6500 XT"))
    assert mm.generate_timeout_s(
        "A short render", execution_device="rocm", min_vram_gb=floor,
    ) == 600.0


@pytest.mark.parametrize("family", ["xpu", "vulkan"])
def test_other_discrete_gpu_families_get_the_cpu_budget(
    family, on_host, floor,
):
    mm = on_host(_gpu(4.0, family=family, name="Discrete GPU"))
    assert mm.generate_timeout_s(
        "A short render",
        execution_device="vulkan",
        min_vram_gb=floor,
        hardware_family=family,
    ) == 600.0


def test_xpu_runtime_reaches_dedicated_vram_budget_guard(on_host, floor):
    mm = on_host(_gpu(4.0, family="xpu", name="Intel Arc"))
    assert mm.generate_timeout_s(
        "A short render", execution_device="xpu", min_vram_gb=floor,
    ) == 600.0


def test_vulkan_on_a_small_dedicated_gpu_gets_the_cpu_budget(on_host, floor):
    mm = on_host(_gpu(4.0))
    assert mm.generate_timeout_s(
        "A short render", execution_device="vulkan", min_vram_gb=floor,
    ) == 600.0


def test_native_hardware_family_overrides_global_cpu_preference(on_host, floor):
    mm = on_host(_gpu(4.0, family="cpu", name="NVIDIA GTX 1650"))
    assert mm.generate_timeout_s(
        "A short render",
        execution_device="vulkan",
        min_vram_gb=floor,
        hardware_family="cuda",
    ) == 600.0


# ── the boundaries it must not cross ─────────────────────────────────────


def test_a_large_card_keeps_the_accelerated_budget(on_host, floor):
    mm = on_host(_gpu(24.0, name="NVIDIA RTX 4090"))
    assert mm.generate_timeout_s(
        "A short render", execution_device="cuda", min_vram_gb=floor,
    ) == 300.0


def test_an_engine_with_no_declared_floor_is_never_judged(on_host):
    """Only engines with a measured figure opt in; inventing a floor for the
    rest would silently double the watchdog for every other engine."""
    mm = on_host(_gpu(4.0))
    assert mm.generate_timeout_s("A short render", execution_device="cuda") == 300.0


def test_mps_is_not_judged_by_a_cuda_measured_floor(on_host, floor):
    """`HostCaps.vram_gb` on MPS is system RAM / 2 for a UNIFIED pool — an 8 GB
    Mac reports 4.0 and runs this engine fine."""
    mm = on_host(_gpu(4.0, family="mps", name="Apple Silicon (MPS)"))
    assert mm.generate_timeout_s(
        "A short render", execution_device="mps", min_vram_gb=floor,
    ) == 300.0


def test_a_failed_vram_probe_does_not_guess(on_host, floor):
    """vram_gb == 0 means the probe failed, not that the card has no memory."""
    mm = on_host(_gpu(0.0))
    assert mm.generate_timeout_s(
        "A short render", execution_device="cuda", min_vram_gb=floor,
    ) == 300.0


def test_native_runtime_with_unknown_dedicated_vram_gets_cpu_budget(
    on_host, floor,
):
    """A native runtime's explicit zero means its own VRAM probe failed.

    Keep the warning quiet because capacity is unknown, but allow enough time
    for a device that may page to system memory instead of assuming fast-GPU
    performance.
    """
    mm = on_host(_gpu(24.0, name="NVIDIA RTX 4090"))
    assert mm.generate_timeout_s(
        "A short render",
        execution_device="vulkan",
        min_vram_gb=floor,
        hardware_family="cuda",
        vram_gb=0.0,
    ) == 600.0


def test_a_cpu_fallback_render_is_unaffected(on_host, floor):
    """Routing already sent this one to the CPU; it gets the CPU budget by the
    device branch, and the VRAM branch must not double-apply."""
    mm = on_host(_gpu(4.0))
    assert mm.generate_timeout_s(
        "A short render", execution_device="cpu", min_vram_gb=floor,
    ) == 600.0


# ── an operator's explicit setting still wins ────────────────────────────


def test_an_explicit_universal_budget_is_honoured_verbatim(monkeypatch, floor):
    """Same contract the CPU branch has kept since #1787: someone who lowered
    the watchdog to fail fast keeps failing fast, small card or not."""
    caps = importlib.import_module("core.device_caps")
    mm_mod = importlib.import_module("services.model_manager")

    # `monkeypatch.context()` so the environment is restored BEFORE the reload
    # below (CodeRabbit on the PR). Deleting the var by hand and reloading
    # inside a `finally` leaves the module constants describing an environment
    # pytest is about to put back, and every later test reads the mismatch.
    with monkeypatch.context() as m:
        m.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "123.5")
        mm = importlib.reload(mm_mod)
        m.setattr(caps, "detect_host_caps", lambda: _gpu(4.0))
        assert mm.generate_timeout_s(
            "A short render", execution_device="cuda", min_vram_gb=floor,
        ) == 123.5
    importlib.reload(mm_mod)


def test_a_raised_accelerated_budget_is_never_cut_down_to_the_cpu_one(
    monkeypatch, model_manager, floor):
    """The floor is a `max`, not an assignment."""
    caps = importlib.import_module("core.device_caps")

    monkeypatch.setattr(caps, "detect_host_caps", lambda: _gpu(4.0))
    monkeypatch.setattr(model_manager, "GPU_JOB_TIMEOUT_S", 900.0)
    monkeypatch.setattr(model_manager, "CPU_JOB_TIMEOUT_S", 600.0)
    assert model_manager.generate_timeout_s(
        "A short render", execution_device="cuda", min_vram_gb=floor,
    ) == 900.0


# ── the verdict cannot drift from the warning built on it ────────────────


@pytest.mark.parametrize(
    "vram_gb, family, expected",
    [
        (4.0, "cuda", True),
        (24.0, "cuda", False),
        (0.0, "cuda", False),
        (4.0, "mps", False),
        (4.0, "rocm", True),
    ],
)
def test_the_budget_and_the_caveat_read_the_same_verdict(vram_gb, family, expected, floor):
    """One predicate, three consumers (caveat, timeout message, budget). Three
    inline copies is how the budget came to disagree with the warning printed
    next to it."""
    caps = _gpu(vram_gb, family=family)
    routing = importlib.import_module("services.engine_routing")
    assert routing.under_provisioned_vram(caps, floor) is expected
    assert bool(routing._caveat(caps, floor)) is expected


def test_an_undeclared_floor_and_a_missing_probe_are_both_silent(floor):
    under_provisioned_vram = importlib.import_module("services.engine_routing").under_provisioned_vram
    assert under_provisioned_vram(_gpu(4.0), 0.0) is False
    assert under_provisioned_vram(None, floor) is False


# ── the pairing, repo-wide ───────────────────────────────────────────────


def test_every_dispatch_that_tells_the_guard_its_floor_tells_the_budget_too():
    """The class, not the instance.

    `/generate` was the reported one and `/convert` had the identical split
    (CodeRabbit on the PR): both handed the guard `min_vram_gb` so a timeout
    could name the card, and both computed the budget without it. Rather than
    one test per router, this is the rule — if a dispatch knows the engine's
    floor well enough to explain the timeout, it knows it well enough to set
    the budget. A new router that forgets fails here.
    """
    import ast
    import pathlib

    routers = pathlib.Path(__file__).resolve().parents[1] / "backend" / "api" / "routers"
    offenders = []
    for path in sorted(routers.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            kw = {k.arg: k.value for k in call.keywords if k.arg}
            if "min_vram_gb" not in kw or "timeout" not in kw:
                continue
            # The SAME floor, not merely some floor: a budget computed with 0
            # or another engine's figure would otherwise pass while the guard
            # used the right one (CodeRabbit on the PR).
            wanted = ast.dump(kw["min_vram_gb"])
            if not any(
                isinstance(n, ast.keyword) and n.arg == "min_vram_gb"
                and ast.dump(n.value) == wanted
                for n in ast.walk(kw["timeout"])
            ):
                offenders.append(f"{path.name}:{call.lineno}")
    assert not offenders, (
        "these dispatches tell the guard the engine's VRAM floor but judge the "
        f"job by a budget that ignores it (#1804): {offenders}"
    )


def test_both_budgets_explicit_leaves_the_accelerated_one_in_charge(monkeypatch, floor):
    """The precedence `docs/performance.md` documents, pinned.

    Filling in BOTH Settings rows is the case #1787 had to disambiguate for CPU
    hosts. For an under-provisioned GPU the accelerated value wins: the host is
    still accelerated, so the number the user chose for accelerated hosts is
    used verbatim rather than floored — which is what keeps "lower it to fail
    fast everywhere" working.
    """
    caps = importlib.import_module("core.device_caps")
    mm_mod = importlib.import_module("services.model_manager")

    with monkeypatch.context() as m:  # see the note on the test above
        m.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "200")
        m.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "600")
        mm = importlib.reload(mm_mod)
        m.setattr(caps, "detect_host_caps", lambda: _gpu(4.0))
        assert mm.generate_timeout_s(
            "A short render", execution_device="cuda", min_vram_gb=floor,
        ) == 200.0
        # …while a CPU dispatch still gets the CPU row it was told it would.
        assert mm.generate_timeout_s(
            "A short render", execution_device="cpu",
        ) == 600.0
    importlib.reload(mm_mod)


def test_collection_does_not_bind_replaced_application_modules(monkeypatch):
    import runpy
    from types import ModuleType

    with monkeypatch.context() as patch:
        for name in ("core.device_caps", "services.engine_routing", "services.tts_backend"):
            patch.setitem(sys.modules, name, ModuleType(name))
        runpy.run_path(__file__)
