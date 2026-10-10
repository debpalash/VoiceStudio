"""Tests for the MOSS-TTS-v1.5 engine (issue #498).

MOSS-TTS-v1.5 runs in a dedicated subprocess venv (transformers==5.0.0),
isolated from the parent's transformers>=5.3 — so these tests never import
MOSS itself. They exercise the parent-side wiring that ships in the default
install: registry resolution, subprocess isolation, hardware honesty, the
not-installed gate, and the generate() kwarg arbitration. No network, no
optional deps, no subprocess spawn.
"""
from __future__ import annotations

import importlib
import sys

import pytest


# ── registry wiring ────────────────────────────────────────────────────────


def test_registry_contains_moss_tts_v15():
    """``_REGISTRY["moss-tts-v15"]`` resolves to ``MossTTSV15Backend`` and is
    flagged subprocess-isolated."""
    from services.tts_backend import _REGISTRY, get_backend_class

    assert "moss-tts-v15" in _REGISTRY, (
        "_REGISTRY is missing 'moss-tts-v15'; check _LAZY_REGISTRY in "
        "services/tts_backend.py"
    )
    cls = _REGISTRY["moss-tts-v15"]
    assert cls.__name__ == "MossTTSV15Backend"
    assert get_backend_class("moss-tts-v15") is cls
    # Duck-typed marker survives sys.modules['services.*'] purges (the same
    # reason list_backends/test_supertonic3 rely on it instead of issubclass).
    assert getattr(cls, "_is_subprocess_isolated", False), (
        "MossTTSV15Backend should be subprocess-isolated"
    )
    for name in ("is_available", "generate", "sample_rate", "supported_languages"):
        assert hasattr(cls, name), f"MossTTSV15Backend missing {name!r}"


def test_pep562_lazy_import():
    """The lazy registry resolves the class on first access."""
    mod = importlib.import_module("services.tts_backend")
    cls = mod._REGISTRY["moss-tts-v15"]
    assert cls.__name__ == "MossTTSV15Backend"


def test_install_hint_present():
    """A Settings tooltip points users at the clone + env var."""
    from services.tts_backend import _INSTALL_HINTS

    hint = _INSTALL_HINTS.get("moss-tts-v15", "")
    assert "OMNIVOICE_MOSS_TTS_V15_DIR" in hint
    assert "OpenMOSS" in hint
    assert "CUDA/CPU" not in hint
    backend = importlib.import_module("engines.moss_tts_v15").MossTTSV15Backend
    for family in backend.gpu_compat:
        assert ("ROCm" if family == "rocm" else family.upper()) in hint


def test_sidecar_script_ships():
    """The sidecar entrypoint ships with the install (the parent spawns it)."""
    from engines.moss_tts_v15.bootstrap import MOSS_TTS_V15_SIDECAR_SCRIPT

    assert MOSS_TTS_V15_SIDECAR_SCRIPT.name == "main.py"
    assert MOSS_TTS_V15_SIDECAR_SCRIPT.is_file()


def test_default_model_source_is_pinned(monkeypatch):
    from engines.moss_tts_v15 import main
    monkeypatch.delenv("OMNIVOICE_MOSS_TTS_V15_MODEL", raising=False)
    assert main._model_source() == (main._DEFAULT_REPO, main._DEFAULT_REVISION)


def test_default_model_revision_matches_the_central_reviewed_pin():
    from engines.moss_tts_v15 import main
    from services.hf_revisions import revision_for

    assert main._DEFAULT_REVISION == revision_for(main._DEFAULT_REPO)


def test_codec_revision_matches_its_separate_central_pin():
    """The independently loaded codec is tracked as a pinned model dependency."""
    from engines.moss_tts_v15 import main
    from services.hf_revisions import revision_for
    from services.model_licenses import load_registry

    assert main._CODEC_REVISION == revision_for(main._CODEC_REPO)
    row = next(row for row in load_registry()["models"] if row["id"] == main._DEFAULT_REPO)
    assert main._CODEC_REPO in row["required_components"]


def test_custom_remote_code_is_rejected_without_explicit_opt_in(monkeypatch):
    from engines.moss_tts_v15 import main
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_MODEL", "someone/custom-model")
    monkeypatch.delenv("OMNIVOICE_MOSS_TTS_V15_TRUST_REMOTE_CODE", raising=False)
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_REVISION", "a" * 40)
    with pytest.raises(RuntimeError, match="after auditing"):
        main._model_source()


def test_custom_remote_code_requires_immutable_revision(monkeypatch):
    from engines.moss_tts_v15 import main
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_MODEL", "someone/custom-model")
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_TRUST_REMOTE_CODE", "1")
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_REVISION", "main")
    with pytest.raises(RuntimeError, match="40-character commit SHA"):
        main._model_source()


def test_audited_custom_remote_code_requires_both_opt_ins(monkeypatch):
    from engines.moss_tts_v15 import main
    revision = "b" * 40
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_MODEL", "someone/custom-model")
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_TRUST_REMOTE_CODE", "true")
    monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_REVISION", revision)
    assert main._model_source() == ("someone/custom-model", revision)


@pytest.mark.parametrize("custom", [False, True])
def test_loader_resolves_model_and_codec_revisions_before_loading(monkeypatch, tmp_path, custom):
    """#2690: upstream forwards processor kwargs to the codec and its constructor."""
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock
    from engines.moss_tts_v15 import main

    repo, revision = main._DEFAULT_REPO, main._DEFAULT_REVISION
    if custom:
        repo, revision = "someone/audited-model", "b" * 40
        monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_MODEL", repo)
        monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_TRUST_REMOTE_CODE", "1")
        monkeypatch.setenv("OMNIVOICE_MOSS_TTS_V15_REVISION", revision)
    else:
        monkeypatch.delenv("OMNIVOICE_MOSS_TTS_V15_MODEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_MOSS_TTS_V15_ATTN", raising=False)

    codec_repo = "OpenMOSS-Team/MOSS-Audio-Tokenizer"
    codec_revision = "3cd226ba2947efa357ef453bcad111b6eafba782"
    snapshots = {
        (repo, revision): str(tmp_path / "model"),
        (codec_repo, codec_revision): str(tmp_path / "codec"),
    }
    hub = Mock(side_effect=lambda repo_id, revision: snapshots[(repo_id, revision)])
    monkeypatch.setattr(main, "_snapshot_path", hub)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False), float32="fp32",
    ))
    processor = Mock()
    processor.model_config.sampling_rate = 24000
    processor.audio_tokenizer.to.return_value = processor.audio_tokenizer

    def load_processor(path, *, trust_remote_code, codec_path):
        """Reject leaked revisions and require each component's own local path."""
        # The real ProcessorMixin rejects revision; the codec must also use
        # its own snapshot rather than the TTS repository's commit.
        assert path == snapshots[(repo, revision)]
        assert codec_path == snapshots[(codec_repo, codec_revision)]
        assert trust_remote_code is True
        return processor

    model = Mock()
    model.to.return_value = model
    factory = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=load_processor),
        AutoModel=SimpleNamespace(from_pretrained=factory),
    ))
    monkeypatch.setattr(main, "_state", None)
    state = main._load_model(io.BytesIO())

    assert state == (processor, model, "cpu", 24000)
    assert [call.args for call in hub.call_args_list] == list(snapshots)
    factory.assert_called_once_with(
        snapshots[(repo, revision)], trust_remote_code=True,
        attn_implementation="sdpa", torch_dtype="fp32",
    )
    model.eval.assert_called_once_with()
    # Subsequent requests reuse the loaded state without contacting the Hub.
    assert main._load_model(io.BytesIO()) is state
    assert hub.call_count == 2


# ── hardware honesty (cross-platform rule) ─────────────────────────────────


def test_gpu_compat_matches_accelerator_paths_without_mps():
    """MPS is undocumented/untested upstream — we must not claim it."""
    from engines.moss_tts_v15 import MossTTSV15Backend

    assert MossTTSV15Backend.gpu_compat == ("cuda", "rocm", "xpu", "npu", "cpu"), (
        f"unexpected device targets: {MossTTSV15Backend.gpu_compat!r}"
    )


def test_is_available_not_installed_is_honest(monkeypatch):
    """When the venv isn't present, is_available() gates cleanly with an
    actionable hint and never claims MPS."""
    monkeypatch.setattr(
        "engines.moss_tts_v15.bootstrap.is_moss_tts_v15_installed",
        lambda: False,
    )
    from engines.moss_tts_v15 import MossTTSV15Backend

    ok, msg = MossTTSV15Backend.is_available()
    assert ok is False
    assert "OMNIVOICE_MOSS_TTS_V15_DIR" in msg
    assert "docs/engines/moss-tts-v15.md" in msg  # actionable pointer
    # Honesty on hardware is enforced by gpu_compat (no 'mps' entry); the
    # message is free to *disclaim* MPS ("no MPS"), which is the opposite of
    # claiming it.


def test_sample_rate_and_languages():
    from engines.moss_tts_v15 import MossTTSV15Backend

    b = MossTTSV15Backend()
    assert b.sample_rate == 24000
    assert b.supported_languages == ["multi"]


# ── generate() parent-side arbitration ─────────────────────────────────────


def test_duration_maps_to_tokens(monkeypatch):
    """``duration`` (seconds) → ``tokens`` at 12.5 tokens/sec; engine-specific
    kwargs are forwarded and the generic ``num_step`` is dropped."""
    captured: dict = {}

    def fake_super_generate(self, text, **kw):
        import torch
        captured["text"] = text
        captured.update(kw)
        return torch.zeros(1, 8)

    from engines.moss_tts_v15 import MossTTSV15Backend
    # Patch the exact SubprocessBackend in this backend's MRO (not via a
    # module-path string): survives the sys.modules['services.*'] reloads
    # other tests perform, so super().generate() hits the fake instead of
    # dispatching to the real class and trying to spawn a sidecar. (#498)
    _sub = next(c for c in MossTTSV15Backend.__mro__ if c.__name__ == "SubprocessBackend")
    monkeypatch.setattr(_sub, "generate", fake_super_generate)

    MossTTSV15Backend().generate(
        "hello world",
        ref_audio="/tmp/spk.wav",
        language="fr",
        duration=26.0,
        num_step=16,           # generic kwarg MOSS doesn't use
        max_new_tokens=2048,
    )
    assert captured["text"] == "hello world"
    assert captured["ref_audio"] == "/tmp/spk.wav"
    assert captured["language"] == "fr"
    assert captured["tokens"] == int(26.0 * 12.5)  # 325
    assert captured["max_new_tokens"] == 2048
    assert "num_step" not in captured  # not part of MOSS's surface


def test_generate_without_ref_audio_omits_reference(monkeypatch):
    """No ref_audio → plain TTS: neither ref_audio nor tokens leak in."""
    captured: dict = {}

    def fake_super_generate(self, text, **kw):
        import torch
        captured.update(kw)
        return torch.zeros(1, 8)

    from engines.moss_tts_v15 import MossTTSV15Backend
    # Patch the exact SubprocessBackend in this backend's MRO (not via a
    # module-path string): survives the sys.modules['services.*'] reloads
    # other tests perform, so super().generate() hits the fake instead of
    # dispatching to the real class and trying to spawn a sidecar. (#498)
    _sub = next(c for c in MossTTSV15Backend.__mro__ if c.__name__ == "SubprocessBackend")
    monkeypatch.setattr(_sub, "generate", fake_super_generate)

    MossTTSV15Backend().generate("just text")
    assert "ref_audio" not in captured
    assert "tokens" not in captured


@pytest.mark.parametrize('family,legacy,probe_raises', [
    (None, False, False), ('cuda', False, False), ('xpu', False, False),
    ('npu', False, False), ('mps', False, False), (None, True, False),
    ('cuda', True, False), (None, False, True), (None, True, True),
])
def test_loader_device_matches_routing(monkeypatch, family, legacy, probe_raises):
    """Exercise model + tokenizer placement without importing optional weights."""
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock
    from engines.moss_tts_v15 import main, MossTTSV15Backend
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    expected = family if family not in (None, 'mps') else 'cpu'
    accelerator = Mock(return_value=SimpleNamespace(type=family) if family else None)
    cuda_available = Mock(return_value=family == 'cuda')
    if probe_raises:
        accelerator.side_effect = RuntimeError('driver initialization failed')
        cuda_available.side_effect = RuntimeError('driver initialization failed')
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(
        accelerator=SimpleNamespace() if legacy else SimpleNamespace(current_accelerator=accelerator),
        cuda=SimpleNamespace(is_available=cuda_available),
        device=lambda value: SimpleNamespace(type=value),
        bfloat16='bf16', float32='fp32',
    ))
    processor = Mock()
    processor.model_config.sampling_rate = 24000
    tokenizer = processor.audio_tokenizer
    model = Mock()
    model.to.return_value = model
    factory = Mock()
    factory.from_pretrained.return_value = model
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        AutoModel=factory,
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *a, **kw: processor),
    ))
    monkeypatch.setattr(main, '_snapshot_path', Mock(return_value='local-fixture'))
    monkeypatch.setattr(main, '_state', None)
    monkeypatch.setattr(main, '_model_source', lambda: ('local-fixture', 'a' * 40))
    state = main._load_model(io.BytesIO())
    if not legacy:
        accelerator.assert_called_once_with(check_available=True)
    model.to.assert_called_once_with(expected)
    tokenizer.to.assert_called_once_with(expected)
    assert factory.from_pretrained.call_args.kwargs['torch_dtype'] == ('fp32' if expected == 'cpu' else 'bf16')
    caps = HostCaps(family=family or 'cpu', available_families=(family, 'cpu') if family else ('cpu',))
    assert resolve_routing(MossTTSV15Backend.gpu_compat, caps)['effective_device'] == state[2]


def test_availability_text_does_not_exclude_declared_devices(monkeypatch):
    from engines.moss_tts_v15 import MossTTSV15Backend, bootstrap

    assert "CUDA/CPU" not in MossTTSV15Backend.display_name
    for installed in (False, True):
        monkeypatch.setattr(bootstrap, "is_moss_tts_v15_installed", lambda: installed)
        available, reason = MossTTSV15Backend.is_available()
        assert available is installed
        assert "CUDA or CPU only" not in reason
        assert "CUDA when present, else CPU" not in reason


def test_bootstrap_install_names_the_pytorch_cuda_index(monkeypatch, tmp_path):
    """#2015: the [torch-runtime] extra pins torch==2.9.1+cu128, which exists
    only on PyTorch's index — without it the install could never resolve."""
    from core.torch_indexes import UV_PIP_CU128_ARGS
    from engines.moss_tts_v15 import bootstrap

    ran = []
    monkeypatch.setattr(bootstrap, "_ENGINES_VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(bootstrap, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(bootstrap, "_uv_env", lambda: None)
    monkeypatch.setattr(bootstrap, "_venv_can_import_moss", lambda p: "yes")
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda argv, **k: ran.append(argv))

    bootstrap._bootstrap_engines_venv(tmp_path / "MOSS-TTS")

    pip = next(a for a in ran if a[1:3] == ["pip", "install"])
    i = pip.index("--extra-index-url")
    assert tuple(pip[i:i + len(UV_PIP_CU128_ARGS)]) == UV_PIP_CU128_ARGS
    venv_python = bootstrap._venv_python_path(tmp_path / ".venv")
    assert pip[pip.index("--python") + 1] == str(venv_python)
    _assert_cpu_torchcodec_constraint(pip)


def _assert_cpu_torchcodec_constraint(args):
    """The CPU version is admitted while the CUDA local version is excluded."""
    from packaging.requirements import Requirement

    requirements = [Requirement(arg) for arg in args if arg.startswith("torchcodec")]
    assert len(requirements) == 1
    versions = requirements[0].specifier
    assert "0.8.1" in versions
    assert "0.8.1+cu128" not in versions


def test_managed_install_excludes_cuda_torchcodec():
    """CUDA TorchCodec requires NPP even though MOSS only decodes CPU audio."""
    from services.sidecar_install import SPECS

    _assert_cpu_torchcodec_constraint(SPECS["moss-tts-v15"].install_args)


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32'])
def test_bootstrap_requires_managed_decoder_upgrade_but_preserves_external_venv(monkeypatch, tmp_path, platform):
    """A stale managed recipe cannot bypass repair through the legacy probe."""
    from engines.moss_tts_v15 import bootstrap
    from services import sidecar_install as si

    bootstrap.invalidate()
    monkeypatch.setattr(bootstrap.sys, 'platform', platform)
    monkeypatch.setattr(si, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(bootstrap, '_ENGINES_VENV_DIR', tmp_path / 'owned-venv')
    monkeypatch.setattr(bootstrap, '_resolved_python', None)
    spec = si.SPECS['moss-tts-v15']
    checkout = si.managed_checkout(spec)
    py = bootstrap._venv_python_path(checkout / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('#!fake\n')
    marker = checkout / si._INSTALL_COMPLETE_MARKER
    marker.write_text(spec.probe_module + '\n')
    monkeypatch.setenv(spec.env_var, str(checkout))
    monkeypatch.setattr(bootstrap, '_venv_can_import_moss', lambda p: 'yes')

    assert not bootstrap.is_moss_tts_v15_installed()
    with pytest.raises(RuntimeError, match='Model Catalogue'):
        bootstrap.resolve_moss_tts_v15_venv()
    marker.write_text(f'{spec.probe_module}\n{spec.install_revision}\n')
    assert bootstrap.is_moss_tts_v15_installed()
    assert bootstrap.resolve_moss_tts_v15_venv() == py

    bootstrap.invalidate()
    external = tmp_path / 'external'
    external_py = bootstrap._venv_python_path(external / '.venv')
    external_py.parent.mkdir(parents=True)
    external_py.write_text('#!manual\n')
    monkeypatch.setenv(spec.env_var, str(external))
    assert bootstrap.is_moss_tts_v15_installed()
    assert bootstrap.resolve_moss_tts_v15_venv() == external_py


def test_bootstrap_install_failure_reports_uvs_error_not_a_host_guess(monkeypatch, tmp_path):
    """The PyTorch index is always supplied now, so blaming "a non-CUDA host"
    would mislead; uv's own error says what failed."""
    import subprocess

    from engines.moss_tts_v15 import bootstrap

    def fake_run(argv, **kwargs):
        if argv[1:3] == ["pip", "install"]:
            raise subprocess.CalledProcessError(1, argv, stderr=b"resolver: no wheel for torchcodec")

    monkeypatch.setattr(bootstrap, "_ENGINES_VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(bootstrap, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(bootstrap, "_uv_env", lambda: None)
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as err:
        bootstrap._bootstrap_engines_venv(tmp_path / "MOSS-TTS")

    message = str(err.value)
    assert "resolver: no wheel for torchcodec" in message
    assert "non-CUDA" not in message
    assert "docs/engines/moss-tts-v15.md" in message
