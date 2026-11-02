"""scripts/update_contributors.py: filtering, sorting and generation, with no network."""
from __future__ import annotations

import importlib.util
import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("update_contributors", ROOT / "scripts/update_contributors.py")
uc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uc)


def _row(login: str, commits: int, kind: str = "User") -> dict:
    return {"login": login, "contributions": commits, "type": kind, "avatar_url": f"https://avatars.example/{login}?v=4"}


def _image(fmt: str, colour: str = "red", size: int = 300) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(out, format=fmt)
    return out.getvalue()


def test_select_keeps_humans_above_threshold_owner_first():
    rows = [
        _row("alice", 50), _row("debpalash", 20), _row("bob", 50), _row("Carol", 50),
        _row("dependabot[bot]", 900, "Bot"), _row("renovate[bot]", 90), _row("github-actions", 99),
        _row("Copilot", 40), _row("org", 40, "Organization"), _row("edge", 10), _row("over", 11),
    ]
    people = uc.select_contributors(rows)
    assert [p["login"] for p in people] == ["debpalash", "alice", "bob", "Carol", "over"]
    assert people[1] == {"login": "alice", "commits": 50, "avatar_url": "https://avatars.example/alice?v=4"}


def test_fetch_contributors_paginates():
    pages = {1: [_row("a", 20)] * 100, 2: [_row("b", 12)], 3: []}
    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        return json.dumps(pages[int(re.search(r"&page=(\d+)", url).group(1))]).encode()

    assert len(uc.fetch_contributors(fetch)) == 101
    assert all("per_page=100" in url and url.startswith("https://api.github.com/repos/debpalash/VoiceStudio/") for url in seen)


def test_avatar_url_requests_fixed_size():
    assert uc.avatar_url("https://a/u/1?v=4") == f"https://a/u/1?v=4&s={uc.AVATAR_SIZE}"
    assert uc.avatar_url("https://a/u/1") == f"https://a/u/1?s={uc.AVATAR_SIZE}"


def test_normalise_png_converts_and_resizes_jpeg():
    png = uc.normalise_png(_image("JPEG"))
    with Image.open(io.BytesIO(png)) as image:
        assert (image.format, image.size, image.mode) == ("PNG", (uc.AVATAR_SIZE, uc.AVATAR_SIZE), "RGB")
    assert uc.normalise_png(_image("JPEG")) == png  # deterministic


def test_rendered_files_satisfy_mobile_contract_parsers():
    people = [{"login": "debpalash", "commits": 9}, {"login": "Chang-Jin-Lee", "commits": 5}]
    ts = uc.render_ts(people, ["debpalash", "velixio"], "2026-01-02")
    assert re.findall(r"login: '([^']+)'", ts) == ["debpalash", "Chang-Jin-Lee"]
    assert uc.parse_core(ts) == ["debpalash", "velixio"] and uc.parse_date(ts) == "2026-01-02"
    assert "import avatar1 from './contributors/Chang-Jin-Lee.png';" in ts
    swift = uc.render_swift('    static let logins = ["old"]\n', ["a", "b"])
    assert swift == '    static let logins = ["a", "b"]\n'
    assert uc.render_kotlin('val logins = listOf("old", "x")', ["a"]) == 'val logins = listOf("a")'
    readme = '### Contributors\n\n<a href="https://github.com/old"><img src="https://github.com/old.png?size=96" x></a>\n\nThanks\n'
    out = uc.render_readme(readme, ["kevin9327"])
    assert 'href="https://github.com/kevin9327"' in out and "old" not in out and out.endswith("\n\nThanks\n")
    with pytest.raises(SystemExit):
        uc.render_readme("no row", ["a"])


def test_current_files_round_trip_unchanged():
    """The committed snapshot is exactly what the generator would write for the same data."""
    ts = uc.DATA_TS.read_text(encoding="utf-8")
    people = [{"login": l, "commits": int(c)} for l, c in re.findall(r"login: '([^']+)', commits: (\d+)", ts)]
    assert uc.render_ts(people, uc.parse_core(ts), uc.parse_date(ts)) == ts
    logins = [p["login"] for p in people]
    assert uc.render_swift(uc.SWIFT.read_text(encoding="utf-8"), logins) == uc.SWIFT.read_text(encoding="utf-8")
    assert uc.render_kotlin(uc.KOTLIN.read_text(encoding="utf-8"), logins) == uc.KOTLIN.read_text(encoding="utf-8")
    core = uc.parse_core(ts)
    for readme in uc.READMES:
        assert uc.render_readme(readme.read_text(encoding="utf-8"), [l for l in logins if l not in core], readme) == readme.read_text(encoding="utf-8")
    for login in logins:
        imageset = uc.XCASSETS / f"contributor-{login}.imageset"
        assert (imageset / "Contents.json").read_text(encoding="utf-8") == uc.imageset_contents(login)
        assert (imageset / f"{login}.png").read_bytes() == (uc.AVATARS / f"{login}.png").read_bytes()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "contributors").mkdir(parents=True)
    (home / "contributors/gone.jpg").write_bytes(b"x")
    (home / "home-contributors-data.ts").write_text(uc.render_ts([], ["debpalash"], "2020-01-01"))
    xcassets = tmp_path / "Contributors.xcassets"
    (xcassets / "contributor-gone.imageset").mkdir(parents=True)
    swift, kotlin = tmp_path / "H.swift", tmp_path / "H.kt"
    swift.write_text("static let logins = []\n")
    kotlin.write_text("val logins = listOf()\n")
    readme = tmp_path / "README.md"
    readme.write_text('<a href="https://github.com/x"><img src="https://github.com/x.png?size=96"></a>\n')
    for name, value in {
        "ROOT": tmp_path, "DATA_TS": home / "home-contributors-data.ts", "AVATARS": home / "contributors",
        "XCASSETS": xcassets, "SWIFT": swift, "KOTLIN": kotlin, "READMES": (readme,),
    }.items():
        monkeypatch.setattr(uc, name, value)
    return tmp_path


def test_main_writes_every_target_then_is_idempotent(sandbox):
    rows = [_row("debpalash", 30), _row("ana", 12), _row("bot[bot]", 99, "Bot")]

    def fetch(url: str) -> bytes:
        if url.startswith("https://api.github.com/"):
            return json.dumps(rows if url.endswith("page=1") else []).encode()
        return _image("JPEG" if "ana" in url else "PNG", "blue" if "ana" in url else "green")

    assert uc.main(fetch) == 0
    ts = uc.DATA_TS.read_text(encoding="utf-8")
    assert re.findall(r"login: '([^']+)'", ts) == ["debpalash", "ana"] and "2020-01-01" not in ts
    assert sorted(p.name for p in uc.AVATARS.iterdir()) == ["ana.png", "debpalash.png"]
    assert sorted(p.name for p in uc.XCASSETS.iterdir()) == ["contributor-ana.imageset", "contributor-debpalash.imageset"]
    assert uc.SWIFT.read_text(encoding="utf-8") == 'static let logins = ["debpalash", "ana"]\n'
    assert uc.KOTLIN.read_text(encoding="utf-8") == 'val logins = listOf("debpalash", "ana")\n'
    assert "github.com/ana.png" in uc.READMES[0].read_text(encoding="utf-8") and "github.com/debpalash" not in uc.READMES[0].read_text(encoding="utf-8")

    # Same data on a later day: nothing changes, the snapshot date included.
    people = uc.select_contributors(rows)
    avatars = {p["login"]: uc.normalise_png(fetch(uc.avatar_url(p["avatar_url"]))) for p in people}
    assert uc.apply(people, avatars, "2099-12-31") == []
    assert uc.DATA_TS.read_text(encoding="utf-8") == ts


def test_main_refuses_without_owner(sandbox):
    assert uc.main(lambda url: json.dumps([_row("ana", 50)] if url.endswith("page=1") else []).encode()) == 1
    assert (uc.AVATARS / "gone.jpg").exists()
