"""Publish a probe HTML report to a GitHub PR as a CI-trace comment.

Standardizes what we've been doing by hand: every probe run that validates a
PR gets (1) a redacted, self-contained HTML report uploaded as a secret Gist
and (2) a markdown digest comment on the PR linking to it — so the PR carries
a permanent trace of what was actually verified.

Privacy: the report is generated from local runs and may embed machine
details. ``redact()`` scrubs, in order:
  - credentials by shape (HF/GitHub/OpenAI/AWS/Discord tokens, bearer values,
    anything that looks like ``key=...``/``secret: ...``)
  - home directories (``/Users/<u>``, ``/home/<u>``, ``C:\\Users\\<u>``) → ``~``
  - usernames inside tmp paths (``pytest-of-<u>``) and email addresses
  - non-loopback IPv4 addresses
The redacted copy is written next to the original as ``*.redacted.html`` and
must be reviewed in a browser before upload (the CLI refuses ``--post``
without ``--yes``, which callers should only pass after human review).

Usage:
  python tests/probe/publish_pr_report.py <report.html>            # redact + digest only
  python tests/probe/publish_pr_report.py <report.html> --pr 324 --post --yes
  python tests/probe/publish_pr_report.py --prune 5                # keep newest 5 reports

Requires: gh CLI authenticated. Stdlib only otherwise.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import subprocess
import sys
from pathlib import Path

REPORTS_DIR = Path(__file__).parent / "reports"

# (pattern, replacement) — order matters: credentials first, then paths.
_REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"hf_[A-Za-z0-9]{16,}"), "hf_<redacted>"),
    (re.compile(r"gh[opsur]_[A-Za-z0-9]{16,}"), "gh*_<redacted>"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "sk-<redacted>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA<redacted>"),
    (re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{25,}"),
     "<redacted-token>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{12,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)"
                r"(\s*[=:]\s*)[\"']?[A-Za-z0-9._\-]{8,}[\"']?"),
     r"\1\2<redacted>"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+"), "~"),
    (re.compile(r"/home/[A-Za-z0-9._-]+"), "~"),
    (re.compile(r"(?i)C:\\+Users\\+[A-Za-z0-9._-]+"), "~"),
    (re.compile(r"pytest-of-[A-Za-z0-9._-]+"), "pytest-of-<user>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    # IPv4 except loopback/0.0.0.0 (functional in a local-first app's logs).
    (re.compile(r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)"
                r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<ip>"),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for pat, repl in _REDACTIONS:
        text, n = pat.subn(repl, text)
        if n:
            counts[pat.pattern[:40]] = n
    return text, counts


def _strip_tags(html_text: str) -> list[str]:
    text = re.sub(r"<style>.*?</style>|<script>.*?</script>", " ",
                  html_text, flags=re.S)
    text = html_mod.unescape(re.sub(r"<[^>]+>", "\n", text))
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def digest_markdown(html_text: str, *, gist_url: str | None, source: str) -> str:
    """Build the PR-comment digest from the report's own text content."""
    lines = _strip_tags(html_text)
    # Header layout (see report.py): verdict, then counts interleaved with labels.
    verdict = next((l for l in lines if l in ("PASS", "FAIL")), "?")
    nums: dict[str, str] = {}
    for i, l in enumerate(lines):
        if l in ("checks", "passed", "failed", "skipped", "advisory") and i:
            nums.setdefault(l, lines[i - 1])
    generated = next((l for l in lines if l.startswith("Generated")), "")
    icon = "✅" if verdict == "PASS" else "❌"
    md = [
        f"## {icon} probe report — {verdict}",
        "",
        f"`{nums.get('checks', '?')}` checks · "
        f"**{nums.get('passed', '?')} passed** · "
        f"{nums.get('failed', '?')} failed · "
        f"{nums.get('skipped', '?')} skipped · "
        f"{nums.get('advisory', '?')} advisory",
        "",
        f"_{generated} · {source} · report redacted before upload_",
    ]
    if gist_url:
        md += ["", f"📄 Full report: {gist_url}"]
    return "\n".join(md)


def prune(keep: int) -> list[str]:
    reports = sorted(REPORTS_DIR.glob("report-2*.html"),
                     key=lambda p: p.name, reverse=True)
    removed = []
    for p in reports[keep:]:
        p.unlink()
        removed.append(p.name)
    for p in REPORTS_DIR.glob("*.redacted.html"):
        if not (REPORTS_DIR / p.name.replace(".redacted", "")).exists():
            p.unlink()
            removed.append(p.name)
    return removed


def post(redacted_path: Path, md: str, pr: int, repo: str) -> str:
    gist = subprocess.run(
        ["gh", "gist", "create", str(redacted_path),
         "--desc", f"probe report for {repo}#{pr} (redacted)"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[-1]
    md = md.replace("📄 Full report: None", "").rstrip()
    if "Full report:" not in md:
        md += f"\n\n📄 Full report: {gist}"
    subprocess.run(
        ["gh", "pr", "comment", str(pr), "--repo", repo, "--body", md],
        capture_output=True, text=True, check=True,
    )
    return gist


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", nargs="?", help="path to report-*.html")
    ap.add_argument("--pr", type=int, help="PR number for the comment")
    ap.add_argument("--repo", default="debpalash/VoiceStudio")
    ap.add_argument("--post", action="store_true",
                    help="upload gist + comment (requires --yes)")
    ap.add_argument("--yes", action="store_true",
                    help="confirm you reviewed the redacted report")
    ap.add_argument("--prune", type=int, metavar="N",
                    help="delete all but the newest N reports, then exit")
    args = ap.parse_args()

    if args.prune is not None:
        for name in prune(args.prune):
            print(f"pruned {name}")
        return 0

    if not args.report:
        ap.error("report path required (or --prune N)")
    src = Path(args.report)
    text = src.read_text(encoding="utf-8")
    redacted, counts = redact(text)
    out = src.with_suffix("").with_suffix("")  # strip .html
    out = src.parent / (src.stem + ".redacted.html")
    out.write_text(redacted)
    print(f"redacted → {out}")
    for pat, n in counts.items():
        print(f"  {n:>3}× {pat}")
    if not counts:
        print("  (nothing matched the redaction patterns)")

    md = digest_markdown(redacted, gist_url=None,
                         source=f"local probe run · {src.name}")
    print("\n--- PR comment digest ---\n" + md + "\n-------------------------")

    if args.post:
        if not (args.pr and args.yes):
            print("refusing to post: --post requires --pr and --yes "
                  "(review the redacted HTML first)", file=sys.stderr)
            return 2
        gist = post(out, md, args.pr, args.repo)
        print(f"posted to {args.repo}#{args.pr} · gist: {gist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
