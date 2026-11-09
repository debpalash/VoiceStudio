import asyncio

from services.audiobook import Chapter, Span


def _run_with_text(monkeypatch, text, engine_id="known", engine_class=object, backend_lookup=None):
    from api.routers import audiobook
    from services import gpu_gateway, model_manager, tts_backend

    captured = {}

    async def prepare(*args, **kwargs):
        return object(), 24000, lambda _voice: {}, engine_id

    async def run(*, local, **kwargs):
        captured["call"] = local
        return local

    monkeypatch.setattr(audiobook, "_prepare_synth", prepare)
    monkeypatch.setattr(audiobook, "_resolve_voice", lambda _voice: {})
    monkeypatch.setattr(audiobook.voice_leases, "hold", lambda *args: None)
    monkeypatch.setattr(gpu_gateway, "run", run)
    monkeypatch.setattr(
        tts_backend,
        "get_backend_class",
        backend_lookup or (lambda _id: engine_class),
    )
    chapter = Chapter(title="One", spans=[Span(voice_id=None, text=text, pause_ms_after=0)])
    asyncio.run(audiobook._run_chapter(
        chapter,
        default_voice=None,
        language=None,
        opts=object(),
        voice_map={},
        lexicon=None,
        cache_dir="unused",
    ))
    return captured["call"], model_manager.generate_timeout_s(text, engine=engine_class)


def test_long_chapter_has_larger_execution_timeout(monkeypatch):
    short, _ = _run_with_text(monkeypatch, "brief")
    long, _ = _run_with_text(monkeypatch, "word " * 4000)
    assert long.timeout > short.timeout


def test_registered_engine_class_reaches_timeout_policy(monkeypatch):
    class RegisteredEngine:
        pass

    from services import model_manager
    seen = {}
    original = model_manager.generate_timeout_s

    def timeout(text, *, engine=None, **kwargs):
        seen["engine"] = engine
        return original(text, engine=engine, **kwargs)

    monkeypatch.setattr(model_manager, "generate_timeout_s", timeout)
    _run_with_text(monkeypatch, "hello", engine_class=RegisteredEngine)
    assert seen["engine"] is RegisteredEngine


def test_unknown_engine_uses_host_and_text_timeout(monkeypatch):
    from services import model_manager, tts_backend
    seen = {}
    original = model_manager.generate_timeout_s

    def missing(_id):
        raise ValueError("not registered")

    def timeout(text, *, engine=None, **kwargs):
        seen["engine"] = engine
        return original(text, engine=engine, **kwargs)

    monkeypatch.setattr(model_manager, "generate_timeout_s", timeout)
    _run_with_text(
        monkeypatch,
        "hello",
        engine_id="third-party",
        engine_class=None,
        backend_lookup=missing,
    )
    assert seen["engine"] is None
