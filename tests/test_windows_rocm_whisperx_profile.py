"""Registered WhisperX HIP admission with fake native metadata and no GPU work."""
import sys
from types import SimpleNamespace

import pytest

HostCaps = ct2 = pipelines = runtime = None
expected_versions = load_recipe = ab = runtime_compute_profile = None


@pytest.fixture(autouse=True)
def app_modules(monkeypatch):
    """Resolve current canonical modules after any earlier suite reloads."""
    from importlib import import_module

    caps = import_module("core.device_caps")
    ct2_module = import_module("core.windows_rocm.ct2")
    pipelines_module = import_module("core.windows_rocm.pipelines")
    runtime_module = import_module("core.windows_rocm.runtime")
    recipe_module = import_module("core.windows_rocm.recipe")
    asr_module = import_module("services.asr_backend")
    routing_module = import_module("services.engine_routing")
    for name, value in {
        "HostCaps": caps.HostCaps, "ct2": ct2_module, "pipelines": pipelines_module,
        "runtime": runtime_module, "expected_versions": recipe_module.expected_versions,
        "load_recipe": recipe_module.load_recipe, "ab": asr_module,
        "runtime_compute_profile": routing_module.runtime_compute_profile,
    }.items():
        monkeypatch.setitem(globals(), name, value)


@pytest.fixture
def qualified(monkeypatch, tmp_path):
    versions = expected_versions(load_recipe()) | {"whisperx": "3.4.5", "pyannote.audio": "3.4.0"}
    monkeypatch.setattr(pipelines, "windows_hip_installed", lambda: True)
    monkeypatch.setattr(pipelines, "validate_target", lambda: None, raising=False)
    original_version = runtime.metadata.version
    monkeypatch.setattr(runtime.metadata, "version", lambda name: versions[name] if name in versions else original_version(name))
    site = tmp_path / "site-packages"
    roots = {name: site / name for name in ("_rocm_sdk_core", "_rocm_sdk_devel")}
    for root in roots.values():
        root.mkdir(parents=True)
    headers = ("hip/hip_runtime.h", "hip/hip_runtime_api.h", "rocrand/rocrand.h",
               "rocrand/rocrand_xorwow.h", "miopen/miopen.h", "miopen/config.h", "rocprim/rocprim.hpp")
    for name in headers:
        header = roots["_rocm_sdk_devel"] / "include" / name
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(runtime, "package_root", roots.get)
    monkeypatch.setattr(runtime.sysconfig, "get_paths", lambda: {"purelib": str(site), "platlib": str(site)})
    for key in ("ROCM_PATH", "ROCM_HOME", "HIP_PATH", "HSA_OVERRIDE_GFX_VERSION",
                "MIOPEN_DISABLE_CACHE", "MIOPEN_USER_DB_PATH", "MIOPEN_SYSTEM_DB_PATH",
                "HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "MIOPEN_FIND_MODE"):
        monkeypatch.delenv(key, raising=False)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for key in ("TMPDIR", "TEMP", "TMP", "MIOPEN_CUSTOM_CACHE_DIR", "XDG_CACHE_HOME",
                "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(key, str(scratch))
    monkeypatch.setattr(runtime.tempfile, "tempdir", str(scratch))
    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"), vram_gb=16)
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: caps)
    torch = SimpleNamespace(__version__=versions["torch"], version=SimpleNamespace(hip="7.2.53211-158bd99533"),
                            cuda=SimpleNamespace(is_available=lambda: True,
                                get_device_properties=lambda index: SimpleNamespace(gcnArchName="gfx1201")))
    monkeypatch.setitem(sys.modules, "torch", torch)
    native_check = ct2.hip_ct2_available
    probes = []
    monkeypatch.setattr(ct2, "hip_ct2_available", lambda: probes.append(True) or True)
    monkeypatch.setattr(ab, "_ctranslate2_execstack_ok", lambda: (True, "ready"))
    monkeypatch.setattr(ab, "_ctranslate2_cudnn_ok", lambda: (True, "ready"))
    monkeypatch.setattr(pipelines, "prepare_audio_imports", lambda: None)
    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace())
    return SimpleNamespace(versions=versions, roots=roots, torch=torch, probes=probes,
                           scratch=scratch, caps=caps, native_check=native_check)


def test_registered_profile_admits_actual_availability(qualified):
    assert ab.WhisperXBackend.is_available()[0]


@pytest.mark.parametrize("instance", [False, True])
def test_shared_routing_reports_qualified_rocm(qualified, instance, monkeypatch):
    monkeypatch.setattr(ab.WhisperXBackend, "_pick_device", staticmethod(lambda: ("cuda", "float16")))
    engine = ab.WhisperXBackend() if instance else ab.WhisperXBackend
    profile = runtime_compute_profile(engine, qualified.caps)
    assert profile["effective_device"] == "rocm"
    assert profile["routing_status"] == "accelerated"
    assert profile["gpu_compat"] == ("cuda", "rocm", "cpu")
    assert ab.WhisperXBackend.gpu_compat == ("cuda", "cpu")


def test_qualified_warm_asr_and_alignment_then_changed_profile_denies(qualified, monkeypatch):
    monkeypatch.setattr(ab.WhisperXBackend, "_pick_device", staticmethod(lambda: ("cuda", "float16")))
    engine = ab.WhisperXBackend()
    model = engine._asr = object()
    aligner = (object(), {})
    monkeypatch.setitem(ab._ALIGN_CACHE, ("fr", "cuda"), aligner)
    monkeypatch.setattr(ab, "_alignment_offline", lambda: True)
    monkeypatch.setattr(ab, "_ensure_alignment_tokenizer", lambda: None)
    engine.ensure_loaded()
    assert ab.load_align_model("fr", "cuda") is aligner
    qualified.versions["torch"] = "2.10.0+rocm7.2.1"
    with pytest.raises(pipelines.PipelineCompatibilityError):
        engine.ensure_loaded()
    assert ab.load_align_model("fr", "cuda") is None
    assert engine._asr is model and ab._ALIGN_CACHE[("fr", "cuda")] is aligner
    assert "rocm" not in ab.WhisperXBackend.runtime_gpu_compat()


@pytest.mark.parametrize("capability", [None, "pyannote-diarization", "unknown", "WhisperXBackend._ensure_asr"])
def test_unknown_and_diarization_capabilities_remain_closed(qualified, capability):
    assert pipelines.pipeline_error("cuda", capability=capability)
    assert not qualified.probes


@pytest.mark.parametrize("capability", ["whisperx-asr", "whisperx-alignment"])
def test_only_named_capabilities_qualify(qualified, capability):
    assert pipelines.pipeline_error("cuda", capability=capability) is None


@pytest.mark.parametrize("package", ["torch", "torchaudio", "rocm-sdk-core", "rocm-sdk-devel", "ctranslate2", "whisperx", "pyannote.audio"])
def test_unknown_versions_reject_before_probe(qualified, package):
    qualified.versions[package] = "unregistered"
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr")
    assert not qualified.probes


@pytest.mark.parametrize("damage", ["no-gpu", "unknown-gpu", "unloaded-torch", "failed-ct2", "missing-header", "wrong-root"])
def test_native_profile_damage_denies(qualified, monkeypatch, damage):
    if damage == "no-gpu":
        qualified.torch.cuda.is_available = lambda: False
    elif damage == "unknown-gpu":
        qualified.torch.cuda.get_device_properties = lambda index: SimpleNamespace(gcnArchName="gfx1200")
    elif damage == "unloaded-torch":
        monkeypatch.delitem(sys.modules, "torch")
    elif damage == "failed-ct2":
        monkeypatch.setattr(ct2, "hip_ct2_available", lambda: False)
    elif damage == "missing-header":
        (qualified.roots["_rocm_sdk_devel"] / "include/rocrand/rocrand_xorwow.h").unlink()
    else:
        monkeypatch.setenv("ROCM_PATH", str(qualified.roots["_rocm_sdk_core"]))
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr")


@pytest.mark.parametrize("name", ["ROCM_PATH", "TEMP", "TMP", "TMPDIR", "MIOPEN_CUSTOM_CACHE_DIR", "XDG_CACHE_HOME"])
def test_unicode_paths_reject_without_native_probe(qualified, monkeypatch, name):
    monkeypatch.setenv(name, str(qualified.scratch / "Za\u017c\u00f3\u0142\u0107"))
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr")
    assert not qualified.probes


@pytest.mark.parametrize("name", ["ROCM_HOME", "HIP_PATH", "HSA_OVERRIDE_GFX_VERSION", "MIOPEN_DISABLE_CACHE", "HIP_VISIBLE_DEVICES"])
def test_unvalidated_overrides_reject_before_probe(qualified, monkeypatch, name):
    monkeypatch.setenv(name, "")
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr")
    assert not qualified.probes


def test_diarization_rejects_before_token_and_warm_reuse(qualified, monkeypatch):
    from services import model_manager as mm, token_resolver

    monkeypatch.setattr(mm, "_diar_pipeline", object())
    monkeypatch.setattr("services.diarization_runtime.selected_backend", lambda: "pyannote")
    calls = []
    def resolve(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("forbidden resolver")
    monkeypatch.setattr(token_resolver, "resolve", resolve)
    assert mm.get_diarization_pipeline(True) == (None, mm.DIARIZATION_ERR_LOAD)
    assert calls == []


def test_compatibility_failure_does_not_poison_deep_imports(qualified, monkeypatch):
    monkeypatch.setattr(ab.WhisperXBackend, "_pick_device", staticmethod(lambda: ("cuda", "float16")))
    engine = ab.WhisperXBackend()
    engine._asr = object()
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda **kwargs: engine)
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kwargs: None)
    monkeypatch.setattr(ab, "_asr_backend_pinned", lambda: True)
    monkeypatch.setattr(ab, "_DEEP_IMPORT_BROKEN", {})
    qualified.versions["whisperx"] = "unknown"
    with pytest.raises(RuntimeError):
        ab.load_active_asr_backend(require_installed=True)
    assert ab._DEEP_IMPORT_BROKEN == {}
    qualified.versions["whisperx"] = "3.4.5"
    assert ab.load_active_asr_backend(require_installed=True) is engine


def test_cpu_and_non_windows_keep_original_semantics(qualified, monkeypatch):
    assert pipelines.pipeline_error("cpu", capability="unknown") is None
    monkeypatch.setattr(pipelines, "windows_hip_installed", lambda: False)
    monkeypatch.setattr(pipelines, "sys", SimpleNamespace(platform="linux", modules=sys.modules))
    assert pipelines.pipeline_error("cuda", capability="unknown") is None
    assert ab.WhisperXBackend.runtime_gpu_compat() == ("cuda", "cpu")


def test_native_cuda_remains_ungated(qualified, monkeypatch):
    monkeypatch.setattr(pipelines, "windows_hip_installed", lambda: False)
    qualified.torch.version.hip = None
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr") is None


def test_stock_ct2_binary_fails_before_native_probe(qualified, monkeypatch):
    monkeypatch.setattr(ct2, "hip_ct2_available", qualified.native_check)
    monkeypatch.setattr(ct2, "validate_target", lambda: None)
    monkeypatch.setattr(ct2, "package_root", lambda package: qualified.scratch)
    monkeypatch.setattr(ct2, "sha256", lambda path: "wrong-dll")
    monkeypatch.setattr(ct2, "verify_environment", lambda: pytest.fail("Wrong DLL must not reach native probe"))
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr")


@pytest.mark.parametrize("damage", ["abi", "recipe", "metadata-error", "cached-temp", "missing-temp", "unicode-sdk", "redirected-header"])
def test_remaining_profile_boundaries_fail_before_probe(qualified, monkeypatch, damage):
    if damage == "abi":
        def invalid_target():
            raise RuntimeError("wrong interpreter")
        monkeypatch.setattr(pipelines, "validate_target", invalid_target)
    elif damage == "recipe":
        monkeypatch.setattr(pipelines, "recipe_identity", lambda: "unregistered recipe")
    elif damage == "metadata-error":
        def inaccessible(name):
            raise OSError("metadata denied")
        monkeypatch.setattr(runtime.metadata, "version", inaccessible)
    elif damage == "cached-temp":
        monkeypatch.setattr(runtime.tempfile, "tempdir", str(qualified.scratch / "\u017b"))
    elif damage == "missing-temp":
        monkeypatch.delenv("TEMP")
    elif damage == "unicode-sdk":
        qualified.roots["_rocm_sdk_devel"] = qualified.scratch / "\u017b" / "_rocm_sdk_devel"
    else:
        original = runtime._ascii_physical_path
        monkeypatch.setattr(runtime, "_ascii_physical_path", lambda path, **kw:
                            False if str(path).endswith("rocrand_xorwow.h") else original(path, **kw))
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr")
    assert not qualified.probes


def test_catalogue_profile_never_initializes_or_expands_sdk(qualified, monkeypatch):
    monkeypatch.setattr(runtime, "initialize_dll_search", lambda: pytest.fail("Catalogue mutated DLL/runtime setup"))
    monkeypatch.setattr(runtime, "import_module", lambda name: pytest.fail("Catalogue expanded SDK"))
    monkeypatch.setattr(runtime.tempfile, "gettempdir", lambda: pytest.fail("Catalogue created a temporary probe file"))
    assert pipelines.pipeline_error("cuda", capability="whisperx-asr") is None
    assert "ROCM_PATH" not in runtime.os.environ


def test_actual_forced_align_uses_qualified_warm_aligner(qualified, monkeypatch):
    segments = [{"text": "bonjour"}]
    output = [{"text": "bonjour", "words": [{"word": "bonjour", "start": 0.0, "end": 0.4}]}]
    monkeypatch.setitem(ab._ALIGN_CACHE, ("fr", "cuda"), (object(), {}))
    monkeypatch.setattr(ab, "_alignment_offline", lambda: True)
    monkeypatch.setattr(ab, "_ensure_alignment_tokenizer", lambda: None)
    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace(align=lambda *args, **kwargs: {"segments": output}))
    assert ab.forced_align(segments, object(), "fr", device="cuda") is output
    qualified.versions["rocm-sdk-devel"] = "unknown"
    assert ab.forced_align(segments, object(), "fr", device="cuda") is segments


def test_catalogue_admits_whisperx_without_changing_auto_preference(qualified, monkeypatch):
    monkeypatch.setattr(ab, "_REGISTRY", {"whisperx": ab.WhisperXBackend})
    for name in ("_DEEP_IMPORT_BROKEN", "_LAST_ERRORS", "_RUNTIME_EVIDENCE", "_RUNTIME_INSTANCES", "_ISOLATED_INSTANCES"):
        monkeypatch.setattr(ab, name, {})
    row, = ab.list_backends()
    assert row["available"] and row["effective_device"] == "rocm"
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_probe_available", lambda cls: True)
    assert ab._auto_detect() == "faster-whisper"
