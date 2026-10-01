"""Native pronunciation scopes match the language names used by synthesis."""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core import db
    from api.routers.pronunciation import router

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "pronunciation.db"))
    db.init_db()
    app = FastAPI()
    app.include_router(router)
    native_client = TestClient(app, client=("127.0.0.1", 50000))
    try:
        yield native_client
    finally:
        native_client.close()


@pytest.mark.parametrize("code,name", [("es", "Spanish"), ("de", "German"),
                                      ("pt", "Portuguese"), ("nl", "Dutch")])
def test_dictionary_code_matches_picker_name(client, code, name):
    created = client.post("/pronunciation", json={"term": "GIF", "replacement": "respelling",
                                                 "language": code})
    assert created.status_code == 200
    for language in (code, name, f"{code}-XX", f"{code}_XX"):
        result = client.post("/pronunciation/test", json={"text": "GIF", "language": language})
        assert result.status_code == 200
        assert result.json()["substituted"] == "respelling"


def test_spanish_dictionary_does_not_apply_to_estonian(client):
    assert client.post("/pronunciation", json={"term": "GIF", "replacement": "Spanish",
                                               "language": "es"}).status_code == 200
    for language in ("Estonian", "et", "et-EE"):
        result = client.post("/pronunciation/test", json={"text": "GIF", "language": language})
        assert result.json()["substituted"] == "GIF"
        assert result.json()["applied_terms"] == []


@pytest.mark.parametrize("operation", ["create", "update", "import"])
def test_saved_display_name_uses_same_canonical_scope(client, operation):
    entry = {"term": "GIF", "replacement": "respelling", "language": "Spanish"}
    if operation == "create":
        result = client.post("/pronunciation", json=entry)
    elif operation == "update":
        initial = client.post("/pronunciation", json={**entry, "language": "*"}).json()
        result = client.put(f"/pronunciation/{initial['id']}", json={"language": "Spanish"})
    else:
        result = client.post("/pronunciation/import", json={"entries": [entry]})
    assert result.status_code == 200
    exported = client.get("/pronunciation/export").json()["entries"]
    assert exported[0]["language"] == "es"
    assert client.post("/pronunciation/test", json={"text": "GIF", "language": "es"}).json()["substituted"] == "respelling"


def test_three_letter_language_ids_are_not_collapsed(client):
    # Abadi=kbt and Abron=abr come from the same bundled picker map as synthesis.
    client.post("/pronunciation", json={"term": "GIF", "replacement": "Abadi", "language": "kbt"})
    assert client.get("/pronunciation/export").json()["entries"][0]["language"] == "kbt"
    assert client.post("/pronunciation/test", json={"text": "GIF", "language": "Abadi"}).json()["substituted"] == "Abadi"
    assert client.post("/pronunciation/test", json={"text": "GIF", "language": "Abron"}).json()["substituted"] == "GIF"


def test_global_auto_and_legacy_literals_keep_their_meaning(client):
    for entry in [{"term": "GIF", "replacement": "global", "language": "*"},
                  {"term": "GIF", "replacement": "legacy", "language": "po"},
                  {"term": "GIF", "replacement": "Spanish", "language": "es"}]:
        assert client.post("/pronunciation", json=entry).status_code == 200
    for language, expected in [(None, "global"), ("Auto", "global"), ("*", "global"),
                               ("French", "global"), ("Portuguese", "global"),
                               ("Polish", "global"), ("po", "legacy"), ("Spanish", "Spanish")]:
        assert client.post("/pronunciation/test", json={"text": "GIF", "language": language}).json()["substituted"] == expected


def test_inert_entry_uses_same_canonical_scope(client):
    assert client.post("/pronunciation", json={"term": "GIF", "replacement": "dʒɪf",
                                               "type": "ipa", "language": "es"}).status_code == 200
    spanish = client.post("/pronunciation/test", json={"text": "GIF", "language": "Spanish"}).json()
    estonian = client.post("/pronunciation/test", json={"text": "GIF", "language": "Estonian"}).json()
    assert spanish["substituted"] == "GIF"
    assert spanish["inert_entries"] == [{"term": "GIF", "type": "ipa"}]
    assert estonian["inert_entries"] == []


@pytest.mark.parametrize("name,code", [("Mandarin", "zh"), ("Arabic", "ar"), ("Tagalog", "tl")])
def test_engine_language_aliases_match_dictionary_scopes(client, name, code):
    assert client.post("/pronunciation", json={"term": "GIF", "replacement": "alias",
                                               "language": name}).status_code == 200
    assert client.get("/pronunciation/export").json()["entries"][0]["language"] == code
    assert client.post("/pronunciation/test", json={"text": "GIF", "language": code}).json()["substituted"] == "alias"


@pytest.mark.parametrize("operation", ["create", "update", "import"])
def test_unknown_regional_scopes_remain_distinct_literals(client, operation):
    for language, replacement in [("spa-MX", "literal Mexico"), ("spa-ES", "literal Spain")]:
        entry = {"term": "GIF", "replacement": replacement, "language": language}
        if operation == "create":
            result = client.post("/pronunciation", json=entry)
        elif operation == "update":
            initial = client.post("/pronunciation", json={**entry, "language": "*"}).json()
            result = client.put(f"/pronunciation/{initial['id']}", json={"language": language})
        else:
            result = client.post("/pronunciation/import", json={"entries": [entry]})
        assert result.status_code == 200
    exported = client.get("/pronunciation/export").json()["entries"]
    assert {entry["language"] for entry in exported} == {"spa-mx", "spa-es"}
    for language, expected in [("spa-MX", "literal Mexico"), ("spa-ES", "literal Spain"),
                               ("spa", "GIF"), ("Spanish", "GIF"), ("es-MX", "GIF")]:
        result = client.post("/pronunciation/test", json={"text": "GIF", "language": language})
        assert result.status_code == 200
        assert result.json()["substituted"] == expected
