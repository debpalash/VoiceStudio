"""Voice Design takes must follow the seed and details on every engine (#3413).

Changing the seed or details and generating again gave the same voice when
the engine never saw them:

* ``/generate`` forwards ``seed`` only to adapters that declare
  ``supports_generation_seed`` (or the native OmniVoice proxy). An engine
  outside this process (a sidecar, a C++ binary or server) never sees the
  parent's ``torch.manual_seed``, so without the flag the seed is lost.
  audio.cpp and OmniVoice GGUF read ``seed`` but never declared it; the
  CosyVoice sidecar did not seed at all.
* Engines that never read ``instruct`` (KittenTTS, Sherpa-ONNX, PocketTTS)
  left Voice Design open and rendered the same preset voice for any details.
"""
import importlib
import inspect
import os
import re

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _tts():
    return importlib.import_module("services.tts_backend")


def _all_backend_classes():
    tts = _tts()
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend

    # The sidecar twins replace their in-process class once their venv exists.
    return list(tts._REGISTRY.values()) + [CosyVoiceSubprocessBackend, VoxCPM2SubprocessBackend]


def _honours_seed(cls) -> bool:
    return bool(
        getattr(cls, "supports_generation_seed", False)
        or getattr(cls, "supports_native_omnivoice_controls", False)
    )


def _out_of_process(cls) -> bool:
    return bool(getattr(cls, "_is_subprocess_isolated", False)
                or getattr(cls, "runs_out_of_process", False))


def test_an_adapter_that_reads_seed_declares_it():
    for cls in _all_backend_classes():
        source = inspect.getsource(cls.generate)
        if re.search(r"""\bkw(?:args)?\.get\(\s*["']seed["']|\bseed\s*=""", source):
            assert _honours_seed(cls), f"{cls.__name__} reads seed but /generate never sends it"


def test_an_out_of_process_engine_that_can_design_honours_the_seed():
    tts = _tts()
    for cls in _all_backend_classes():
        if _out_of_process(cls) and tts.voice_design_support(cls) is not False:
            assert _honours_seed(cls), (
                f"{cls.__name__} designs in another process; the parent's torch "
                "seed never reaches it, so it must take the seed per request"
            )


def test_engines_that_ignore_instruct_cannot_design():
    tts = _tts()
    from engines.pockettts import PocketTTSBackend

    for cls in (tts.KittenTTSBackend, tts.SherpaOnnxBackend, PocketTTSBackend):
        assert tts.voice_design_support(cls) is False, cls.__name__


def _probe(flag, calls):
    import torch

    class Probe:
        sample_rate = 24000
        supports_generation_seed = flag

        def generate(self, text, **kw):
            calls.append(kw)
            return torch.ones(1, 2400) * 0.1

    return Probe()


def test_the_seed_reaches_audiocpp_and_gguf_through_generate():
    from api.routers.generation import _run_backend_inference

    for module, name in (("engines.audiocpp", "AudioCPPBackend"),
                         ("engines.omnivoice_gguf", "OmniVoiceGGUFBackend")):
        cls = getattr(importlib.import_module(module), name)
        calls = []
        _run_backend_inference(_probe(cls.supports_generation_seed, calls), "Hello.",
                               "English", None, None, "male", None,
                               32, 2, 1, True, True, 1234, "raw")
        assert calls[-1].get("seed") == 1234, name


def test_cosyvoice_sidecar_seeds_its_own_rng(monkeypatch):
    import torch
    from engines.cosyvoice_subprocess import main

    seeds = []
    monkeypatch.setattr(main, "_load_model", lambda _stdout: object())
    monkeypatch.setattr(main, "_run_model", lambda _model, _msg: [{"tts_speech": torch.zeros(1, 240)}])
    monkeypatch.setattr(torch, "manual_seed", lambda seed: seeds.append(seed))
    monkeypatch.setattr(main, "_send", lambda _stdout, _frame: None)
    main._handle_synthesize({"text": "hello", "seed": 7}, None)
    assert seeds == [7]
