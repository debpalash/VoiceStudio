"""Run the CLA check on every open pull request, from .github/workflows/cla-backfill.yml."""
import os
import sys
import time

from cla_check import GitHub, run

# GitHub allows about 80 content-creating requests a minute, and one check can make four.
PAUSE_SECONDS = 4


def backfill(gh, check, server_url, pause=PAUSE_SECONDS):
    """Check each open pull request. Returns the numbers that failed."""
    failed = []
    for pr in gh.paginate(f"/repos/{gh.repo}/pulls?state=open"):
        number = pr["number"]
        try:
            check(gh, "pull_request_target", {"pull_request": {"number": number, "head": pr["head"]}}, server_url)
            print(f"CLA backfill: checked #{number}")
        except (RuntimeError, OSError) as err:
            print(f"CLA backfill: #{number} failed: {err}")
            failed.append(number)
        time.sleep(pause)
    return failed


def main():
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    failed = backfill(gh, run, os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    if failed:
        print("CLA backfill: comment recheck on " + ", ".join(f"#{n}" for n in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
