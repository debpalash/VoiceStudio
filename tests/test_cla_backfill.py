"""Tests for the manual pass that runs the CLA check on every open pull request."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / ".github" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("cla_backfill", _SCRIPTS / "cla_backfill.py")
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)


class FakeGitHub:
    def paginate(self, path, limit=None):
        assert path.endswith("/pulls?state=open")
        return [{"number": n, "head": {"sha": f"sha{n}"}} for n in (1, 2, 3)]
    repo = "debpalash/VoiceStudio"


def test_checks_every_open_pr_with_its_head():
    seen = []

    def check(gh, event_name, event, server_url):
        seen.append((event_name, event["pull_request"]["number"], event["pull_request"]["head"]["sha"]))

    assert backfill.backfill(FakeGitHub(), check, "https://github.com", pause=0) == []
    assert seen == [("pull_request_target", 1, "sha1"), ("pull_request_target", 2, "sha2"),
                    ("pull_request_target", 3, "sha3")]


def test_one_failure_does_not_stop_the_others():
    seen = []

    def check(gh, event_name, event, server_url):
        seen.append(event["pull_request"]["number"])
        if event["pull_request"]["number"] == 2:
            raise RuntimeError("rate limited")

    assert backfill.backfill(FakeGitHub(), check, "https://github.com", pause=0) == [2]
    assert seen == [1, 2, 3]
