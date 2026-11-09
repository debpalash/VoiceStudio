"""MOSS-TTS-Nano from its own venv: the sidecar, and the switch to it.

At the pinned upstream commit, `moss_tts_nano` exports only `__version__` and
the entry point is the top-level `moss_tts_nano_runtime.NanoTTSService`. These
tests run the sidecar against a fake of that runtime.
"""
import importlib.util
import io
import json
import re
import struct
import sys
import types
from pathlib import Path

import numpy as np
import pytest

_MAIN = Path(__file__).resolve().parents[1] / "backend/engines/moss_tts_nano_subprocess/main.py"


class _EmptyModel:
    pass


def _load_sidecar(
    monkeypatch, calls, *, sample_rate=48000, channels=2, samples=960,
    model_factory=_EmptyModel, fail_first_synthesis=False,
):
    class NanoTTSService:
        def __init__(self, **kw):
            calls.append(("init", kw))
            self.output_dir = Path(kw["output_dir"])
            self.checkpoint_path = kw["checkpoint_path"]
            self._model = None
            self._failed = False

        def _load_model_locked(self):
            if self._model is None:
                self._model = model_factory()
            return self._model

        def preload(self, **kw):
            calls.append(("preload", kw))
            if kw.get("load_model"):
                self._load_model_locked()

        def get_model(self):
            return self._load_model_locked()

        def synthesize(self, **kw):
            calls.append(("synthesize", kw))
            model = self._load_model_locked()
            if hasattr(model, "load_text_tokenizer"):
                model.load_text_tokenizer()
            if fail_first_synthesis and not self._failed:
                self._failed = True
                self._model = None  # Upstream discards the model on inference errors.
                raise RuntimeError("connection interrupted during inference")
            shape = (samples, channels) if channels > 1 else (samples,)
            return {"waveform_numpy": np.full(shape, 0.25, dtype=np.float32), "sample_rate": sample_rate}

    runtime = types.ModuleType("moss_tts_nano_runtime")
    runtime.NanoTTSService = NanoTTSService
    package = types.ModuleType("moss_tts_nano")
    defaults = types.ModuleType("moss_tts_nano.defaults")
    defaults.DEFAULT_CHECKPOINT_PATH = "OpenMOSS-Team/MOSS-TTS-Nano"
    defaults.DEFAULT_AUDIO_TOKENIZER_PATH = "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano"
    package.defaults = defaults
    monkeypatch.setitem(sys.modules, "moss_tts_nano_runtime", runtime)
    monkeypatch.setitem(sys.modules, "moss_tts_nano", package)
    monkeypatch.setitem(sys.modules, "moss_tts_nano.defaults", defaults)
    spec = importlib.util.spec_from_file_location("_nano_sidecar_under_test", _MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frames(buf):
    data, out, i = buf.getvalue(), [], 0
    while i < len(data):
        (n,) = struct.unpack("!I", data[i:i + 4])
        out.append(json.loads(data[i + 4:i + 4 + n]))
        i += 4 + n
    return out


def test_drives_upstreams_runtime_and_downmixes_to_mono(monkeypatch):
    calls = []
    sidecar = _load_sidecar(monkeypatch, calls)
    out = io.BytesIO()

    sidecar._handle_synthesize({"op": "synthesize", "text": "hello"}, out)

    init = dict(calls)["init"]
    assert init["checkpoint_path"] == "OpenMOSS-Team/MOSS-TTS-Nano"
    assert init["audio_tokenizer_path"] == "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano"
    assert ("preload", {"load_model": True}) in calls
    synth = dict(calls)["synthesize"]
    assert synth["text"] == "hello"
    assert synth["mode"] == "voice_clone"
    assert synth["prompt_audio_path"] is None
    # One output file, reused, inside the temp dir — not one per call.
    assert Path(synth["output_audio_path"]).parent == Path(init["output_dir"])
    audio = _frames(out)[-1]
    assert audio["op"] == "audio"
    assert audio["sample_rate"] == 48000 and audio["n_samples"] == 960


def test_passes_the_reference_clip_and_rejects_a_url(monkeypatch, tmp_path):
    calls = []
    sidecar = _load_sidecar(monkeypatch, calls)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    sidecar._handle_synthesize({"text": "hi", "ref_audio": str(ref)}, io.BytesIO())
    assert dict(calls)["synthesize"]["prompt_audio_path"] == str(ref)
    with pytest.raises(ValueError, match="local file path"):
        sidecar._handle_synthesize({"text": "hi", "ref_audio": "https://x.test/a.wav"}, io.BytesIO())


def test_resamples_to_the_rate_the_engine_reports(monkeypatch):
    sidecar = _load_sidecar(monkeypatch, [], sample_rate=24000, channels=1, samples=960)
    out = io.BytesIO()
    sidecar._handle_synthesize({"text": "hi"}, out)
    audio = _frames(out)[-1]
    assert audio["sample_rate"] == 48000
    assert audio["n_samples"] == 1920


def test_only_the_cold_call_sends_progress(monkeypatch):
    """Progress frames keep the watchdog armed through downloads; a warm call
    sending them would stop it from catching a generation that wedges."""
    sidecar = _load_sidecar(monkeypatch, [])
    first, second = io.BytesIO(), io.BytesIO()
    sidecar._handle_synthesize({"text": "one"}, first)
    sidecar._handle_synthesize({"text": "two"}, second)
    assert any(f["op"] == "progress" for f in _frames(first))
    assert [f["op"] for f in _frames(second)] == ["audio"]


@pytest.mark.parametrize("custom_modules_cache", [False, True])
@pytest.mark.parametrize("fail_first_synthesis", [False, True])
def test_tokenizer_reuses_hub_cache_without_relocating_dynamic_modules(
    monkeypatch, tmp_path, custom_modules_cache, fail_first_synthesis,
):
    """Nano's tokenizer must not nest a new HF cache inside its loaded module.

    Upstream's _load_text_tokenizer calls these two hooks before loading its
    tokenizer. Its default hooks use __file__/.cache/huggingface and change
    HF_MODULES_CACHE, growing Windows paths beyond the short app cache (#3137).
    """
    from huggingface_hub import constants
    from transformers import dynamic_module_utils

    hub_cache = tmp_path / "hub"
    modules_cache = tmp_path / ("custom-modules" if custom_modules_cache else "modules")
    model_code = modules_cache / "transformers_modules" / "MOSS_hyphen_TTS_hyphen_Nano" / ("a" * 40)
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(hub_cache))
    monkeypatch.setattr(dynamic_module_utils, "HF_MODULES_CACHE", str(modules_cache))
    if custom_modules_cache:
        monkeypatch.setenv("HF_MODULES_CACHE", str(modules_cache))
    else:
        monkeypatch.delenv("HF_MODULES_CACHE", raising=False)

    class NanoModel:
        def _resolve_hf_cache_dir(self):
            return str(model_code / ".cache" / "huggingface")

        def _patch_hf_dynamic_module_cache_dir(self, cache_dir):
            nested_modules = str(Path(cache_dir) / "modules")
            monkeypatch.setenv("HF_MODULES_CACHE", nested_modules)
            monkeypatch.setattr(dynamic_module_utils, "HF_MODULES_CACHE", nested_modules)

        def load_text_tokenizer(self):
            cache_dir = self._resolve_hf_cache_dir()
            self._patch_hf_dynamic_module_cache_dir(cache_dir)
            tokenizer = Path(cache_dir) / "tokenizer.model"
            tokenizer.parent.mkdir(parents=True, exist_ok=True)
            tokenizer.write_bytes(b"cached tokenizer")

    sidecar = _load_sidecar(
        monkeypatch, [], model_factory=NanoModel,
        fail_first_synthesis=fail_first_synthesis,
    )
    monkeypatch.setattr(sidecar.time, "sleep", lambda seconds: None)
    out = io.BytesIO()
    sidecar._handle_synthesize({"text": "hello"}, out)
    sidecar._handle_synthesize({"text": "again"}, out)

    assert (hub_cache / "tokenizer.model").is_file()
    assert dynamic_module_utils.HF_MODULES_CACHE == str(modules_cache)
    assert sidecar.os.environ.get("HF_MODULES_CACHE") == (
        str(modules_cache) if custom_modules_cache else None
    )
    assert not (model_code / ".cache").exists()
    assert [frame["op"] for frame in _frames(out)].count("audio") == 2


def _cache_auto_tokenizer(cache, layout, contents="complete"):
    """Write a tiny Hub snapshot that real AutoTokenizer can load offline."""
    from tokenizers import Tokenizer, models, pre_tokenizers

    storage = cache / "models--OpenMOSS-Team--MOSS-TTS-Nano"
    revision = "b" * 40
    snapshot = storage / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (storage / "refs").mkdir()
    (storage / "refs" / "main").write_text(revision, encoding="utf-8")
    config = {"tokenizer_class": "PreTrainedTokenizerFast", "unk_token": "[UNK]"}
    if layout == "bpe":
        config = {"tokenizer_class": "GPT2Tokenizer", "unk_token": "<|endoftext|>"}
    (snapshot / "tokenizer_config.json").write_text(json.dumps(config), encoding="utf-8")
    model_config = {"model_type": "gpt2"} if layout == "bpe" else {"tokenizer_class": "PreTrainedTokenizerFast"}
    (snapshot / "config.json").write_text(json.dumps(model_config), encoding="utf-8")
    (snapshot / "special_tokens_map.json").write_text("{}", encoding="utf-8")
    if contents == "metadata":
        return snapshot
    if layout == "bpe" or contents == "incomplete":
        (snapshot / "vocab.json").write_text(
            json.dumps({"h": 0, "i": 1, "<|endoftext|>": 2}), encoding="utf-8",
        )
        if contents != "incomplete":
            (snapshot / "merges.txt").write_text("#version: 0.2\n", encoding="utf-8")
    else:
        tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "hi": 1}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer.save(str(snapshot / "tokenizer.json"))
    if contents == "model-only":
        # Missing configuration fails before AutoTokenizer can read this payload.
        for path in snapshot.iterdir():
            path.unlink()
        (snapshot / "tokenizer.model").write_bytes(b"cached vocabulary")
    elif contents == "payload-only":
        for name in ("tokenizer_config.json", "config.json", "special_tokens_map.json"):
            (snapshot / name).unlink()
    elif contents == "config-only":
        (snapshot / "tokenizer_config.json").unlink()
    return snapshot


@pytest.mark.parametrize("layout", ["json", "bpe"])
@pytest.mark.parametrize("shared_contents", [
    "empty", "metadata", "incomplete", "complete", "model-only", "payload-only", "config-only",
])
def test_auto_tokenizer_cache_layouts_remain_usable_offline(
    monkeypatch, tmp_path, layout, shared_contents,
):
    """Real consumer loads choose complete caches, including config.json fallback."""
    from huggingface_hub import constants
    from transformers import AutoTokenizer, dynamic_module_utils

    hub_cache = tmp_path / "hub"
    model_code = tmp_path / "modules" / "modeling_moss_tts_nano.py"
    legacy_cache = model_code.parent / ".cache" / "huggingface"
    snapshot = _cache_auto_tokenizer(legacy_cache, layout)
    assert not (snapshot / "tokenizer.model").exists()
    if shared_contents != "empty":
        _cache_auto_tokenizer(hub_cache, layout, shared_contents)
    repo = "OpenMOSS-Team/MOSS-TTS-Nano"
    expected_tokens = [1] if layout == "json" else [0, 1]
    tokenizer = AutoTokenizer.from_pretrained(
        repo, cache_dir=legacy_cache, use_fast=False,
        trust_remote_code=True, local_files_only=True,
    )
    assert tokenizer.encode("hi", add_special_tokens=False) == expected_tokens

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(hub_cache))
    modules_cache = str(model_code.parent)
    monkeypatch.setattr(dynamic_module_utils, "HF_MODULES_CACHE", modules_cache)
    monkeypatch.setenv("HF_MODULES_CACHE", modules_cache)
    module = types.ModuleType("_auto_tokenizer_nano_model")
    module.__file__ = str(model_code)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    used_caches = []
    load_tokenizer = AutoTokenizer.from_pretrained

    def track_tokenizer(*args, **kwargs):
        tokenizer = load_tokenizer(*args, **kwargs)
        if tokenizer.vocab_size > 0:
            used_caches.append(Path(kwargs["cache_dir"]))
        return tokenizer

    monkeypatch.setattr(AutoTokenizer, "from_pretrained", track_tokenizer)

    class NanoModel:
        config = types.SimpleNamespace(tokenizer_use_fast=False)

        def _load_resolved_text_tokenizer(self, resolved_path, cache_dir):
            return AutoTokenizer.from_pretrained(
                resolved_path, cache_dir=cache_dir, use_fast=False,
                trust_remote_code=True, local_files_only=True,
            )

        def load_text_tokenizer(self):
            if not hasattr(self, "_cached_text_tokenizer"):
                cache_dir = self._resolve_hf_cache_dir()
                self._patch_hf_dynamic_module_cache_dir(cache_dir)
                self._cached_text_tokenizer = self._load_resolved_text_tokenizer(repo, cache_dir)
            assert self._cached_text_tokenizer.encode("hi", add_special_tokens=False) == expected_tokens

    NanoModel.__module__ = module.__name__
    sidecar = _load_sidecar(monkeypatch, [], model_factory=NanoModel, fail_first_synthesis=True)
    monkeypatch.setattr(sidecar.time, "sleep", lambda seconds: None)
    out = io.BytesIO()

    sidecar._handle_synthesize({"text": "hi"}, out)
    sidecar._handle_synthesize({"text": "hi again"}, out)

    expected_cache = hub_cache if shared_contents in {"complete", "config-only"} else legacy_cache
    assert used_caches == [expected_cache] * 2
    assert dynamic_module_utils.HF_MODULES_CACHE == modules_cache
    assert sidecar.os.environ["HF_MODULES_CACHE"] == modules_cache
    assert _frames(out)[-1]["op"] == "audio"


def _model_with_legacy_cache(monkeypatch, tmp_path, loader):
    """Give a lightweight model the same on-disk legacy root as a cached Nano."""
    model_code = tmp_path / "modules" / "modeling_moss_tts_nano.py"
    legacy_cache = model_code.parent / ".cache" / "huggingface"
    legacy_cache.mkdir(parents=True)
    module = types.ModuleType("_nano_tokenizer_loader_model")
    module.__file__ = str(model_code)
    monkeypatch.setitem(sys.modules, module.__name__, module)

    class NanoModel:
        config = types.SimpleNamespace(tokenizer_use_fast=False)

        def _load_resolved_text_tokenizer(self, resolved_path, cache_dir):
            return loader(resolved_path, cache_dir)

    NanoModel.__module__ = module.__name__
    return NanoModel(), legacy_cache


@pytest.mark.parametrize("local_kind", ["file", "directory"])
def test_local_tokenizers_keep_the_upstream_loader(monkeypatch, tmp_path, local_kind):
    """Local .model files and directory fallbacks remain the upstream's job."""
    from transformers import AutoTokenizer

    calls = []
    tokenizer = object()

    def load_local(resolved_path, cache_dir):
        calls.append((resolved_path, cache_dir))
        return tokenizer

    model, _ = _model_with_legacy_cache(monkeypatch, tmp_path, load_local)
    sidecar = _load_sidecar(monkeypatch, [])
    sidecar._configure_tokenizer_cache(model)
    local = tmp_path / ("tokenizer.model" if local_kind == "file" else "checkpoint")
    if local_kind == "file":
        local.write_bytes(b"local vocabulary")
    else:
        local.mkdir()

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("local paths must go directly to the original loader")

    monkeypatch.setattr(AutoTokenizer, "from_pretrained", unexpected_probe)
    assert model._load_resolved_text_tokenizer(str(local), "shared") is tokenizer
    assert calls == [(str(local), "shared")]


def test_cache_misses_preserve_the_original_first_use_loader(monkeypatch, tmp_path):
    """Only the original loader retains the existing first-use download behavior."""
    from huggingface_hub import constants
    from transformers import AutoTokenizer

    hub_cache = tmp_path / "hub"
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(hub_cache))
    calls = []
    tokenizer = object()

    def load_original(resolved_path, cache_dir):
        calls.append(("original", cache_dir))
        return tokenizer

    model, legacy_cache = _model_with_legacy_cache(monkeypatch, tmp_path, load_original)
    sidecar = _load_sidecar(monkeypatch, [])
    sidecar._configure_tokenizer_cache(model)
    load_tokenizer = AutoTokenizer.from_pretrained

    def track_probe(*args, **kwargs):
        assert kwargs["local_files_only"] is True
        calls.append(("offline", kwargs["cache_dir"]))
        return load_tokenizer(*args, **kwargs)

    monkeypatch.setattr(AutoTokenizer, "from_pretrained", track_probe)
    assert model._load_resolved_text_tokenizer("OpenMOSS-Team/MOSS-TTS-Nano", str(hub_cache)) is tokenizer
    assert calls == [("offline", str(hub_cache)), ("offline", str(legacy_cache)), ("original", str(hub_cache))]


@pytest.mark.parametrize("probe_error", [TypeError, ImportError])
def test_incomplete_tokenizer_errors_still_try_legacy(monkeypatch, tmp_path, probe_error):
    """Tokenizer versions can report incomplete cache files with different errors."""
    from huggingface_hub import constants
    from transformers import AutoTokenizer

    hub_cache = tmp_path / "hub"
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(hub_cache))
    model, legacy_cache = _model_with_legacy_cache(monkeypatch, tmp_path, lambda *args: None)
    _cache_auto_tokenizer(legacy_cache, "json")
    sidecar = _load_sidecar(monkeypatch, [])
    sidecar._configure_tokenizer_cache(model)
    load_tokenizer = AutoTokenizer.from_pretrained
    calls = []

    def probe(*args, **kwargs):
        assert kwargs["local_files_only"] is True
        calls.append(kwargs["cache_dir"])
        if kwargs["cache_dir"] == str(hub_cache):
            raise probe_error("incomplete cached tokenizer")
        return load_tokenizer(*args, **kwargs)

    monkeypatch.setattr(AutoTokenizer, "from_pretrained", probe)
    tokenizer = model._load_resolved_text_tokenizer("OpenMOSS-Team/MOSS-TTS-Nano", str(hub_cache))
    assert tokenizer.encode("hi", add_special_tokens=False) == [1]
    assert calls == [str(hub_cache), str(legacy_cache)]


def test_cache_hooks_do_not_rewrap_or_retain_discarded_models(monkeypatch, tmp_path):
    """Discarded models release immediately even while automatic cyclic GC is off."""
    import gc
    import weakref

    model, _ = _model_with_legacy_cache(monkeypatch, tmp_path, lambda *args: None)
    sidecar = _load_sidecar(monkeypatch, [])
    sidecar._configure_tokenizer_cache(model)
    wrapper = model._load_resolved_text_tokenizer
    sidecar._configure_tokenizer_cache(model)
    assert model._load_resolved_text_tokenizer is wrapper
    model_ref = weakref.ref(model)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        del model
        assert model_ref() is None
    finally:
        if was_enabled:
            gc.enable()


def test_the_sidecar_imports_nothing_from_the_app():
    src = _MAIN.read_text(encoding="utf-8")
    for name in ("services", "core", "engines", "api", "backend", "utils"):
        assert not re.search(rf"^\s*(from|import) {name}\b", src, re.M), name


def test_the_class_switches_to_the_sidecar_once_its_venv_exists(monkeypatch, tmp_path):
    from engines.moss_tts_nano_subprocess import MossTTSNanoSubprocessBackend
    from services import tts_backend
    from services.sidecar_install import _INSTALL_COMPLETE_MARKER, _venv_python

    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_NANO_DIR", "")
    monkeypatch.delenv("OMNIVOICE_MOSS_TTS_NANO_DIR")
    assert tts_backend.get_backend_class("moss-tts-nano") is tts_backend.MossTTSNanoBackend

    py = _venv_python(tmp_path / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    (tmp_path / _INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_NANO_DIR", str(tmp_path))

    cls = tts_backend.get_backend_class("moss-tts-nano")
    assert cls is MossTTSNanoSubprocessBackend
    assert cls.venv_python() == py
    assert cls.is_available() == (True, "ready")
    for attr in ("id", "display_name", "gpu_compat"):
        assert getattr(cls, attr) == getattr(tts_backend.MossTTSNanoBackend, attr), attr
    assert cls().supported_languages == tts_backend.MossTTSNanoBackend().supported_languages


def test_every_own_venv_engine_has_an_installer_that_sets_its_path():
    """The resolver switches on the env var the engine's module reads; the
    installer must be the thing that sets that same variable."""
    import importlib

    from services import sidecar_install, tts_backend

    for engine_id, (module_name, class_name) in tts_backend._OWN_VENV_SIDECARS.items():
        module = importlib.import_module(module_name)
        spec = sidecar_install.get_spec(engine_id)
        assert spec is not None, engine_id
        assert spec.env_var == module.VENV_ENV_VAR, engine_id
        assert getattr(module, class_name).id == engine_id
