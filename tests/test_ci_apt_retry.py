"""CI apt must go through .github/scripts/apt-retry.sh (transient DNS flakes)."""
from __future__ import annotations

import re
from pathlib import Path

GH = Path(__file__).resolve().parent.parent / ".github"
FILES = sorted([*GH.glob("workflows/**/*.yml"), *GH.glob("actions/**/*.yml")])
# apt-get subcommands that never touch the network
OFFLINE = re.compile(r"apt-get\s+(?:-\S+\s+)*(clean|autoclean|purge|remove|autoremove)\b")


def test_helper_retries_with_backoff():
    s = (GH / "scripts" / "apt-retry.sh").read_text(encoding="utf-8")
    assert "Acquire::Retries=5" in s and "Acquire::http::Timeout=30" in s
    assert "DELAYS=(10 30 60)" in s


def test_no_direct_networked_apt():
    bad = []
    for f in FILES:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\bapt(-get)?\s+(update|install|upgrade)\b", code) or (
                "apt-get" in code and not OFFLINE.search(code)
            ):
                bad.append(f"{f.name}:{n}: {line.strip()}")
    assert not bad, "use .github/scripts/apt-retry.sh:\n" + "\n".join(bad)


def test_playwright_with_deps_not_unwrapped():
    for f in FILES:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "playwright" in line and "--with-deps" in line.split("#", 1)[0]:
                raise AssertionError(f"{f.name}:{n}: split into retried install-deps + install")
            if re.search(r"playwright\s+install(-deps)?\b", line.split("#", 1)[0]):
                assert "apt-retry.sh retry" in line, f"{f.name}:{n}: wrap in apt-retry.sh retry"


def test_helper_falls_back_to_public_dns_when_mirrors_do_not_resolve():
    # Runner DNS once failed for hours on the Ubuntu mirror names while public
    # resolvers answered them; the helper must switch resolvers, not only retry.
    script = (GH / "scripts" / "apt-retry.sh").read_text(encoding="utf-8")
    assert "getent hosts" in script and "azure.archive.ubuntu.com" in script
    assert "DNS=1.1.1.1 8.8.8.8" in script and "systemctl restart systemd-resolved" in script
    install = script.split("apt_install() {", 1)[1].split("}", 1)[0]
    assert install.lstrip().startswith("check_dns"), "check DNS before every install attempt"
