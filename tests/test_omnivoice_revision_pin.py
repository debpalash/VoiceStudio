"""OmniVoice loads follow the reviewed pin, never the Hub's moving ``main``."""
from types import SimpleNamespace

import pytest

PIN = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
FALLBACK_PIN = "528e871c2a26c4f0f7773b9754e2e1acae20899d"


@pytest.fixture
def ov():
    """The OmniVoice module, resolved at test time (never a stale import)."""
    import importlib

    return importlib.import_module("omnivoice.models.omnivoice")


@pytest.fixture
def seen(monkeypatch, tmp_path, ov):
    """Record every Hub-facing call made by ``OmniVoice.from_pretrained``."""
    import huggingface_hub

    calls = {"model": [], "tokenizer": [], "snapshot": [], "audio": [], "extractor": []}
    snap = tmp_path / "snap"
    (snap / "audio_tokenizer").mkdir(parents=True)

    monkeypatch.setattr(
        ov.PreTrainedModel, "from_pretrained",
        classmethod(lambda cls, *a, **k: calls["model"].append(k) or SimpleNamespace(device="cpu")),
    )
    monkeypatch.setattr(
        ov.AutoTokenizer, "from_pretrained",
        lambda *a, **k: calls["tokenizer"].append(k) or object(),
    )

    def fake_snapshot(repo_id, *a, **k):
        calls["snapshot"].append(k)
        if k.get("local_files_only"):
            raise FileNotFoundError("cache miss")
        return str(snap)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)

    class Audio:
        @classmethod
        def from_pretrained(cls, path, **k):
            calls["audio"].append((path, k))
            return object()

    monkeypatch.setattr(ov, "_audio_tokenizer_cls", lambda: Audio)
    monkeypatch.setattr(
        ov.AutoFeatureExtractor, "from_pretrained",
        lambda path, **k: calls["extractor"].append((path, k)) or SimpleNamespace(sampling_rate=24000),
    )
    calls["snap"] = snap
    return calls


def test_revision_reaches_every_hub_call(seen, ov):
    ov.OmniVoice.from_pretrained("k2-fsa/OmniVoice", revision=PIN)
    assert seen["model"][0]["revision"] == PIN
    assert seen["tokenizer"][0]["revision"] == PIN
    # Both the cache-first and the network snapshot_download are pinned.
    assert [k.get("revision") for k in seen["snapshot"]] == [PIN, PIN]


def test_local_dir_ignores_revision_and_stays_offline(seen, tmp_path, ov):
    local = tmp_path / "local"
    (local / "audio_tokenizer").mkdir(parents=True)
    ov.OmniVoice.from_pretrained(str(local), revision=PIN)
    assert "revision" not in seen["model"][0]
    assert "revision" not in seen["tokenizer"][0]
    assert seen["snapshot"] == []


def test_unpinned_custom_repo_is_unchanged(seen, ov):
    ov.OmniVoice.from_pretrained("org/custom")
    assert "revision" not in seen["model"][0]
    assert "revision" not in seen["tokenizer"][0]
    assert seen["snapshot"] == [{"local_files_only": True}, {}]


def test_fallback_audio_tokenizer_is_pinned(seen, ov):
    seen["snap"].joinpath("audio_tokenizer").rmdir()
    ov.OmniVoice.from_pretrained(
        "k2-fsa/OmniVoice", revision=PIN,
        allow_tokenizer_fallback=True, tokenizer_fallback_revision=FALLBACK_PIN,
    )
    path, kwargs = seen["audio"][0]
    assert path == ov._AUDIO_TOKENIZER_FALLBACK_REPO
    assert kwargs["revision"] == FALLBACK_PIN
    assert seen["extractor"][0][1]["revision"] == FALLBACK_PIN


def test_bundled_audio_tokenizer_is_a_local_path_without_revision(seen, ov):
    ov.OmniVoice.from_pretrained("k2-fsa/OmniVoice", revision=PIN)
    assert "revision" not in seen["audio"][0][1]


def test_backend_load_kwargs_follow_the_curated_pins(tmp_path, monkeypatch):
    from services.omnivoice_checkpoint import omnivoice_pin_kwargs

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))  # fresh install

    assert omnivoice_pin_kwargs("k2-fsa/OmniVoice") == {
        "revision": PIN, "tokenizer_fallback_revision": FALLBACK_PIN,
    }
    assert omnivoice_pin_kwargs("org/custom") == {"tokenizer_fallback_revision": FALLBACK_PIN}
    assert omnivoice_pin_kwargs(str(tmp_path)) == {}


def test_asr_load_is_pinned_and_local_dir_is_not(monkeypatch, tmp_path, ov):
    import sys

    seen = []
    fake = SimpleNamespace(pipeline=lambda *a, **k: seen.append(k) or object())
    monkeypatch.setitem(sys.modules, "transformers", fake)
    model = ov.OmniVoice.__new__(ov.OmniVoice)
    monkeypatch.setattr(ov.OmniVoice, "device", property(lambda self: "cpu"))
    model.load_asr_model(revision=PIN)
    model.load_asr_model(model_name=str(tmp_path), revision=PIN)
    assert seen[0]["revision"] == PIN
    assert "revision" not in seen[1]


def test_backend_asr_pin_and_every_call_site_use_it(tmp_path, monkeypatch):
    import pathlib
    from services.omnivoice_checkpoint import omnivoice_asr_pin_kwargs
    from services.hf_revisions import revision_for

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))  # fresh install
    assert omnivoice_asr_pin_kwargs() == {"revision": revision_for("openai/whisper-large-v3-turbo")}
    root = pathlib.Path(__file__).resolve().parents[1] / "backend"
    sub = (root / "engines/omnivoice_subprocess/main.py").read_text(encoding="utf-8")
    mm = (root / "services/model_manager.py").read_text(encoding="utf-8")
    assert "omnivoice_pin_kwargs(checkpoint)" in sub and "omnivoice_asr_pin_kwargs()" in sub
    # The offline weight prefetch resolves the same installed-or-pinned snapshot.
    assert 'omnivoice_pin_kwargs(checkpoint).get("revision")' in sub
    assert "omnivoice_pin_kwargs(checkpoint)" in mm
    assert "load_asr_model(**omnivoice_asr_pin_kwargs())" in mm


def test_an_existing_cache_keeps_its_installed_revision(tmp_path, monkeypatch):
    """Backward compatibility: a user who already has an OmniVoice snapshot
    (recorded in ``refs/main`` by an older install) keeps loading it offline,
    with no multi-GB re-download to the curated pin."""
    from services.omnivoice_checkpoint import omnivoice_asr_pin_kwargs, omnivoice_pin_kwargs

    installed = "1" * 40
    cache = tmp_path / "hub"
    for repo in ("models--k2-fsa--OmniVoice", "models--openai--whisper-large-v3-turbo"):
        refs = cache / repo / "refs"
        refs.mkdir(parents=True)
        (refs / "main").write_text(installed, encoding="ascii")
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    assert omnivoice_pin_kwargs("k2-fsa/OmniVoice")["revision"] == installed
    assert omnivoice_asr_pin_kwargs() == {"revision": installed}
