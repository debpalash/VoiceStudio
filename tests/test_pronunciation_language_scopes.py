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


@pytest.mark.parametrize("operation", ["create", "update", "import"])
@pytest.mark.parametrize("code", ["cmn", "zho"])
def test_known_chinese_iso_scopes_match_script_tags(client, operation, code):
    entry = {"term": "GIF", "replacement": "Chinese", "language": code}
    if operation == "create":
        result = client.post("/pronunciation", json=entry)
    elif operation == "update":
        initial = client.post(
            "/pronunciation", json={**entry, "language": "*"},
        ).json()
        result = client.put(
            f"/pronunciation/{initial['id']}", json={"language": code},
        )
    else:
        result = client.post("/pronunciation/import", json={"entries": [entry]})
    assert result.status_code == 200
    for language in (code, f"{code}-Hans", f"{code}-Hant", f"{code}_Hans"):
        result = client.post(
            "/pronunciation/test", json={"text": "GIF", "language": language},
        )
        assert result.status_code == 200
        assert result.json()["substituted"] == "Chinese"
    unrelated = client.post(
        "/pronunciation/test", json={"text": "GIF", "language": "spa-MX"},
    )
    assert unrelated.json()["substituted"] == "GIF"


@pytest.mark.parametrize("operation", ["create", "update", "import"])
@pytest.mark.parametrize("code", ["cmn", "zho"])
@pytest.mark.parametrize("reverse", [False, True])
def test_chinese_script_scopes_keep_identity_and_precedence(
    client, operation, code, reverse,
):
    entries = [
        {"term": "GIF", "replacement": "global", "language": "*"},
        {"term": "gif", "replacement": "base", "language": code},
        {"term": "GIF", "replacement": "simplified", "language": f"{code}-Hans"},
        {"term": "gif", "replacement": "traditional", "language": f"{code}_Hant"},
    ]
    for entry in reversed(entries) if reverse else entries:
        if operation == "create":
            result = client.post("/pronunciation", json=entry)
        elif operation == "update":
            initial = client.post(
                "/pronunciation", json={**entry, "language": "*"},
            ).json()
            result = client.put(
                f"/pronunciation/{initial['id']}",
                json={"language": entry["language"]},
            )
        else:
            result = client.post("/pronunciation/import", json={"entries": [entry]})
        assert result.status_code == 200
    exported = client.get("/pronunciation/export").json()["entries"]
    assert {entry["language"] for entry in exported} == {
        "*", code, f"{code}-hans", f"{code}-hant",
    }
    # Export/import retains the explicit script identities, not just live rows.
    for entry in client.get("/pronunciation").json():
        assert client.delete(f"/pronunciation/{entry['id']}").status_code == 200
    assert client.post(
        "/pronunciation/import", json={"entries": exported},
    ).status_code == 200
    from services.pronunciation import apply_lexicon, load_dict_for_request

    for language, expected in [
        (code, "base"), (f"{code}-Hans", "simplified"),
        (f"{code}_Hant", "traditional"), ("spa-MX", "global"),
    ]:
        result = client.post(
            "/pronunciation/test", json={"text": "GIF gif", "language": language},
        )
        assert result.status_code == 200
        assert result.json()["substituted"] == f"{expected} {expected}"
        assert apply_lexicon(
            "GIF gif", load_dict_for_request(language),
        ) == f"{expected} {expected}"


@pytest.mark.parametrize("code", ["cmn", "zho"])
def test_unknown_chinese_suffixes_remain_distinct_literals(client, code):
    for suffix in ("custom", "another"):
        entry = {"term": "GIF", "replacement": suffix, "language": f"{code}-{suffix}"}
        assert client.post("/pronunciation", json=entry).status_code == 200
    exported = client.get("/pronunciation/export").json()["entries"]
    assert {entry["language"] for entry in exported} == {
        f"{code}-custom", f"{code}-another",
    }
    for suffix in ("custom", "another"):
        result = client.post(
            "/pronunciation/test", json={"text": "GIF", "language": f"{code}-{suffix}"},
        )
        assert result.json()["substituted"] == suffix
    for language in (code, f"{code}-Hans", f"{code}-Hant"):
        result = client.post(
            "/pronunciation/test", json={"text": "GIF", "language": language},
        )
        assert result.json()["substituted"] == "GIF"


@pytest.mark.parametrize("code", ["cmn", "zho"])
def test_inert_chinese_rows_share_supported_scope_matching(client, code):
    for term, language, enabled in [
        ("global", "*", True), ("base", code, True),
        ("simplified", f"{code}-Hans", True),
        ("traditional", f"{code}-Hant", True),
        ("disabled", f"{code}-Hans", False),
    ]:
        entry = {
            "term": term, "replacement": "dʒɪf", "type": "ipa",
            "language": language, "enabled": enabled,
        }
        assert client.post("/pronunciation", json=entry).status_code == 200
    result = client.post(
        "/pronunciation/test", json={"text": "GIF", "language": f"{code}-Hans"},
    ).json()
    assert result["substituted"] == "GIF"
    assert {entry["term"] for entry in result["inert_entries"]} == {
        "global", "base", "simplified",
    }


@pytest.mark.parametrize('language', ['Spanish', 'es', 'es-MX', 'sp'])
def test_saved_legacy_spanish_scope_still_applies_and_round_trips(client, language):
    from core.db import db_conn
    from services.pronunciation import apply_lexicon, load_dict_for_request

    # Old POST /pronunciation stored the picker name Spanish as its first two
    # letters. Insert the actual old representation, bypassing today's writer.
    with db_conn() as conn:
        conn.execute(
            'INSERT INTO pronunciation_entries '
            '(id, term, replacement, type, language, enabled, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('legacy-spanish', 'GIF', 'jiff', 'respelling', 'sp', 1, '2026-01-01'),
        )
    assert client.post('/pronunciation/test', json={
        'text': 'GIF', 'language': language,
    }).json()['substituted'] == 'jiff'
    assert apply_lexicon('GIF', load_dict_for_request(language)) == 'jiff'
    exported = client.get('/pronunciation/export').json()
    assert exported['entries'][0]['language'] == 'sp', 'reads must not rewrite user rows'
    assert client.post('/pronunciation/import', json={**exported, 'replace': True}).status_code == 200
    assert client.get('/pronunciation/export').json()['entries'][0]['language'] == 'es'
    assert client.post('/pronunciation/test', json={
        'text': 'GIF', 'language': language,
    }).json()['substituted'] == 'jiff'


def test_legacy_spanish_never_leaks_into_another_bundled_language():
    from omnivoice.utils.lang_map import LANG_NAME_TO_ID
    from services.pronunciation import apply_pronunciation

    row = {'term': 'GIF', 'replacement': 'jiff', 'type': 'respelling',
           'language': 'sp', 'enabled': 1}
    for name, code in LANG_NAME_TO_ID.items():
        expected = 'jiff' if code == 'es' else 'GIF'
        assert apply_pronunciation('GIF', [row], name) == expected, name
        assert apply_pronunciation('GIF', [row], code) == expected, code


@pytest.mark.parametrize('scope,languages', [
    ('ge', ['German', 'Georgian', 'de', 'ka']),
    ('po', ['Polish', 'Portuguese', 'pl', 'pt']),
    ('ak', ['Akebu', 'keu']),
    ('qu', ['Quiotepec Chinantec', 'chq']),
    ('vo', ['Votic', 'vot']),
])
def test_other_legacy_or_literal_codes_are_not_guessed(scope, languages):
    from services.pronunciation import apply_pronunciation, normalize_language_scope

    # A unique picker prefix is insufficient: e.g. ak/qu/vo are real ISO codes
    # outside this picker, while ge/po have multiple possible source names.
    row = {'term': 'GIF', 'replacement': 'literal', 'type': 'respelling',
           'language': scope, 'enabled': 1}
    assert normalize_language_scope(scope) == scope
    assert apply_pronunciation('GIF', [row], scope) == 'literal'
    for language in languages:
        assert apply_pronunciation('GIF', [row], language) == 'GIF'
