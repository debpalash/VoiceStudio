"""Translation must work on LM Studio, not just on Ollama (#1332).

A user reported that translation works through Ollama and fails through LM
Studio on the same machine — a clean A/B that rules out the surrounding
pipeline. The cause is in the provider table: LM Studio's `default_model` was
the string ``"local-model"``, which is a placeholder and not a model id. LM
Studio serves whatever the user has loaded and rejects a name it does not know,
so every request 404s. Ollama's default is ``llama3.1`` — a real name that
people actually pull — so the same code path worked there.

There is no name we could ship that would be right, because the answer depends
on what the user loaded. So the fix asks the server, and these tests pin the
parts that would silently regress: that discovery never overrides a user's
explicit choice, that it stays off for providers with real defaults, and that a
server which is down or empty leaves the caller no worse off.
"""
import importlib
import os
import sys

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


@pytest.fixture
def llm():
    """Resolve at call time — sibling suites reload/purge ``services.*``."""
    mod = importlib.import_module("services.llm_providers")
    mod.forget_discovered_models()
    yield mod
    mod.forget_discovered_models()


@pytest.fixture(autouse=True)
def _no_env_or_store(monkeypatch, llm):
    """A clean slate: no env pins, and a settings store that answers empty.

    Without this the developer's own configuration decides the result, which is
    how a test like this passes on one machine and fails on another.

    Discovery first asks LM Studio's native API which typed model is resident.
    A live developer server would otherwise leak its model into these cases, so
    the transport reports an empty native list unless a test supplies metadata;
    dedicated probe tests exercise the transport itself.
    """
    for var in ("LMSTUDIO_MODEL", "LMSTUDIO_BASE_URL", "OLLAMA_MODEL"):
        monkeypatch.delenv(var, raising=False)
    import services.settings_store as store
    monkeypatch.setattr(store, "get_text", lambda *a, **k: "")
    import urllib.request
    from types import SimpleNamespace
    import io
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(
        open=lambda *args, **kwargs: io.BytesIO(b'{"data": []}')))


def _expire(llm, pid):
    """Age a cache entry past its TTL without touching the clock.

    ``time.monotonic`` is the stdlib's, shared with sqlite and logging, so
    freezing it breaks the settings store underneath the test — rewriting the
    stored expiry tests the same branch without that blast radius.
    """
    value, _ = llm._DISCOVERED_MODEL[pid]
    llm._DISCOVERED_MODEL[pid] = (value, 0.0)


def _fake_openai(monkeypatch, llm, ids, raises=None):
    """Stub the OpenAI SDK's models.list for the discovery path."""
    calls = {"n": 0}

    class _Models:
        def list(self, timeout=None):
            calls["n"] += 1
            if raises:
                raise raises
            return [type("M", (), {"id": i})() for i in ids]

    class _Client:
        def __init__(self, **kw):
            self.models = _Models()

    fake = type("openai", (), {"OpenAI": _Client})
    monkeypatch.setitem(sys.modules, "openai", fake)
    import io
    import json
    import urllib.request
    from types import SimpleNamespace
    def native_response(*args, **kwargs):
        models = _Models().list()
        return io.BytesIO(json.dumps({'data': [
            {'id': model.id, 'type': 'llm', 'state': 'loaded'} for model in models
        ]}).encode())
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=native_response))
    return calls


def test_placeholder_is_replaced_by_a_real_loaded_model(monkeypatch, llm):
    """The reported bug: without this, `local-model` goes on the wire."""
    _fake_openai(monkeypatch, llm, ["qwen2.5-7b-instruct"])
    p = llm.get_provider("lmstudio")
    assert llm.resolve_model(p) == "qwen2.5-7b-instruct"


def test_an_explicit_model_always_wins(monkeypatch, llm):
    """Discovery sits BELOW the user's choice. A setting that gets silently
    replaced by whatever the server happens to serve is a worse bug than the
    one being fixed."""
    import services.settings_store as store
    monkeypatch.setattr(store, "get_text",
                        lambda key, *a, **k: "my-pick" if "model" in key else "")
    calls = _fake_openai(monkeypatch, llm, ["something-else"])
    p = llm.get_provider("lmstudio")
    assert llm.resolve_model(p) == "my-pick"
    assert calls["n"] == 0, "discovery ran despite an explicit model being set"


def test_env_pin_also_wins(monkeypatch, llm):
    monkeypatch.setenv("LMSTUDIO_MODEL", "from-env")
    calls = _fake_openai(monkeypatch, llm, ["something-else"])
    p = llm.get_provider("lmstudio")
    assert llm.resolve_model(p) == "from-env"
    assert calls["n"] == 0


def test_providers_with_a_real_default_never_probe(monkeypatch, llm):
    """Ollama's default is a real model name, and every cloud provider's is
    too. Probing them would add a network round-trip to resolve a value that
    was already correct — and for a cloud provider, on every call."""
    calls = _fake_openai(monkeypatch, llm, ["irrelevant"])
    for pid, expected in (("ollama", "llama3.1"), ("openai", "gpt-4o-mini")):
        assert llm.resolve_model(llm.get_provider(pid)) == expected
    assert calls["n"] == 0


def test_server_down_falls_back_to_the_default(monkeypatch, llm):
    """Discovery is best-effort: a local server that is not running must leave
    the caller exactly where it was, not raise inside a translation."""
    _fake_openai(monkeypatch, llm, [], raises=ConnectionError("refused"))
    p = llm.get_provider("lmstudio")
    assert llm.resolve_model(p) == "local-model"


def test_server_with_no_models_falls_back(monkeypatch, llm):
    """LM Studio running with nothing loaded returns an empty list. Picking
    from it would be an IndexError in the request path."""
    _fake_openai(monkeypatch, llm, [])
    assert llm.resolve_model(llm.get_provider("lmstudio")) == "local-model"


def test_choice_is_deterministic_across_runs(monkeypatch, llm):
    """Several models loaded: two runs on one machine must pick the same one,
    or a bug report stops being reproducible."""
    _fake_openai(monkeypatch, llm, ["zeta", "alpha", "mid"])
    p = llm.get_provider("lmstudio")
    first = llm.resolve_model(p)
    llm.forget_discovered_models()
    _fake_openai(monkeypatch, llm, ["mid", "zeta", "alpha"])  # different order
    assert llm.resolve_model(p) == first == "alpha"


def test_discovery_is_cached(monkeypatch, llm):
    """Translation resolves the model per segment; a round-trip each time would
    turn a working setup into a slow one."""
    calls = _fake_openai(monkeypatch, llm, ["qwen2.5-7b-instruct"])
    p = llm.get_provider("lmstudio")
    for _ in range(5):
        llm.resolve_model(p)
    assert calls["n"] == 1


def test_saving_a_base_url_drops_the_cache(monkeypatch, llm):
    """Pointing at a different server must not keep serving the old server's
    model — that would make the user's edit look like it did nothing."""
    _fake_openai(monkeypatch, llm, ["first-server-model"])
    p = llm.get_provider("lmstudio")
    assert llm.resolve_model(p) == "first-server-model"

    import services.settings_store as store
    monkeypatch.setattr(store, "set_text", lambda *a, **k: None)
    llm.save_overrides("lmstudio", base_url="http://localhost:9999/v1")

    _fake_openai(monkeypatch, llm, ["second-server-model"])
    assert llm.resolve_model(p) == "second-server-model"


def test_lmstudio_is_the_only_placeholder_today():
    """A guard on the flag itself: marking a provider as a placeholder makes
    every resolve for it hit the network, so it should be a deliberate act."""
    import services.llm_providers as mod
    flagged = {p.id for p in mod.all_providers() if p.model_is_placeholder}
    assert flagged == {"lmstudio"}, (
        f"model_is_placeholder changed to {flagged}. Each one adds a network "
        f"probe to model resolution — intended?"
    )


def test_a_failed_probe_is_not_retried_per_segment(monkeypatch, llm):
    """A stopped server must cost one 5s timeout, not one per segment.

    Translation resolves the model for every segment it renders, so an uncached
    failure turns a 200-segment dub into 1000 seconds of discovering nothing —
    worse than the bug this whole path fixes (greptile / CodeRabbit).
    """
    calls = _fake_openai(monkeypatch, llm, [], raises=ConnectionError("refused"))
    p = llm.get_provider("lmstudio")
    for _ in range(20):
        assert llm.resolve_model(p) == "local-model"
    assert calls["n"] == 1, (
        f"probed {calls['n']} times against a server that is down; each one is "
        f"a 5s timeout in the request path"
    )


def test_an_empty_server_is_also_remembered(monkeypatch, llm):
    """Running-but-nothing-loaded is just as expensive to re-probe as down."""
    calls = _fake_openai(monkeypatch, llm, [])
    p = llm.get_provider("lmstudio")
    for _ in range(10):
        llm.resolve_model(p)
    assert calls["n"] == 1


def test_a_remembered_failure_expires(monkeypatch, llm):
    """...but it must expire, or starting the server would need an app restart."""
    _fake_openai(monkeypatch, llm, [], raises=ConnectionError("refused"))
    p = llm.get_provider("lmstudio")
    assert llm.resolve_model(p) == "local-model"

    # Age the entry rather than patching time.monotonic: that name is the
    # stdlib's, shared with sqlite and logging, and freezing it breaks the
    # settings store underneath the test.
    _expire(llm, "lmstudio")
    _fake_openai(monkeypatch, llm, ["now-its-up"])
    assert llm.resolve_model(p) == "now-its-up", (
        "a failure was remembered forever; the user starts LM Studio and "
        "nothing works until they restart OmniVoice"
    )


def test_a_discovered_model_expires(monkeypatch, llm):
    """The user can swap the loaded model inside LM Studio without touching
    OmniVoice. An unbounded cache keeps sending the unloaded name and 404s
    every translation until a restart (greptile)."""
    _fake_openai(monkeypatch, llm, ["first-model"])
    p = llm.get_provider("lmstudio")
    assert llm.resolve_model(p) == "first-model"

    _expire(llm, "lmstudio")
    _fake_openai(monkeypatch, llm, ["swapped-model"])
    assert llm.resolve_model(p) == "swapped-model"


def test_a_fresh_discovery_is_still_reused_within_its_ttl(monkeypatch, llm):
    """The TTL must not defeat the caching it bounds."""
    calls = _fake_openai(monkeypatch, llm, ["m"])
    p = llm.get_provider("lmstudio")
    llm.resolve_model(p)
    llm.resolve_model(p)


def test_native_loaded_model_wins_and_is_cached(monkeypatch, llm):
    listing_calls = _fake_openai(monkeypatch, llm, ["alpha", "zeta"])
    probe_calls = {"n": 0}

    def probe(url, api_key="local"):
        probe_calls["n"] += 1
        return "zeta"

    monkeypatch.setattr(llm, "_probe_lmstudio_loaded_model", probe)
    provider = llm.get_provider("lmstudio")
    assert llm.resolve_model(provider) == "zeta"
    assert llm.resolve_model(provider) == "zeta"
    assert probe_calls["n"] == 1
    assert listing_calls["n"] == 0
