import datetime as dt
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/scripts"))
spec = importlib.util.spec_from_file_location("cla_reminders", Path(__file__).resolve().parents[1] / ".github/scripts/cla_reminders.py")
reminders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reminders)


class FakeGitHub:
    repo = "owner/repo"

    def __init__(self, state, comments):
        self.state, self.comments, self.calls = state, comments, []

    def get(self, path):
        return {"statuses": [{"context": "CLA", "state": self.state}]}

    def paginate(self, path):
        return self.comments

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return 200, {}, {}


def comment(body, id=1):
    return {"id": id, "body": body, "user": {"login": "github-actions[bot]"}}


def test_deadline_and_reminder():
    check = comment("<!-- voicestudio-cla -->\n**Still to sign:** @alice\n")
    timer = comment("<!-- voicestudio-cla-timer -->\n<!-- cla-active -->\n<!-- cla-start:2026-10-01 -->", 2)
    gh = FakeGitHub("failure", [check, timer])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": []}, dt.date(2026, 10, 15))
    assert any("2026-10-15 UTC" in (body or {}).get("body", "") for _, _, body in gh.calls)
    assert any("@alice" in (body or {}).get("body", "") for _, _, body in gh.calls)
    assert any("cla-wait-expired" in (body or {}).get("labels", []) for _, _, body in gh.calls)


def test_signed_removes_labels_and_stops_timer():
    timer = comment("<!-- voicestudio-cla-timer -->\n<!-- cla-active -->\n<!-- cla-start:2026-10-01 -->", 2)
    gh = FakeGitHub("success", [timer])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": [{"name": "awaiting-cla"}, {"name": "cla-wait-expired"}]}, dt.date(2026, 10, 15))
    assert len([call for call in gh.calls if call[0] == "DELETE"]) == 2
    assert any("waiting period ended" in (body or {}).get("body", "") for _, _, body in gh.calls)


def test_pending_status_does_not_change_timer():
    gh = FakeGitHub("pending", [])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": []}, dt.date(2026, 10, 15))
    assert not gh.calls


def test_other_cla_failure_preserves_existing_deadline():
    check = comment("<!-- voicestudio-cla -->\nClose duplicate pull requests sharing this head.")
    timer = comment("<!-- voicestudio-cla-timer -->\n<!-- cla-active -->\n<!-- cla-start:2026-10-01 -->", 2)
    gh = FakeGitHub("failure", [check, timer])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": [{"name": "awaiting-cla"}]}, dt.date(2026, 10, 15))
    assert not gh.calls
