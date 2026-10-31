"""The sponsor roster lives in SPONSORS.md, the READMEs and the in-app config; they must agree."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "electron" / "src" / "shared" / "config" / "sponsors.js"
_READMES = [_ROOT / "README.md", *sorted((_ROOT / "docs" / "readme").glob("README_*.md"))]

_README_REPOSITORIES = {
    "OpenGTM": "/debpalash/OpenGTM",
    "Opal": "/debpalash/Opal",
    "Supermetry": "/velixio/supermetry",
}

_ENTRY = re.compile(
    r"\{\s*name:\s*'(?P<name>[^']+)',\s*logoUrl:\s*(?P<logo>[^,]+),\s*"
    r"url:\s*'(?P<url>[^']+)',\s*tier:\s*'(?P<tier>[^']+)',?\s*\}"
)
_LOGO_LINK = re.compile(r'<a href="(?P<url>[^"]+)"><picture>(?P<body>.*?)</picture></a>')
_ASSET = re.compile(r'(?:src|srcset)="([^"]+)"')
_ALT = re.compile(r'alt="([^"]+)"')


def _config_text() -> str:
    return _CONFIG.read_text(encoding="utf-8")


def _sponsors_array() -> str:
    text = _config_text()
    start = text.index("export const SPONSORS = [")
    return text[start : text.index("];", start)]


def _in_app_sponsors() -> list[dict[str, str]]:
    return [m.groupdict() for m in _ENTRY.finditer(_sponsors_array())]


def _in_app_tiers() -> list[str]:
    match = re.search(r"export const SPONSOR_TIERS = \[([^\]]*)\]", _config_text())
    assert match, "SPONSOR_TIERS not found"
    return re.findall(r"'([^']+)'", match.group(1))


def _current_sponsors_section() -> str:
    text = (_ROOT / "SPONSORS.md").read_text(encoding="utf-8")
    start = text.index("## Current sponsors")
    return text[start : text.index("<!--", start)]


def _logo_links(text: str) -> list[dict[str, str]]:
    return [
        {"url": m["url"], "name": _ALT.search(m["body"])[1], "assets": _ASSET.findall(m["body"])}
        for m in _LOGO_LINK.finditer(text)
    ]


def _host(url: str) -> str:
    return urlsplit(url).netloc


def test_in_app_tiers_match_funding_json():
    """The in-app tier keys are the funding.json sponsor tiers, highest first."""
    plans = json.loads((_ROOT / "funding.json").read_text(encoding="utf-8"))["funding"]["plans"]
    tiers = [p["guid"].removesuffix("-sponsor") for p in plans if p["guid"] != "one-time"]
    assert _in_app_tiers() == list(reversed(tiers))


def test_in_app_config_parses_every_entry():
    assert _sponsors_array().count("name:") == len(_in_app_sponsors()), "a SPONSORS entry no longer matches the parser"


@pytest.mark.parametrize("sponsor", _in_app_sponsors(), ids=lambda s: s["name"])
def test_in_app_sponsor_uses_a_bundled_logo_and_a_known_tier(sponsor):
    """CSP is img-src 'self' and the app is local-first: logos ship with the app, never from a URL."""
    assert sponsor["tier"] in _in_app_tiers()
    logo = sponsor["logo"].strip()
    assert not logo.startswith(("'", '"', "`")), f"{sponsor['name']}: logoUrl must be a bundled import"
    imported = re.search(rf"import {re.escape(logo)} from '([^']+)';", _config_text())
    assert imported, f"{sponsor['name']}: {logo} is not imported"
    assert (_CONFIG.parent / imported[1]).resolve().is_file()
    assert parse_qs(urlsplit(sponsor["url"]).query).get("utm_content") == ["in-app-support"]


def test_sponsors_md_and_the_app_list_the_same_sponsors():
    links = _logo_links(_current_sponsors_section())
    in_app = {(s["name"], _host(s["url"])) for s in _in_app_sponsors()}
    assert {(s["name"], _host(s["url"])) for s in links} == in_app
    for link in links:
        for asset in link["assets"]:
            assert (_ROOT / asset).is_file(), f"SPONSORS.md: {asset} does not exist"


@pytest.mark.parametrize("path", _READMES, ids=lambda p: p.name)
def test_readme_logo_row_matches_the_app_and_resolves(path):
    links = _logo_links(path.read_text(encoding="utf-8"))
    assert {s["name"] for s in links} == {s["name"] for s in _in_app_sponsors()}
    assert {s["name"] for s in links} == set(_README_REPOSITORIES)
    for link in links:
        target = urlsplit(link["url"])
        assert (target.scheme, target.netloc, target.path.rstrip("/")) == (
            "https", "github.com", _README_REPOSITORIES[link["name"]]
        )
        assert parse_qs(target.query) == {
            "utm_source": ["voicestudio"], "utm_medium": ["sponsor"],
            "utm_campaign": ["strategic-sponsor"], "utm_content": ["readme-logo"],
        }
        for asset in link["assets"]:
            assert (path.parent / asset).resolve().is_file(), f"{path.name}: {asset} does not exist"
