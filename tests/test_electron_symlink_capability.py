"""Electron suites that create file symlinks must probe the host capability.

Creating a *file* symlink is the one filesystem operation a stock Windows host
refuses (EPERM, unless Developer Mode or SeCreateSymbolicLinkPrivilege is
granted). Two main-process suites simulate POSIX layouts while creating the link on
the host filesystem, so on such a host they failed with 14 errors that had nothing
to do with the code under test -- and a real regression in those files would have
been lost in that noise.

Both now route those cases through a capability probe that runs them where the
host allows it and skips them with a reason where it does not. This guard keeps a
future edit from putting a bare `test(` back, which is what reintroduces the noise.
"""
from __future__ import annotations

import pathlib
import re

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "electron" / "src" / "main"

#: Suites whose cases create real file symlinks.
_PROBED_SUITES = ("terminal-command.test.ts", "python-runtime-discovery.test.ts")


def _source(name: str) -> str:
    return (_MAIN / name).read_text(encoding="utf-8")


def test_each_suite_declares_a_capability_probe():
    for name in _PROBED_SUITES:
        source = _source(name)
        assert "const symlinkHostTest" in source, (
            f"{name} creates file symlinks but has no capability probe; on a stock "
            "Windows host those cases fail with EPERM instead of skipping"
        )
        assert "symlinkSync(target" in source, (
            f"{name}: the probe must actually attempt a symlink, not assume one works"
        )
        assert re.search(r"return test\.skip;", source), (
            f"{name}: the probe's failure branch must skip, not throw"
        )
        assert "mkdtempSync" in source and "rmSync(dir, { recursive: true, force: true })" in source, (
            f"{name}: the probe must clean up the directory it creates"
        )


def test_no_link_creating_case_is_declared_with_a_bare_test():
    """A `test(` whose body creates a link is the shape that broke on Windows."""
    offenders = []
    for name in _PROBED_SUITES:
        lines = _source(name).splitlines()
        index = 0
        while index < len(lines):
            if re.match(r"^(test|it)\(", lines[index]):
                end = index + 1
                while end < len(lines) and not re.match(
                    r"^(test|it|posixTest|symlinkHostTest)\(", lines[end]
                ):
                    end += 1
                body = "\n".join(lines[index:end])
                if "symlinkSync(" in body or re.search(r"\bsymlink\(", body):
                    offenders.append(f"{name}:{index + 1}: {lines[index].strip()[:70]}")
                index = end
                continue
            index += 1
    assert not offenders, (
        "These cases create a file symlink without the capability probe:\n" + "\n".join(offenders)
    )
