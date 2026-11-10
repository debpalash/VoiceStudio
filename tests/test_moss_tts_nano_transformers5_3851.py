"""#3851: MOSS-TTS-Nano's remote model must load and run under transformers 5.

Two transformers 5 behaviours broke it, both reproduced here with a tiny model
shaped like upstream's (a list ``_keys_to_ignore_on_load_unexpected`` and a
non-persistent RoPE ``rotary_emb.inv_freq``) and the real ``from_pretrained``:
the list made every load raise ``TypeError``, and the buffer came back as
uninitialized memory, so synthesis produced NaN/Inf.
"""
import importlib.util
import types
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")
from transformers import PretrainedConfig, PreTrainedModel  # noqa: E402
from transformers.modeling_utils import PreTrainedModel as _Base  # noqa: E402

_MAIN = Path(__file__).resolve().parents[1] / "backend/engines/moss_tts_nano_subprocess/main.py"
_TF5 = int(transformers.__version__.split(".")[0]) >= 5


def _sidecar():
    spec = importlib.util.spec_from_file_location("_nano_sidecar_3851", _MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected(dim=8, base=10000.0):
    return 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.float32) / dim))


class TinyConfig(PretrainedConfig):
    model_type = "voicestudio-tiny-moss-3851"


class Rotary(torch.nn.Module):
    def __init__(self, dim=8):
        super().__init__()
        self.register_buffer("inv_freq", _expected(dim), persistent=False)


class Attn(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.proj = torch.nn.Linear(8, 8)
        self.rotary_emb = Rotary()


class Stack(torch.nn.Module):
    def __init__(self, layers):
        super().__init__()
        self.h = torch.nn.ModuleList(Attn() for _ in range(layers))


class TinyMoss(PreTrainedModel):
    config_class = TinyConfig
    base_model_prefix = "transformer"
    # Upstream declares a list, which transformers 4 accepted.
    _keys_to_ignore_on_load_unexpected = [r"local_transformer\.wte\.weight"]

    def __init__(self, config):
        super().__init__(config)
        self.transformer = Stack(2)
        self.local_transformer = Stack(1)

    @property
    def all_tied_weights_keys(self):  # upstream defines it instead of post_init()
        return {}


def _rope_buffers(model):
    return [layer.rotary_emb.inv_freq for stack in (model.transformer, model.local_transformer)
            for layer in stack.h]


@pytest.fixture
def checkpoint(tmp_path):
    TinyMoss(TinyConfig()).save_pretrained(tmp_path)
    return tmp_path


@pytest.fixture
def pristine_loader(monkeypatch):
    # The shim patches transformers process-wide (other sidecar tests install
    # it too): start from the unpatched method and restore after each test.
    method = _Base._adjust_missing_and_unexpected_keys
    while hasattr(method, "__wrapped__"):
        method = method.__wrapped__
    monkeypatch.setattr(_Base, "_adjust_missing_and_unexpected_keys", method)


@pytest.mark.skipif(not _TF5, reason="transformers 4 accepts a list")
def test_a_listed_ignore_pattern_breaks_the_unpatched_load(checkpoint, pristine_loader):
    with pytest.raises(TypeError):
        TinyMoss.from_pretrained(checkpoint)


def test_the_sidecar_loads_and_restores_every_rope_buffer(checkpoint, pristine_loader):
    sidecar = _sidecar()
    sidecar._accept_list_ignore_patterns()
    sidecar._accept_list_ignore_patterns()  # idempotent: never stacks wrappers

    model = TinyMoss.from_pretrained(checkpoint)
    for buffer in _rope_buffers(model):
        buffer.fill_(float("nan"))  # what transformers 5's empty_like can leave
    restored = sidecar._restore_non_persistent_buffers(model)

    assert restored is model
    for buffer in _rope_buffers(model):
        if _TF5:
            torch.testing.assert_close(buffer, _expected())
        else:  # transformers 4 initializes buffers itself; nothing is touched
            assert torch.isnan(buffer).all()


@pytest.mark.skipif(not _TF5, reason="transformers 5 only")
def test_restore_runs_once_per_model_and_keeps_device_and_dtype(checkpoint, pristine_loader):
    sidecar = _sidecar()
    sidecar._accept_list_ignore_patterns()
    model = TinyMoss.from_pretrained(checkpoint).to(torch.bfloat16)
    for buffer in _rope_buffers(model):
        buffer.zero_()
    sidecar._restore_non_persistent_buffers(model)
    for buffer in _rope_buffers(model):
        assert buffer.dtype == torch.bfloat16
        torch.testing.assert_close(buffer, _expected().to(torch.bfloat16))
    # The runtime calls its loader on every synthesis and gets the cached
    # model back; the restore must not rebuild a reference each time.
    for buffer in _rope_buffers(model):
        buffer.zero_()
    sidecar._restore_non_persistent_buffers(model)
    assert all(not buffer.any() for buffer in _rope_buffers(model))


def test_non_module_models_pass_through(pristine_loader):
    model = types.SimpleNamespace()
    assert _sidecar()._restore_non_persistent_buffers(model) is model
