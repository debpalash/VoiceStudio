"""The open-source app ships no licence client (#2762 removed the UI, this PR the plumbing).

Licence activation belongs to a separate Pro distribution. A licence client here
would add an unlisted outbound call to the local-first sanctioned-calls list.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("lemonsqueezy", "pro-license", "pro:activate", "__PRO_STORE_ID__")


def test_electron_and_backend_have_no_licence_client():
    hits = []
    for base in ("electron/src", "electron/electron.vite.config.ts", "backend"):
        root = ROOT / base
        files = [root] if root.is_file() else root.rglob("*")
        for path in files:
            if path.suffix not in {".ts", ".tsx", ".mjs", ".js", ".py"} or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            hits += [f"{path.relative_to(ROOT)}: {needle}" for needle in FORBIDDEN if needle.lower() in text]
    assert not hits, "licence client code belongs in the Pro distribution:\n" + "\n".join(hits)
