"""Zero-touch Vulkan sizing for OmniVoice GGUF: adapter discovery, APU shared
memory, multi-GPU numbering, quant tiers, CPU lock, overrides, promotion and
the weight download."""
import importlib
from types import SimpleNamespace

import pytest

GIB = 1024 ** 3


def _device(index, name, kind, vendor, heaps, uuid, bf16=None):
    """One ``GPU<n>:`` section of real ``vulkaninfo`` output (abridged).

    ``bf16`` None prints no extension list (like ``--summary``); True / False
    lists the device extensions with / without ``VK_KHR_shader_bfloat16``."""
    lines = [
        f"GPU{index}:",
        "VkPhysicalDeviceProperties:",
        "---------------------------",
        "\tapiVersion        = 1.3.292 (4206884)",
        f"\tvendorID          = {vendor}",
        f"\tdeviceType        = PHYSICAL_DEVICE_TYPE_{kind}",
        f"\tdeviceName        = {name}",
        "VkPhysicalDeviceIDProperties:",
        f"\tdeviceUUID      = {uuid}",
        "VkPhysicalDeviceMemoryProperties:",
        f"\tmemoryHeaps: count = {len(heaps)}",
    ]
    for i, (size, local) in enumerate(heaps):
        lines += [
            f"\t\tmemoryHeaps[{i}]:",
            f"\t\t\tsize   = {size} ({hex(size)}) ({size / GIB:.2f} GiB)",
            f"\t\t\tbudget = {size} ({hex(size)})",
            "\t\t\tflags:",
            "\t\t\t\tMEMORY_HEAP_DEVICE_LOCAL_BIT" if local else "\t\t\t\tNone",
        ]
    lines += ["\tmemoryTypes: count = 2", "\t\tmemoryTypes[0]:", "\t\t\theapIndex = 0"]
    if bf16 is not None:
        lines += ["Device Extensions: count = 2",
                  "\tVK_KHR_16bit_storage                   : extension revision 1"]
        if bf16:
            lines += ["\tVK_KHR_shader_bfloat16                 : extension revision 1",
                      "VkPhysicalDeviceShaderBfloat16FeaturesKHR:",
                      "\tshaderBFloat16Type             = true"]
    return "\n".join(lines) + "\n"


LLVMPIPE = _device(9, "llvmpipe (LLVM 17.0.6, 256 bits)", "CPU", "0x10005",
                   [(16 * GIB, True)], "6d657361-3233-2e33-2e30-000000000000")
RADEON_890M = _device(0, "AMD Radeon(TM) 890M Graphics", "INTEGRATED_GPU", "0x1002",
                      [(512 * 1024 ** 2, True), (16 * GIB, False)],
                      "00000000-c400-0000-0000-000000000000")
RX_6700_XT = _device(0, "AMD Radeon RX 6700 XT", "DISCRETE_GPU", "0x1002",
                     [(12868124672, True), (16 * GIB, False)],
                     "00000000-0300-0000-0000-000000000000", bf16=False)
UHD_770 = _device(0, "Intel(R) UHD Graphics 770", "INTEGRATED_GPU", "0x8086",
                  [(int(0.25 * GIB), True)], "8680a780-0400-0000-0002-000000000000")


def _arc(index, name, size_gb, uuid, bf16=None):
    return _device(index, name, "DISCRETE_GPU", "0x8086", [(size_gb * GIB, True)], uuid, bf16)


@pytest.fixture
def vk(monkeypatch):
    """vulkan_auto with the probe inputs replaced; the cache is cleared."""
    mod = importlib.import_module("engines.omnivoice_gguf.vulkan_auto")
    compute = importlib.import_module("engines.omnivoice_gguf.compute")
    for name in ("OMNIVOICE_DEVICE", "OMNIVOICE_GGUF_BACKEND", "OMNIVOICE_GGUF_DEVICE",
                 "GGML_BACKEND", "OMNIVOICE_GGUF_QUANT", "OMNIVOICE_TTS_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.setattr(compute, "binary_has_vulkan_module", lambda bin_dir=None: True)
    state = SimpleNamespace(text=None, ram=31.3, loader=False, gpus=())
    monkeypatch.setattr(mod, "_run_vulkaninfo", lambda: state.text)
    monkeypatch.setattr(mod, "system_ram_gb", lambda: state.ram)
    monkeypatch.setattr(mod, "has_vulkan_loader", lambda: state.loader)
    inventory = importlib.import_module("core.gpu_inventory")
    monkeypatch.setattr(inventory, "detect_host_gpus", lambda: state.gpus)
    compute._promotion_for.cache_clear()
    mod.detect_vulkan_profile.cache_clear()
    yield SimpleNamespace(mod=mod, compute=compute, state=state)
    compute._promotion_for.cache_clear()
    mod.detect_vulkan_profile.cache_clear()


def _auto_base():
    backend = importlib.import_module("engines.omnivoice_gguf.backend")
    return backend._automatic_entry(backend._load_quant_map())["base"]


def test_ryzen_ai_890m_apu_uses_shared_memory(vk):
    """A 512 MB frame buffer on a 31.3 GB host is budgeted on half the RAM.
    Native BF16 is not confirmed (no extension list), so the pick is Q8_0."""
    vk.state.text = RADEON_890M + LLVMPIPE
    adapter = vk.compute.selected_vulkan_adapter()
    assert adapter.name == "AMD Radeon(TM) 890M Graphics"
    assert adapter.ggml_name == "Vulkan0"
    assert adapter.dedicated_vram_gb == 0.5
    assert adapter.shared_memory is True
    assert adapter.effective_vram_gb >= 15
    assert adapter.bf16 is None
    assert vk.compute.automatic_compute_class() == "mid-vram"
    assert _auto_base() == "omnivoice-base-Q8_0.gguf"


def test_apu_on_a_small_host_keeps_its_dedicated_budget(vk):
    """Below 16 GB of RAM the shared-memory rule does not apply."""
    vk.state.text = RADEON_890M
    vk.state.ram = 8.0
    assert vk.compute.selected_vulkan_adapter().effective_vram_gb == 0.5
    assert _auto_base() == "omnivoice-base-Q4_K_M.gguf"


def test_rx_6700_xt_without_native_bf16_lands_on_q8(vk):
    """RDNA 2 has no bfloat16 shaders; emulated BF16 ran 1.8x slower there."""
    vk.state.text = RX_6700_XT
    adapter = vk.compute.selected_vulkan_adapter()
    assert adapter.device_type == "DISCRETE_GPU"
    assert adapter.shared_memory is False
    assert adapter.effective_vram_gb == pytest.approx(11.98, abs=0.01)
    assert adapter.bf16 is False
    assert _auto_base() == "omnivoice-base-Q8_0.gguf"


@pytest.mark.parametrize("bf16,base", [(True, "omnivoice-base-BF16.gguf"),
                                       (False, "omnivoice-base-Q8_0.gguf"),
                                       (None, "omnivoice-base-Q8_0.gguf")])
def test_bf16_needs_native_bfloat16_shaders(vk, bf16, base):
    """12 GB+ gets BF16 only when the device reports VK_KHR_shader_bfloat16."""
    vk.state.text = _arc(0, "Intel(R) Arc(TM) B580 Graphics", 12,
                         "8680a056-0800-0000-0300-000000000000", bf16)
    assert vk.compute.selected_vulkan_adapter().bf16 is bf16
    assert _auto_base() == base


def test_bf16_feature_flag_wins_over_the_extension_name(vk):
    """An extension listed with its feature off is not native BF16."""
    vk.state.text = _arc(0, "Intel(R) Arc(TM) A770 Graphics", 16,
                         "8680a056-0800-0000-0300-000000000000", True).replace(
        "shaderBFloat16Type             = true", "shaderBFloat16Type             = false")
    assert vk.compute.selected_vulkan_adapter().bf16 is False
    assert _auto_base() == "omnivoice-base-Q8_0.gguf"


def test_os_inventory_never_assumes_bf16(vk):
    vk.state.loader = True
    vk.state.gpus = (SimpleNamespace(vendor="amd", name="AMD Radeon RX 6700 XT",
                                     discrete=True, vram_gb=12.0),)
    adapter = vk.compute.selected_vulkan_adapter()
    assert (adapter.effective_vram_gb, adapter.bf16) == (12.0, None)
    assert _auto_base() == "omnivoice-base-Q8_0.gguf"


@pytest.mark.parametrize("card,size_gb", [("Intel(R) Arc(TM) A770 Graphics", 16),
                                          ("Intel(R) Arc(TM) B580 Graphics", 12)])
def test_discrete_arc_beats_the_igpu_at_gpu0(vk, monkeypatch, card, size_gb):
    """UHD at GPU0 and Arc at GPU1: GGML opens the discrete card, Vulkan1."""
    vk.state.text = UHD_770 + _arc(1, card, size_gb, "8680a056-0800-0000-0300-000000000000")
    adapter = vk.compute.selected_vulkan_adapter()
    assert (adapter.name, adapter.ggml_name) == (card, "Vulkan1")
    assert _auto_base() == "omnivoice-base-Q8_0.gguf"
    # The explicit device index names the same adapter.
    monkeypatch.setenv("OMNIVOICE_GGUF_BACKEND", "vulkan")
    monkeypatch.setenv("OMNIVOICE_GGUF_DEVICE", "1")
    assert vk.compute.selected_vulkan_adapter().name == card
    monkeypatch.setenv("OMNIVOICE_GGUF_DEVICE", "0")
    assert vk.compute.selected_vulkan_adapter().name == "Intel(R) UHD Graphics 770"


def test_a_second_driver_for_the_same_gpu_is_not_a_second_device(vk):
    """RADV + AMDVLK print one GPU twice; GGML lists it once."""
    vk.state.text = RX_6700_XT + RX_6700_XT.replace("GPU0:", "GPU1:")
    assert len(vk.mod.detect_vulkan_profile().adapters) == 1


@pytest.mark.parametrize("vendor,name", [("amd", "AMD Radeon RX 7600"),
                                         ("intel", "Intel(R) Arc(TM) A750 Graphics")])
def test_8gb_card_from_the_64bit_os_inventory_is_q8(vk, vendor, name):
    """Without vulkaninfo the 64-bit registry / DRM size is used, never the
    32-bit WMI AdapterRAM that wraps an 8 GB card to 4 GB or less."""
    from core.gpu_inventory import HostGPU

    vk.state.loader = True
    vk.state.gpus = (HostGPU(vendor=vendor, name=name, vram_gb=8.0, discrete=True),)
    adapter = vk.compute.selected_vulkan_adapter()
    assert adapter.effective_vram_gb == 8.0
    assert vk.mod.detect_vulkan_profile().source == "os"
    assert _auto_base() == "omnivoice-base-Q8_0.gguf"


def test_os_inventory_needs_a_vulkan_loader(vk):
    from core.gpu_inventory import HostGPU

    vk.state.gpus = (HostGPU(vendor="amd", name="AMD Radeon RX 7600", vram_gb=8.0,
                             discrete=True),)
    assert vk.compute.selected_vulkan_adapter() is None
    assert _auto_base() == "omnivoice-base-Q4_K_M.gguf"


def test_binary_without_the_vulkan_module_stays_unverified(vk, monkeypatch):
    vk.state.text = RX_6700_XT
    monkeypatch.setattr(vk.compute, "binary_has_vulkan_module", lambda bin_dir=None: False)
    assert vk.compute.selected_vulkan_adapter() is None
    assert _auto_base() == "omnivoice-base-Q4_K_M.gguf"


def test_binary_has_vulkan_module_reads_the_bundle(tmp_path):
    compute = importlib.import_module("engines.omnivoice_gguf.compute")
    assert compute.binary_has_vulkan_module(tmp_path) is False
    (tmp_path / "ggml-vulkan.dll").write_bytes(b"MZ")
    assert compute.binary_has_vulkan_module(tmp_path) is True


@pytest.mark.parametrize("variable,value", [("OMNIVOICE_DEVICE", "cpu"),
                                            ("OMNIVOICE_GGUF_BACKEND", "cpu"),
                                            ("GGML_BACKEND", "CPU")])
def test_explicit_cpu_locks_q4_and_blocks_the_gpu_offer(vk, monkeypatch, variable, value):
    vk.state.text = RADEON_890M
    monkeypatch.setenv(variable, value)
    assert vk.compute.selected_vulkan_adapter() is None
    assert _auto_base() == "omnivoice-base-Q4_K_M.gguf"
    assert vk.compute.vulkan_eligible() is False


def test_verified_adapter_is_reported_as_gpu(vk):
    vk.state.text = RADEON_890M
    profile = vk.compute.runtime_profile(SimpleNamespace(gpu_compat=("vulkan", "cpu")), None)
    assert profile["effective_device"] == "vulkan"
    assert profile["routing_status"] == "accelerated"
    assert profile["runtime_device_name"] == "AMD Radeon(TM) 890M Graphics"
    assert profile["runtime_device_verified"] is True


@pytest.fixture
def eligible(vk, monkeypatch, tmp_path):
    """A CPU-only PyTorch host whose bundled runtime is ready, with prefs in
    memory."""
    from core import device_caps, prefs

    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    vk.state.text = RADEON_890M
    store: dict = {}
    monkeypatch.setattr(prefs, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(prefs, "set_", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(prefs, "resolve", lambda key, env=None, default=None:
                        __import__("os").environ.get(env or "") or store.get(key, default))
    monkeypatch.setattr(device_caps, "detect_host_caps",
                        lambda: SimpleNamespace(family="cpu"))
    monkeypatch.setattr(gguf._make_backend_class(), "_binary_status",
                        classmethod(lambda cls: (True, "")))
    monkeypatch.setattr(vk.compute, "_licence_accepted", lambda: True)
    quants = importlib.import_module("engines.omnivoice_gguf.quants")
    downloads = []
    monkeypatch.setattr(quants, "start_download",
                        lambda value=None: downloads.append(value) or {"status": "started"})
    monkeypatch.setattr(quants, "installed_pair", lambda *args: None)
    vk.store, vk.downloads = store, downloads
    vk.offer = importlib.import_module("engines.omnivoice_gguf.gpu_offer")
    return vk


def test_apu_gets_the_gpu_offer_instead_of_a_silent_switch(eligible):
    from services import tts_backend

    status = eligible.offer.offer_status()
    assert status["show"] is True
    assert status["gpu_name"] == "AMD Radeon(TM) 890M Graphics"
    assert status["shared_memory"] is True
    assert (status["quant"], status["download_mb"]) == ("Q8_0", 945)
    # Nothing changes until the user answers.
    assert tts_backend.active_backend_id() == "omnivoice"
    assert eligible.downloads == []


def test_accepting_switches_engine_and_downloads(eligible):
    from services import tts_backend

    eligible.offer.respond(True)
    assert tts_backend.active_backend_id() == "omnivoice-gguf"
    assert eligible.store["gguf_vulkan_offer"] == "accepted"
    assert eligible.downloads == [None]
    assert eligible.offer.offer_status()["show"] is False


def test_declining_is_remembered_and_keeps_the_engine(eligible):
    from services import tts_backend

    eligible.offer.respond(False)
    assert tts_backend.active_backend_id() == "omnivoice"
    assert eligible.downloads == []
    status = eligible.offer.offer_status()
    assert (status["show"], status["eligible"], status["answer"]) == (False, True, "declined")


def test_accepting_needs_the_model_licence_first(eligible, monkeypatch):
    from services import tts_backend
    from services.model_acceptance import ModelLicenceNotAccepted

    def refuse(cls):
        raise ModelLicenceNotAccepted([])

    monkeypatch.setattr(tts_backend, "ensure_engine_licence", refuse)
    with pytest.raises(ModelLicenceNotAccepted):
        eligible.offer.respond(True)
    assert "tts_backend" not in eligible.store
    assert eligible.downloads == []


def test_no_offer_when_the_user_picked_an_engine(eligible):
    eligible.store["tts_backend"] = "kokoro"
    assert eligible.offer.offer_status()["show"] is False


def test_no_offer_when_pytorch_has_a_gpu(eligible, monkeypatch):
    from core import device_caps

    monkeypatch.setattr(device_caps, "detect_host_caps",
                        lambda: SimpleNamespace(family="cuda"))
    assert eligible.offer.offer_status()["eligible"] is False


# ── overrides ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("Q8_0", "omnivoice-base-Q8_0.gguf"), ("bf16", "omnivoice-base-BF16.gguf"),
    ("omnivoice-base-F32.gguf", "omnivoice-base-F32.gguf"), ("auto", None), ("", None),
])
def test_quant_env_override(monkeypatch, value, expected):
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    monkeypatch.setenv("OMNIVOICE_GGUF_QUANT", value)
    assert gguf.env_quant_override() == expected


def test_env_override_beats_settings_and_hardware(vk, monkeypatch):
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    vk.state.text = RX_6700_XT
    monkeypatch.setattr(gguf, "_persisted_quant_override", lambda: "omnivoice-base-F32.gguf")
    engine = gguf.OmniVoiceGGUFBackend()
    assert engine._select_quant_entry()["base"] == "omnivoice-base-F32.gguf"
    monkeypatch.setenv("OMNIVOICE_GGUF_QUANT", "Q4_K_M")
    assert engine._select_quant_entry()["base"] == "omnivoice-base-Q4_K_M.gguf"


# ── quant picker API ────────────────────────────────────────────────────────


@pytest.fixture
def quants(monkeypatch, tmp_path):
    from huggingface_hub import constants

    mod = importlib.import_module("engines.omnivoice_gguf.quants")
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(mod, "installed_pair", lambda *args: None)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    mod._job.update(state="idle", quant=None, error=None)
    monkeypatch.setattr(mod, "_RUNTIME_CACHE", {})
    return mod


def test_download_fetches_the_pair_at_the_pinned_revision(quants, monkeypatch):
    import huggingface_hub

    calls = []
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda **kwargs: calls.append(kwargs) or "path")
    started = []
    monkeypatch.setattr(quants.threading, "Thread", lambda target, args, **kw:
                        SimpleNamespace(start=lambda: started.append(target(*args))))
    result = quants.start_download("Q8_0")
    assert result["status"] == "started"
    revision = importlib.import_module(
        "engines.omnivoice_gguf.backend")._load_quant_map()["_meta"]["source_commit_sha"]
    assert [c["filename"] for c in calls] == ["omnivoice-base-Q8_0.gguf",
                                              "omnivoice-tokenizer-Q8_0.gguf"]
    assert {c["revision"] for c in calls} == {revision}
    assert {c["repo_id"] for c in calls} == {"Serveurperso/OmniVoice-GGUF"}
    assert quants._job["state"] == "done"


def test_download_respects_offline_mode(quants, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert quants.start_download("BF16")["status"] == "failed"


def test_unknown_quant_is_rejected(quants):
    with pytest.raises(ValueError):
        quants.select_quant("Q2_K")
    with pytest.raises(ValueError):
        quants.start_download("Q2_K")


@pytest.mark.parametrize("reason,problem", [
    ("GGUF binary missing at omnivoice-tts-linux-x86_64", "missing"),
    ("omnivoice-tts-linux-x86_64 is not a usable executable", "placeholder"),
    ("checksum mismatch for omnivoice-tts-linux-x86_64", "integrity"),
    ("omnivoice-tts is quarantined by Gatekeeper", "quarantined"),
])
def test_runtime_problem_is_separate_from_weights(quants, monkeypatch, reason, problem):
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    monkeypatch.setattr(gguf._make_backend_class(), "_binary_status",
                        classmethod(lambda cls: (False, reason)))
    assert quants._runtime()["problem"] == problem


def test_runtime_check_is_cached_until_the_binary_changes(quants, monkeypatch, tmp_path):
    """The picker polls status every 1.5 s; the binary is hashed once per file."""
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    binary = tmp_path / "omnivoice-tts"
    binary.write_bytes(b"v1")
    monkeypatch.setattr(gguf, "_binary_path", lambda slug=None: binary)
    calls = []
    monkeypatch.setattr(gguf._make_backend_class(), "_binary_status",
                        classmethod(lambda cls: calls.append(1) or (True, "")))
    assert quants._runtime()["ready"] is True
    assert quants._runtime()["ready"] is True
    assert len(calls) == 1
    binary.write_bytes(b"version 2")  # a reinstall: size changes
    quants._runtime()
    assert len(calls) == 2


def test_bad_backend_pin_does_not_break_the_picker(vk, quants, monkeypatch):
    """An invalid OMNIVOICE_GGUF_BACKEND is generate's error, not a 500 here."""
    vk.state.text = RX_6700_XT
    monkeypatch.setenv("OMNIVOICE_GGUF_BACKEND", "vulkan")
    monkeypatch.setenv("OMNIVOICE_GGUF_DEVICE", "not-a-number")
    status = quants.quant_status()
    assert status["recommended"] == "omnivoice-base-Q4_K_M.gguf"
    vk.compute._promotion_for.cache_clear()
    assert vk.compute.vulkan_eligible() is False


def test_unknown_env_quant_is_shown_as_itself(vk, quants, monkeypatch):
    vk.state.text = RX_6700_XT
    monkeypatch.setenv("OMNIVOICE_GGUF_QUANT", "BF17")
    status = quants.quant_status()
    assert status["selected"] == "omnivoice-base-BF17.gguf"
    assert status["selection_valid"] is False
    assert not any(o["selected"] for o in status["options"])


def test_no_offer_while_the_environment_pins_another_engine(eligible, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "omnivoice")
    status = eligible.offer.offer_status()
    assert (status["eligible"], status["show"], status["blocked_by_environment"]) == (False, False, True)
    with pytest.raises(ValueError):
        eligible.offer.respond(True)


# ── review fixes ────────────────────────────────────────────────────────────


def test_accepting_does_not_switch_when_the_download_cannot_start(eligible, monkeypatch):
    """Offline: the engine must not point at weights that never arrive."""
    from services import tts_backend

    quants = importlib.import_module("engines.omnivoice_gguf.quants")
    monkeypatch.setattr(quants, "start_download", lambda value=None: {"status": "failed"})
    result = eligible.offer.respond(True)
    assert result["download"]["status"] == "failed"
    assert result["active"] is False
    assert "tts_backend" not in eligible.store
    assert "gguf_vulkan_offer" not in eligible.store
    assert tts_backend.active_backend_id() == "omnivoice"


def test_a_runtime_failure_is_not_cached(quants, monkeypatch, tmp_path):
    """Allowing a quarantined file does not change its identity; re-check it."""
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    binary = tmp_path / "omnivoice-tts"
    binary.write_bytes(b"v1")
    monkeypatch.setattr(gguf, "_binary_path", lambda slug=None: binary)
    answers = iter([(False, "omnivoice-tts is quarantined by Gatekeeper"), (True, "")])
    monkeypatch.setattr(gguf._make_backend_class(), "_binary_status",
                        classmethod(lambda cls: next(answers)))
    assert quants._runtime()["problem"] == "quarantined"
    assert quants._runtime()["ready"] is True


def test_offer_eligibility_follows_a_repaired_runtime(eligible, monkeypatch, tmp_path):
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    binary = tmp_path / "omnivoice-tts"
    binary.write_bytes(b"broken")
    monkeypatch.setattr(gguf, "_binary_path", lambda slug=None: binary)
    ok = {"value": False}
    monkeypatch.setattr(gguf._make_backend_class(), "_binary_status",
                        classmethod(lambda cls: (ok["value"], "")))
    assert eligible.compute.vulkan_eligible() is False
    binary.write_bytes(b"repaired runtime")
    ok["value"] = True
    assert eligible.compute.vulkan_eligible() is True


def test_api_errors_carry_no_exception_text(quants, monkeypatch):
    """Exception messages can hold local paths: the API returns fixed text."""
    import huggingface_hub

    def boom(**kwargs):
        raise OSError("/Users/someone/.cache/huggingface/secret-path")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", boom)
    monkeypatch.setattr(quants.threading, "Thread", lambda target, args, **kw:
                        SimpleNamespace(start=lambda: target(*args)))
    quants.start_download("Q8_0")
    assert quants._job["state"] == "failed"
    assert "secret-path" not in quants._job["error"]
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")

    def unresolvable(self, download=True):
        raise RuntimeError("/Users/someone/models missing")

    monkeypatch.setattr(gguf._make_backend_class(), "_resolve_quant_paths", unresolvable)
    assert quants._in_use("rev") == (None, "weights_unavailable")


def test_accepting_reuses_an_installed_pair_instead_of_downloading(eligible, monkeypatch):
    """Q4_K_M cached, Q8_0 recommended: generation loads Q4_K_M, so no download
    (and it works offline)."""
    from services import tts_backend

    quants = importlib.import_module("engines.omnivoice_gguf.quants")
    monkeypatch.setattr(quants, "_in_use", lambda revision: ("omnivoice-base-Q4_K_M.gguf", None))
    assert eligible.offer.offer_status()["download_mb"] == 0
    result = eligible.offer.respond(True)
    assert result["download"]["status"] == "already_installed"
    assert eligible.downloads == []
    assert tts_backend.active_backend_id() == "omnivoice-gguf"


def test_os_inventory_sizes_the_quant_but_gets_no_offer_or_gpu_budget(vk, monkeypatch):
    """Only a loader and a listed GPU: the driver may still be unable to open it."""
    from core import device_caps
    from core.gpu_inventory import HostGPU

    vk.state.loader = True
    vk.state.gpus = (HostGPU(vendor="amd", name="AMD Radeon RX 7600", vram_gb=8.0, discrete=True),)
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: SimpleNamespace(family="cpu"))
    assert _auto_base() == "omnivoice-base-Q8_0.gguf"
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    profile = vk.compute.runtime_profile(gguf._make_backend_class(), None)
    assert profile["routing_status"] == "cpu_only"
    assert vk.compute.vulkan_eligible() is False


def test_a_refused_offer_is_rechecked_without_a_binary_change(eligible, monkeypatch):
    """A repaired ggml-vulkan library leaves the executable unchanged."""
    gguf = importlib.import_module("engines.omnivoice_gguf.backend")
    ok = {"value": False}
    monkeypatch.setattr(gguf._make_backend_class(), "_binary_status",
                        classmethod(lambda cls: (ok["value"], "")))
    monkeypatch.setattr(eligible.compute, "_binary_identity", lambda: ("same",))
    assert eligible.compute.vulkan_eligible() is False
    ok["value"] = True
    assert eligible.compute.vulkan_eligible() is True


def test_accepting_waits_while_another_quant_is_downloading(eligible, monkeypatch):
    """already_running for a different quant must not switch the engine."""
    from services import tts_backend

    quants = importlib.import_module("engines.omnivoice_gguf.quants")
    monkeypatch.setattr(quants, "start_download", lambda value=None: {
        "status": "already_running", "quant": "omnivoice-base-F32.gguf"})
    result = eligible.offer.respond(True)
    assert result["download"]["status"] == "busy"
    assert "tts_backend" not in eligible.store
    assert tts_backend.active_backend_id() == "omnivoice"
    monkeypatch.setattr(quants, "start_download", lambda value=None: {
        "status": "already_running", "quant": quants.quant_status()["selected"]})
    assert eligible.offer.respond(True)["download"]["status"] == "already_running"
    assert tts_backend.active_backend_id() == "omnivoice-gguf"
