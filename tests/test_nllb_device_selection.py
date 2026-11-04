import asyncio
import sys
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

import pytest


class Tensor:
    def __init__(self, values, device="cpu"):
        self.values = values
        self.device = device

    def to(self, device):
        return Tensor(self.values, device)


class Model:
    def __init__(self):
        self.devices = ["cpu", "cpu"]
        self.moves = []
        self.calls = []
        self.fail_moves = []
        self.fail_generate = None
        self.on_generate = None

    def to(self, device):
        self.moves.append(device)
        self.devices[0] = device
        if self.fail_moves and self.fail_moves[0] == device:
            self.fail_moves.pop(0)
            raise RuntimeError(f"placement failed on {device}")
        self.devices[1] = device
        return self

    def generate(self, *, input_ids, attention_mask, forced_bos_token_id, **options):
        assert self.devices == [input_ids.device, input_ids.device]
        assert attention_mask.device == input_ids.device
        self.calls.append((input_ids.device, list(input_ids.values), options))
        if self.on_generate:
            self.on_generate()
        if self.fail_generate == input_ids.device:
            raise RuntimeError(f"generation failed on {input_ids.device}")
        return [f"{forced_bos_token_id}:{text}" for text in input_ids.values]


class Tokenizer:
    src_lang = None

    def __call__(self, texts, *, return_tensors, padding):
        assert self.src_lang == "eng_Latn"
        assert return_tensors == "pt" and padding is True
        return {"input_ids": Tensor(texts), "attention_mask": Tensor([1] * len(texts))}

    def convert_tokens_to_ids(self, language):
        return language

    def batch_decode(self, tokens, *, skip_special_tokens):
        assert skip_special_tokens is True
        return tokens


@pytest.fixture
def nllb(monkeypatch):
    from api.routers import dub_translate
    from core import device_caps
    from services import model_manager, performance_profiles

    state = SimpleNamespace(
        router=dub_translate, family="cpu", cuda=True, mps=True,
        models=[], tokenizer_loads=[], flushes=[], cache_releases=[], next_fail_moves=[],
    )

    def empty_cache(device):
        model = dub_translate._nllb_model
        state.cache_releases.append((
            device, None if model is None else list(model.devices), dub_translate._nllb_device,
        ))

    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(
        is_available=lambda: state.cuda,
        mem_get_info=lambda: (20 * 1024**3, 24 * 1024**3),
        empty_cache=lambda: empty_cache("cuda"),
    )
    torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: state.mps))
    torch.mps = SimpleNamespace(empty_cache=lambda: empty_cache("mps"))
    state.torch = torch
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: torch)
    monkeypatch.setattr(model_manager, "_configure_rocm_if_needed", lambda _: None)
    monkeypatch.setattr(model_manager, "check_device_compatibility", lambda: (True, ""))
    monkeypatch.setattr(
        device_caps, "detect_host_caps",
        lambda: device_caps.HostCaps(
            family=state.family, available_families=("rocm", "cuda", "mps", "cpu"),
            requested_family=state.family,
        ),
    )
    release_device_cache = model_manager.release_device_cache

    def release_cache(**kwargs):
        state.flushes.append(kwargs)
        release_device_cache(**kwargs)

    monkeypatch.setattr(model_manager, "release_device_cache", release_cache)

    def load_tokenizer(*args, **kwargs):
        assert kwargs["local_files_only"] is True
        assert kwargs["revision"]
        state.tokenizer_loads.append(args)
        return Tokenizer()

    def load_model(*args, **kwargs):
        assert kwargs["local_files_only"] is True
        assert kwargs["revision"]
        model = Model()
        model.fail_moves = list(state.next_fail_moves)
        state.next_fail_moves.clear()
        state.models.append(model)
        return model

    transformers = ModuleType("transformers")
    transformers.AutoTokenizer = SimpleNamespace(from_pretrained=load_tokenizer)
    transformers.AutoModelForSeq2SeqLM = SimpleNamespace(from_pretrained=load_model)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setattr(dub_translate, "_nllb_language", dub_translate.FLORES_CODES.get)
    monkeypatch.setattr(dub_translate, "_nllb_model", None)
    monkeypatch.setattr(dub_translate, "_nllb_tokenizer", None)
    monkeypatch.setattr(dub_translate, "_nllb_device", None)
    monkeypatch.setattr(
        performance_profiles, "translation_decode_defaults", lambda: {"num_beams": 2},
    )
    monkeypatch.setenv("OMNIVOICE_UNLOAD_NLLB", "0")
    monkeypatch.setenv("OMNIVOICE_NLLB_BATCH_SIZE", "2")
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(dub_translate, "_gpu_pool", pool)
        yield state


def request(**kwargs):
    from schemas.requests import TranslateRequest

    return TranslateRequest(
        provider="nllb", source_lang="en", target_lang="de", quality="fast",
        segments=kwargs.pop("segments", [{"id": "1", "text": "hello"}]), **kwargs,
    )


def translate(state, **kwargs):
    return asyncio.run(state.router.dub_translate(request(**kwargs)))


def assert_translated(response):
    assert response["translated"] == [{"id": "1", "text": "deu_Latn:hello"}]


@pytest.mark.parametrize("family,cuda,mps,expected", [
    ("cpu", True, False, "cpu"),
    ("cpu", False, True, "cpu"),
    ("rocm", True, False, "cuda"),
    ("cuda", True, False, "cuda"),
    ("mps", True, True, "mps"),
    ("mps", False, True, "mps"),
])
def test_shared_choice_controls_weights_and_all_inputs(nllb, family, cuda, mps, expected):
    nllb.family, nllb.cuda, nllb.mps = family, cuda, mps
    assert_translated(translate(nllb))
    assert nllb.models[0].devices == [expected, expected]
    assert nllb.models[0].calls[0][0] == expected
    assert nllb.router._nllb_device == expected


@pytest.mark.parametrize("family,device", [("rocm", "cuda"), ("cuda", "cuda"), ("mps", "mps")])
def test_warm_model_follows_cpu_gpu_cpu_and_reuses_unchanged_device(nllb, family, device):
    for choice, expected in [("cpu", "cpu"), (family, device), (family, device), ("cpu", "cpu")]:
        nllb.family = choice
        assert_translated(translate(nllb))
        assert nllb.models[0].devices == [expected, expected]
        assert nllb.models[0].calls[-1][0] == expected
    assert len(nllb.models) == len(nllb.tokenizer_loads) == 1
    assert nllb.models[0].moves == [device, "cpu"]


@pytest.mark.parametrize("family,device", [("rocm", "cuda"), ("cuda", "cuda"), ("mps", "mps")])
def test_partial_gpu_placement_restores_cpu_and_next_request_retries(nllb, family, device):
    nllb.family = family
    nllb.next_fail_moves = [device]
    assert_translated(translate(nllb))
    model = nllb.models[0]
    assert model.devices == ["cpu", "cpu"]
    assert model.calls[0][0] == "cpu"
    assert nllb.router._nllb_device == "cpu"
    assert_translated(translate(nllb))
    assert model.devices == [device, device]
    assert model.calls[-1][0] == device
    assert len(nllb.models) == 1


@pytest.mark.parametrize("family,device", [("rocm", "cuda"), ("cuda", "cuda"), ("mps", "mps")])
def test_warm_cpu_choice_releases_old_allocator_before_cpu_generation(nllb, family, device):
    nllb.family = family
    assert_translated(translate(nllb))
    assert_translated(translate(nllb))
    assert nllb.cache_releases == []
    model = nllb.models[0]

    def require_released_allocator():
        assert nllb.cache_releases == [(device, ["cpu", "cpu"], "cpu")]

    model.on_generate = require_released_allocator
    nllb.family = "cpu"
    assert_translated(translate(nllb))
    assert_translated(translate(nllb))
    assert [call[0] for call in model.calls] == [device, device, "cpu", "cpu"]
    assert nllb.flushes == [{"device": device}]
    assert nllb.router._nllb_model is model


@pytest.mark.parametrize("family,device", [("rocm", "cuda"), ("cuda", "cuda"), ("mps", "mps")])
def test_partial_placement_releases_attempted_allocator_before_cpu_generation(nllb, family, device):
    assert_translated(translate(nllb))
    model = nllb.models[0]
    model.fail_moves = [device]

    def require_released_allocator():
        if model.devices == ["cpu", "cpu"]:
            assert nllb.cache_releases == [(device, ["cpu", "cpu"], "cpu")]

    model.on_generate = require_released_allocator
    nllb.family = family
    assert_translated(translate(nllb))
    assert model.calls[-1][0] == "cpu"
    assert_translated(translate(nllb))
    assert_translated(translate(nllb))
    assert model.calls[-1][0] == device
    assert nllb.flushes == [{"device": device}]
    assert nllb.router._nllb_model is model


def test_failed_cpu_move_unloads_then_releases_the_old_mps_allocator(nllb):
    nllb.family = "mps"
    assert_translated(translate(nllb))
    model = nllb.models[0]
    model.fail_moves = ["cpu"]
    nllb.family = "cpu"
    response = translate(nllb)
    assert "Model load error:" in response["translated"][0]["error"]
    assert len(model.calls) == 1
    assert nllb.cache_releases == [("mps", None, None)]
    assert nllb.flushes == [{"device": "mps"}]
    assert_translated(translate(nllb))
    assert nllb.models[-1].calls[-1][0] == "cpu"


def test_failed_cross_gpu_restore_releases_both_allocators_after_unload(nllb):
    nllb.family = "mps"
    assert_translated(translate(nllb))
    model = nllb.models[0]
    model.fail_moves = ["cuda", "cpu"]
    nllb.family = "rocm"
    response = translate(nllb)
    assert "Model load error:" in response["translated"][0]["error"]
    assert len(model.calls) == 1
    assert nllb.cache_releases == [("mps", None, None), ("cuda", None, None)]
    assert nllb.flushes == [{"device": "mps"}, {"device": "cuda"}]
    assert_translated(translate(nllb))
    assert nllb.models[-1].calls[-1][0] == "cuda"


@pytest.mark.parametrize("partial_placement", [False, True])
def test_allocator_failure_keeps_cpu_translation_and_best_effort_policy(nllb, monkeypatch, partial_placement):
    nllb.family = "cpu" if partial_placement else "rocm"
    assert_translated(translate(nllb))
    model = nllb.models[0]
    model.fail_moves = ["cuda"] if partial_placement else []
    nllb.family = "rocm" if partial_placement else "cpu"
    attempts = []

    def fail_empty_cache():
        attempts.append(list(model.devices))
        raise RuntimeError("allocator unavailable")

    monkeypatch.setattr(nllb.torch.cuda, "empty_cache", fail_empty_cache)
    assert_translated(translate(nllb))
    assert model.devices == ["cpu", "cpu"]
    assert model.calls[-1][0] == "cpu"
    assert attempts == [["cpu", "cpu"]]
    assert nllb.flushes == [{"device": "cuda"}]


@pytest.mark.parametrize("warm", [False, True])
def test_failed_cpu_restore_discards_partial_model_and_can_reload(nllb, warm):
    nllb.family = "cpu" if warm else "rocm"
    if warm:
        assert_translated(translate(nllb))
        nllb.models[0].fail_moves = ["cuda", "cpu"]
        nllb.family = "rocm"
    else:
        nllb.next_fail_moves = ["cuda", "cpu"]
    response = translate(nllb)
    assert "Model load error:" in response["translated"][0]["error"]
    assert len(nllb.models[0].calls) == int(warm)
    assert nllb.router._nllb_model is None
    assert nllb.router._nllb_device is None
    assert_translated(translate(nllb))
    assert len(nllb.models) == 2
    assert nllb.models[-1].calls[-1][0] == "cuda"


def test_failed_warm_cpu_selection_does_not_generate_on_gpu(nllb):
    nllb.family = "rocm"
    assert_translated(translate(nllb))
    model = nllb.models[0]
    model.fail_moves = ["cpu"]
    nllb.family = "cpu"
    response = translate(nllb)
    assert "Model load error:" in response["translated"][0]["error"]
    assert len(model.calls) == 1
    assert nllb.router._nllb_model is None
    assert_translated(translate(nllb))
    assert nllb.models[-1].calls[-1][0] == "cpu"


def test_mps_generation_failure_retries_with_matching_cpu_inputs(nllb):
    nllb.family = "mps"
    assert_translated(translate(nllb))
    nllb.models[0].fail_generate = "mps"
    assert_translated(translate(nllb))
    assert [call[0] for call in nllb.models[0].calls] == ["mps", "mps", "cpu"]
    assert nllb.router._nllb_device == "cpu"
    assert {call[2]["num_beams"] for call in nllb.models[0].calls} == {2}
    assert {call[2]["max_length"] for call in nllb.models[0].calls} == {400}


def test_mps_failed_cpu_restore_discards_model_before_retry(nllb):
    nllb.family = "mps"
    assert_translated(translate(nllb))
    model = nllb.models[0]
    model.fail_generate = "mps"
    model.fail_moves = ["cpu"]
    response = translate(nllb)
    assert response["translated"][0]["error"] == "placement failed on cpu"
    assert len(model.calls) == 2
    assert nllb.router._nllb_model is None
    assert nllb.router._nllb_device is None
    assert_translated(translate(nllb))
    assert len(nllb.models) == 2


@pytest.fixture
def nllb_warnings(nllb, monkeypatch):
    """Keep warning text without pinning exception tracebacks in log records."""
    messages = []

    def warning(message, *args):
        messages.append(message % args)

    monkeypatch.setattr(nllb.router.logger, "warning", warning)
    return messages


@pytest.mark.parametrize("row_count", [1, 3])
@pytest.mark.parametrize("unload", [False, True])
def test_mps_failed_restore_flushes_after_model_and_inputs_die(
    nllb, nllb_warnings, monkeypatch, row_count, unload,
):
    nllb.family = "mps"
    assert_translated(translate(nllb))
    model = nllb.models.pop()
    model.fail_generate = "mps"
    model.fail_moves = ["cpu"]
    model_ref = weakref.ref(model)
    calls = model.calls
    del model
    assert nllb.models == []
    monkeypatch.setenv("OMNIVOICE_UNLOAD_NLLB", "1" if unload else "0")

    input_refs, flush_lifetimes, unloads = [], [], []
    generate = Model.generate
    unload_nllb = nllb.router._unload_nllb

    def track_generate(self, **kwargs):
        input_refs.extend(weakref.ref(kwargs[key]) for key in ("input_ids", "attention_mask"))
        return generate(self, **kwargs)

    def empty_cache():
        flush_lifetimes.append((
            model_ref() is None, all(ref() is None for ref in input_refs),
        ))

    def track_unload(**kwargs):
        unloads.append(kwargs)
        return unload_nllb(**kwargs)

    monkeypatch.setattr(Model, "generate", track_generate)
    monkeypatch.setattr(nllb.torch.mps, "empty_cache", empty_cache)
    monkeypatch.setattr(nllb.router, "_unload_nllb", track_unload)
    segments = [{"id": str(index), "text": f"row {index}"} for index in range(row_count)]
    response = translate(nllb, segments=segments)

    assert [{"id": row["id"], "text": row["text"]} for row in response["translated"]] == segments
    assert all(row.get("error") for row in response["translated"])
    if row_count == 1:
        assert response["translated"][0]["error"] == "placement failed on cpu"
    assert len(calls) == 2
    assert len(input_refs) == 2
    assert flush_lifetimes == [(True, True)]
    assert nllb.flushes == [{"device": "mps"}]
    assert unloads == [{"devices": ()}, {"devices": ("mps",)}]
    assert nllb.router._nllb_model is None
    assert nllb.router._nllb_tokenizer is None
    assert nllb.router._nllb_device is None
    assert "MPS generate failed, retrying on CPU: generation failed on mps" in nllb_warnings

    monkeypatch.setenv("OMNIVOICE_UNLOAD_NLLB", "0")
    assert_translated(translate(nllb))
    assert len(nllb.models) == 1
    assert nllb.models[0].calls[-1][0] == "mps"


def test_gpu_oom_keeps_individual_row_recovery_on_the_selected_device(nllb):
    nllb.family = "rocm"
    assert_translated(translate(nllb))
    model = nllb.models[0]

    def fail_batch():
        if len(model.calls[-1][1]) > 1:
            raise RuntimeError("CUDA out of memory")

    model.on_generate = fail_batch
    response = translate(nllb, segments=[
        {"id": "1", "text": "one"}, {"id": "2", "text": "two"}, {"id": "3", "text": "three"},
    ])
    assert response["translated"] == [
        {"id": "1", "text": "deu_Latn:one"}, {"id": "2", "text": "deu_Latn:two"},
        {"id": "3", "text": "deu_Latn:three"},
    ]
    assert [len(call[1]) for call in model.calls] == [1, 2, 1, 1, 1]
    assert all(call[0] == "cuda" for call in model.calls)
    assert nllb.flushes == [{"device": "cuda"}]


def test_device_changes_preserve_batch_targets_order_and_empty_rows(nllb):
    assert_translated(translate(nllb))
    nllb.family = "rocm"
    response = translate(nllb, segments=[
        {"id": "1", "text": "one"}, {"id": "2", "text": "two", "target_lang": "es"},
        {"id": "3", "text": " "}, {"id": "4", "text": "four"}, {"id": "5", "text": "five"},
    ])
    assert response["translated"] == [
        {"id": "1", "text": "deu_Latn:one"}, {"id": "2", "text": "spa_Latn:two"},
        {"id": "3", "text": " "}, {"id": "4", "text": "deu_Latn:four"},
        {"id": "5", "text": "deu_Latn:five"},
    ]
    assert [len(call[1]) for call in nllb.models[0].calls] == [1, 2, 1, 1]
    assert all(call[0] == "cuda" for call in nllb.models[0].calls[1:])


def test_concurrent_jobs_do_not_relocate_or_unload_an_active_model(nllb, monkeypatch):
    assert_translated(translate(nllb))
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    model = nllb.models[0]

    def hold_generation():
        entered.set()
        assert release.wait(5)

    model.on_generate = hold_generation
    monkeypatch.setattr(nllb.router, "_should_unload_nllb", lambda: True)

    async def run():
        first = asyncio.create_task(nllb.router.dub_translate(request()))
        assert await asyncio.to_thread(entered.wait, 5)
        nllb.family = "rocm"
        second = asyncio.create_task(nllb.router.dub_translate(request()))
        asyncio.get_running_loop().call_later(0.15, second_started.set)
        try:
            assert await asyncio.to_thread(second_started.wait, 5)
            assert len(model.calls) == 2
            assert model.devices == ["cpu", "cpu"]
            assert nllb.router._nllb_model is model
        finally:
            release.set()
            responses = await asyncio.gather(first, second)
        for response in responses:
            assert_translated(response)

    asyncio.run(run())
    assert len(nllb.models) == 2
    assert nllb.models[-1].calls[-1][0] == "cuda"
    assert nllb.router._nllb_model is None


def test_explicit_unload_waits_for_active_generation(nllb):
    assert_translated(translate(nllb))
    entered, release, unloading = threading.Event(), threading.Event(), threading.Event()
    model = nllb.models[0]

    def hold_generation():
        entered.set()
        assert release.wait(5)

    def unload():
        unloading.set()
        nllb.router._unload_nllb()

    model.on_generate = hold_generation

    async def run():
        pending = asyncio.create_task(nllb.router.dub_translate(request()))
        assert await asyncio.to_thread(entered.wait, 5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(unload)
            try:
                assert await asyncio.to_thread(unloading.wait, 5)
                await asyncio.sleep(0.05)
                assert not future.done()
                assert nllb.router._nllb_model is model
            finally:
                release.set()
                response = await pending
                future.result(timeout=5)
        assert_translated(response)

    asyncio.run(run())
    assert nllb.router._nllb_model is None
