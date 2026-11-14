"""Phone greeting reuse follows the same spoken text as actual synthesis."""
import asyncio
import importlib
import threading

from test_telephony_twilio import store, fake_engine as phone_engine  # noqa: F401
from test_text_normalization_routes import saved_pronunciations  # noqa: F401


def test_phone_cache_refreshes_only_when_effective_pronunciation_changes(
    store, phone_engine, saved_pronunciations,
):
    import test_telephony_twilio as phone

    db = importlib.import_module("core.db")

    async def render():
        return await phone.session.render_ulaw_all(
            "cats [[cats|cats]]", voice="pron-voice", engine="fake-phone", language="en",
        )

    assert asyncio.run(render())
    assert phone_engine.calls == ["felines cats"]
    assert asyncio.run(render())
    assert phone_engine.calls == ["felines cats"]
    with db.db_conn() as conn:
        conn.execute("UPDATE pronunciation_entries SET replacement='Lecturer' WHERE term='Doctor'")
        conn.execute("UPDATE pronunciation_entries SET replacement='unrelated' "
                     "WHERE term='cats' AND profile_id=''")
    assert asyncio.run(render())
    assert phone_engine.calls == ["felines cats"]
    with db.db_conn() as conn:
        conn.execute("UPDATE pronunciation_entries SET replacement='puppies' "
                     "WHERE term='cats' AND profile_id='pron-voice'")
    assert asyncio.run(render())
    assert phone_engine.calls == ["felines cats", "puppies cats"]


def test_phone_key_and_inference_share_prepared_text_when_dictionary_changes(
    store, phone_engine, saved_pronunciations, monkeypatch,
):
    import test_telephony_twilio as phone

    db = importlib.import_module("core.db")
    stream = importlib.import_module("api.routers.tts_stream")
    synthesize = stream.synthesize_stream

    async def change_after_key(*args, **kwargs):
        with db.db_conn() as conn:
            conn.execute("UPDATE pronunciation_entries SET replacement='puppies' "
                         "WHERE term='cats' AND profile_id=''")
        async for chunk in synthesize(*args, **kwargs):
            yield chunk

    monkeypatch.setattr(stream, "synthesize_stream", change_after_key)

    async def render():
        return await phone.session.render_ulaw_all(
            "cats [[cats|cats]]", engine="fake-phone", language="en",
        )

    assert asyncio.run(render())
    # Reapplying preparation would rewrite kittens through the second saved
    # entry; loading new rows after the key would instead speak puppies.
    assert phone_engine.calls == ["kittens cats"]
    assert asyncio.run(render())
    assert phone_engine.calls == ["kittens cats", "puppies cats"]


def test_phone_preparation_loads_one_snapshot_off_the_event_loop(
    store, phone_engine, saved_pronunciations, monkeypatch,
):
    import test_telephony_twilio as phone

    pronunciation = importlib.import_module("services.pronunciation")
    load = pronunciation.load_entries_from_db
    threads = []

    def record_load():
        threads.append(threading.get_ident())
        return load()

    monkeypatch.setattr(pronunciation, "load_entries_from_db", record_load)

    async def render():
        return await phone.session.render_ulaw_all("cats", engine="fake-phone", language="en")

    assert asyncio.run(render())
    assert phone_engine.calls == ["kittens"]
    assert len(threads) == 1 and threads[0] != threading.get_ident()
