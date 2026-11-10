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


def _bodies(gh):
    return [(body or {}).get("body", "") for _, _, body in gh.calls]


def test_new_timer_gets_a_three_day_deadline_stored_in_the_comment():
    check = comment("<!-- voicestudio-cla -->\n**Still to sign:** @alice\n")
    gh = FakeGitHub("failure", [check])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": []}, dt.date(2026, 11, 10))
    timer = next(b for b in _bodies(gh) if reminders.MARKER in b)
    assert "<!-- cla-deadline:2026-11-13 -->" in timer and "**CLA deadline: 2026-11-13 UTC** (3 days remaining)" in timer
    assert "Signing takes a minute" in timer and "After the deadline" in timer
    assert "still sign after the deadline" in timer and "does not close your pull request" in timer
    assert "14 days" not in timer


def test_posted_legacy_fourteen_day_deadline_is_kept_and_made_explicit():
    check = comment("<!-- voicestudio-cla -->\n**Still to sign:** @alice\n")
    legacy = comment("<!-- voicestudio-cla-timer -->\n<!-- cla-active -->\n<!-- cla-start:2026-11-05 -->\n"
                     "<!-- cla-signers:alice -->\n**CLA deadline: 2026-11-19 UTC**", 2)
    gh = FakeGitHub("failure", [check, legacy])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": []}, dt.date(2026, 11, 10))
    timer = next(b for b in _bodies(gh) if reminders.MARKER in b)
    assert "<!-- cla-deadline:2026-11-19 -->" in timer and "2026-11-19 UTC** (9 days remaining)" in timer
    # The legacy reminder stays at day seven: none yet on day five.
    assert not any("a reminder that" in b for b in _bodies(gh))


def test_posted_deadline_marker_never_moves():
    check = comment("<!-- voicestudio-cla -->\n**Still to sign:** @alice\n")
    posted = comment("<!-- voicestudio-cla-timer -->\n<!-- cla-active -->\n<!-- cla-start:2026-11-01 -->\n"
                     "<!-- cla-deadline:2026-11-15 -->\n<!-- cla-signers:alice -->", 2)
    gh = FakeGitHub("failure", [check, posted])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": []}, dt.date(2026, 11, 10))
    assert any("2026-11-15 UTC** (5 days remaining)" in b for b in _bodies(gh))


def test_newly_unsigned_contributor_restarts_with_three_days_and_reminder_on_day_one():
    check = comment("<!-- voicestudio-cla -->\n**Still to sign:** @alice, @bob\n")
    old = comment("<!-- voicestudio-cla-timer -->\n<!-- cla-active -->\n<!-- cla-start:2026-10-01 -->\n"
                  "<!-- cla-signers:alice -->", 2)
    gh = FakeGitHub("failure", [check, old])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": []}, dt.date(2026, 11, 10))
    assert any("<!-- cla-start:2026-11-10 -->" in b and "<!-- cla-deadline:2026-11-13 -->" in b for b in _bodies(gh))
    assert not any("a reminder that" in b for b in _bodies(gh))
    restarted = comment(next(b for b in _bodies(gh) if reminders.MARKER in b), 2)
    gh = FakeGitHub("failure", [check, restarted])
    reminders.reconcile(gh, {"number": 1, "head": {"sha": "abc"}, "labels": []}, dt.date(2026, 11, 11))
    assert any("a reminder that the CLA deadline is 2026-11-13 UTC" in b for b in _bodies(gh))
    assert not any("cla-wait-expired" in (body or {}).get("labels", []) for _, _, body in gh.calls)


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
