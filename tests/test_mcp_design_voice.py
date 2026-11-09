import asyncio
import json
from dataclasses import dataclass, field

import pytest

pytest.importorskip("mcp")


def _call(server, tool, **arguments):
    reply = asyncio.run(server.call_tool(tool, arguments))
    blocks = reply[0] if isinstance(reply, tuple) else reply
    return json.loads(blocks[0].text)


@dataclass
class BackendJournal:
    profiles: list[dict] = field(default_factory=list)
    generations: list[dict] = field(default_factory=list)


@pytest.fixture
def voice_tools(monkeypatch):
    from fastapi import FastAPI, Request, Response
    import mcp_server

    journal = BackendJournal()
    backend = FastAPI()

    @backend.post("/design/describe")
    async def map_description(request: Request):
        words = (await request.json())["description"].split()
        known = [word for word in words if word in {"male", "female", "elderly", "canadian"}]
        if not known:
            return {"attrs": {}, "instruct": "", "matched": [], "unmatched": words}
        attributes = {
            "gender": "female" if "female" in known else "male",
            "age": "elderly" if "elderly" in known else "Auto",
            "accent": "canadian" if "canadian" in known else "Auto",
        }
        return {
            "attrs": attributes,
            "instruct": "Render the mapped identity consistently.",
            "matched": known,
            "unmatched": [word for word in words if word not in known],
        }

    @backend.post("/profiles")
    async def store_profile(request: Request):
        fields = dict(await request.form())
        journal.profiles.append(fields)
        return {"id": "designed-42", "name": fields["name"], "kind": fields["kind"]}

    @backend.post("/generate")
    async def synthesize(request: Request):
        journal.generations.append(dict(await request.form()))
        return Response(b"WAVE", headers={"X-Audio-Id": "audio42"})

    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    return mcp_server.create_mcp_server(app=backend), journal


def test_voice_design_commands_are_exposed(voice_tools):
    server, _journal = voice_tools
    exposed = {definition.name for definition in asyncio.run(server.list_tools())}
    assert exposed.issuperset({"describe_voice", "design_voice"})


def test_description_preview_has_no_profile_side_effect(voice_tools):
    server, journal = voice_tools
    preview = _call(server, "describe_voice", description="female elderly")

    assert journal.profiles == []
    assert preview["attrs"] == {"gender": "female", "age": "elderly", "accent": "Auto"}


def test_profile_save_uses_the_preview_recipe(voice_tools):
    server, journal = voice_tools
    result = _call(
        server,
        "design_voice",
        name="Northwind",
        description="male canadian luminous",
        language="en-CA",
    )
    sent = journal.profiles[0]

    assert (sent["kind"], sent["language"], bool(sent["instruct"])) == ("design", "en-CA", True)
    assert json.loads(sent["vd_states"]) == {
        "gender": "male", "age": "Auto", "accent": "canadian",
    }
    assert result["profile_id"] == "designed-42"
    assert result["unmatched"] == ["luminous"]


def test_unrecognized_words_do_not_create_a_profile(voice_tools):
    server, journal = voice_tools
    refusal = _call(server, "design_voice", name="Blank", description="silvery textured")

    assert journal.profiles == []
    assert refusal["unmatched"] == ["silvery", "textured"]
    assert refusal.get("error")


def test_profile_creation_selects_the_long_timeout(monkeypatch, voice_tools):
    import mcp_server

    requested_modes = []
    monkeypatch.setattr(
        mcp_server,
        "_post_timeout_s",
        lambda mode=None, text=None: requested_modes.append(mode) or 123,
    )
    server, _journal = voice_tools
    _call(server, "design_voice", name="Measured", description="female")
    assert requested_modes.count("generate") == 1


@pytest.mark.parametrize("argument", [pytest.param(None, id="omitted"), "Auto", "de"])
def test_speech_language_form_distinguishes_omission(voice_tools, argument):
    server, journal = voice_tools
    request = {"text": "Guten Tag"}
    if argument is not None:
        request["language"] = argument
    _call(server, "generate_speech", **request)

    posted = journal.generations[-1]
    assert posted.get("language") == argument
    assert ("language" in posted) is (argument is not None)
