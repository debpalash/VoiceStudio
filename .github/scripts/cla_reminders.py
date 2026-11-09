"""Maintain CLA waiting labels and a public, 14-day reminder on open PRs."""
import datetime as dt
import os
import re

from cla_check import COMMENT_MARKER, GitHub

MARKER = "<!-- voicestudio-cla-timer -->"
REMINDER = "<!-- voicestudio-cla-reminder:{start} -->"
WAITING = "awaiting-cla"
DUE = "cla-wait-expired"
DATE = re.compile(r"<!-- cla-start:(\d{4}-\d\d-\d\d) -->")
SIGNERS = re.compile(r"<!-- cla-signers:([A-Za-z0-9,\-]*) -->")


def request(gh, method, path, body=None):
    status, payload, _ = gh.request(method, path, body)
    if status >= 400 and not (method == "DELETE" and status == 404):
        raise RuntimeError(f"{method} {path}: {status} {payload}")
    return payload


def reconcile(gh, pr, today):
    number = pr["number"]
    issue = f"/repos/{gh.repo}/issues/{number}"
    labels = {label["name"] for label in pr.get("labels", [])}
    status = gh.get(f"/repos/{gh.repo}/commits/{pr['head']['sha']}/status")
    cla = next((s for s in status["statuses"] if s["context"] == "CLA"), None)
    if not cla or cla["state"] not in ("success", "failure"):
        return
    comments = gh.paginate(issue + "/comments")
    bot_comments = [c for c in comments if (c.get("user") or {}).get("login") == "github-actions[bot]"]
    check = next((c for c in bot_comments if COMMENT_MARKER in (c.get("body") or "")), None)
    timer = next((c for c in bot_comments if MARKER in (c.get("body") or "")), None)
    if cla["state"] == "success":
        for label in (WAITING, DUE):
            if label in labels:
                request(gh, "DELETE", issue + "/labels/" + label)
        if timer and "<!-- cla-active -->" in timer["body"]:
            request(gh, "PATCH", f"/repos/{gh.repo}/issues/comments/{timer['id']}",
                    {"body": MARKER + "\nCLA waiting period ended; see the CLA check for the current status."})
        return
    if not check or "**Still to sign:**" not in check["body"]:
        return  # Another failure does not establish that contributors signed.
    signers = check["body"].split("**Still to sign:**", 1)[1].split("\n", 1)[0].strip()
    names = set(re.findall(r"@([A-Za-z0-9-]+)", signers))
    if not names:
        raise RuntimeError(f"Cannot identify unsigned contributors on PR #{number}")
    match = DATE.search(timer["body"]) if timer and "<!-- cla-active -->" in timer["body"] else None
    old_names = SIGNERS.search(timer["body"]) if match else None
    if old_names and not names.issubset(set(old_names.group(1).split(","))):
        match = None  # A newly unsigned contributor gets the full waiting period.
    start = dt.date.fromisoformat(match.group(1)) if match else today
    deadline = start + dt.timedelta(days=14)
    remaining = max(0, (deadline - today).days)
    body = (f"{MARKER}\n<!-- cla-active -->\n<!-- cla-start:{start.isoformat()} -->\n"
            f"<!-- cla-signers:{','.join(sorted(names))} -->\n"
            f"**CLA deadline: {deadline.isoformat()} UTC** ({remaining} days remaining). "
            "Please sign using the instructions in the CLA check above. After 14 days, "
            "maintainers may independently fix the underlying issue without using your contribution. "
            "You can still sign after the deadline; this timer does not close your pull request.")
    if timer:
        if timer["body"] != body:
            request(gh, "PATCH", f"/repos/{gh.repo}/issues/comments/{timer['id']}", {"body": body})
    else:
        request(gh, "POST", issue + "/comments", {"body": body})
    reminder = REMINDER.format(start=start.isoformat())
    if today >= start + dt.timedelta(days=7) and not any(reminder in (c.get("body") or "") for c in bot_comments):
        request(gh, "POST", issue + "/comments", {"body": f"{reminder}\n{signers} — a reminder that the CLA deadline is {deadline.isoformat()} UTC. Please see the CLA instructions above."})
    desired = {WAITING} | ({DUE} if today >= deadline else set())
    missing = desired - labels
    if missing:
        request(gh, "POST", issue + "/labels", {"labels": sorted(missing)})
    if DUE in labels and DUE not in desired:
        request(gh, "DELETE", issue + "/labels/" + DUE)


def main():
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    today = dt.datetime.now(dt.timezone.utc).date()
    for pr in gh.paginate(f"/repos/{gh.repo}/pulls?state=open"):
        reconcile(gh, pr, today)


if __name__ == "__main__":
    main()
