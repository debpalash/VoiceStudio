"""Tooling that CI installs at run time must be pinned.

`security.yml` runs bandit through `pipx run --spec 'bandit[sarif]==1.9.4'`. The
spec used to be unpinned, so the analyser was whatever PyPI served that morning:
a new bandit release could change the reported findings between two runs of the
same commit, and a compromised release would execute inside a job that holds
`security-events: write`. Pinning is the cheapest control, and this test keeps
any future `pipx run --spec` honest.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
_PIPX_RUN = re.compile(r"\bpipx\s+run\b")
_SPEC = re.compile(r"--spec(?:=|\s+)(?P<token>\S+)")
# `name[extras]==digits.dots`, and nothing else: no wildcard, no range, no URL.
_REQUIREMENT = re.compile(r"[A-Za-z0-9._-]+(?:\[[A-Za-z0-9,._-]+\])?==\d+(?:\.\d+)*")


def _pipx_specs(workflows: Path = WORKFLOWS) -> list[tuple[str, str]]:
    """Every `pipx run --spec` value, quoted or bare, and `--spec=<value>` too.

    Only the lines that invoke pipx are read, so a later `--spec` belonging to a
    different command in the same step is not mistaken for a tool install, and the
    whole shell token is taken: adjacent quoted chunks are one word to the shell,
    so `'bandit==1.9.4''.*'` is the wildcard spec it looks like, not the pinned
    prefix of one. GitHub reads `.yml` and `.yaml` alike.
    """
    found: list[tuple[str, str]] = []
    for path in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (workflow.get("jobs") or {}).values():
            for step in (job or {}).get("steps") or []:
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                # A backslash-newline is removed by the shell, with no space
                # inserted, so the two halves of a split word rejoin as one.
                joined = re.sub(r"\\\r?\n", "", run)
                for command in joined.splitlines():
                    if not _PIPX_RUN.search(command):
                        continue
                    for match in _SPEC.finditer(command):
                        token = match.group("token")
                        found.append((path.name, token.replace("'", "").replace('"', "")))
    return found


def _is_exact_pin(spec: str) -> bool:
    """True only for an exact version pin, so `==1.9.*` and ranges do not count.

    The whole requirement is matched, not its trailing text: a direct reference
    such as `bandit @ https://example.org/x.tar.gz?build==1.9.4` also ends in a
    version but does not pin one.
    """
    requirement = spec.split(";", 1)[0].strip()  # drop any environment marker
    return bool(_REQUIREMENT.fullmatch(requirement))


def test_every_pipx_run_spec_is_version_pinned():
    specs = _pipx_specs()
    assert specs, "no pipx --spec found: did the bandit step move?"
    unpinned = [f"{name}: {spec}" for name, spec in specs if not _is_exact_pin(spec)]
    assert not unpinned, (
        "A run-time tool install must pin an exact version so CI cannot resolve a "
        "different analyser between runs:\n  " + "\n  ".join(unpinned)
    )


@pytest.mark.parametrize(
    ("spec", "pinned"),
    [
        ("bandit[sarif]==1.9.4", True),
        ("bandit==1.9", True),
        ("bandit[sarif]", False),
        ("bandit[sarif]>=1.9", False),
        ("bandit[sarif]~=1.9", False),
        ("bandit[sarif]==1.9.*", False),
        ("bandit[sarif]==1.9.4,<2", False),
        ("bandit[sarif]==1.9.4 ; python_version >= '3.11'", True),
        # A URL that merely ends in a version does not pin one.
        ("bandit @ https://example.org/latest.tar.gz?build==1.9.4", False),
        ("bandit @ https://example.org/bandit-1.9.4.tar.gz", False),
    ],
)
def test_wildcard_and_range_specs_do_not_count_as_pinned(spec: str, pinned: bool) -> None:
    assert _is_exact_pin(spec) is pinned


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pipx run --spec 'bandit[sarif]==1.9.4' bandit -r backend/", ["bandit[sarif]==1.9.4"]),
        ("pipx run --spec bandit bandit -r backend/", ["bandit"]),
        ("pipx run --spec=black black .", ["black"]),
        ('pipx run --spec "ruff==0.6.9" ruff check', ["ruff==0.6.9"]),
        ("pipx  run  --spec  black  black .", ["black"]),
        # Adjacent quoted chunks are one shell word: a wildcard cannot hide behind them.
        ("pipx run --spec 'bandit==1.9.4''.*' bandit", ["bandit==1.9.4.*"]),
    ],
)
def test_unquoted_and_equals_specs_are_still_checked(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    """A new step must not slip past by writing the spec without quotes."""
    workflow = tmp_path / "synthetic.yml"
    workflow.write_text(
        "name: synthetic\non: push\njobs:\n  probe:\n    runs-on: ubuntu-22.04\n"
        f"    steps:\n      - run: {command}\n",
        encoding="utf-8",
    )

    specs = _pipx_specs(tmp_path)

    assert specs == [("synthetic.yml", expected[0])]


def test_yaml_extension_is_scanned_too(tmp_path: Path) -> None:
    """GitHub accepts `.yaml` as well as `.yml`; the scan has to match."""
    workflow = tmp_path / "synthetic.yaml"
    workflow.write_text(
        "name: synthetic\non: push\njobs:\n  probe:\n    runs-on: ubuntu-22.04\n"
        "    steps:\n      - run: pipx run --spec black black .\n",
        encoding="utf-8",
    )

    assert _pipx_specs(tmp_path) == [("synthetic.yaml", "black")]


def test_a_continued_command_joins_without_a_space(tmp_path: Path) -> None:
    """A backslash-newline is removed, not replaced with a space, by the shell."""
    workflow = tmp_path / "synthetic.yml"
    workflow.write_text(
        "name: synthetic\non: push\njobs:\n  probe:\n    runs-on: ubuntu-22.04\n"
        "    steps:\n      - run: |\n"
        "          pipx run --spec 'bandit==1.9.4'\\\n"
        "          '.*' bandit\n",
        encoding="utf-8",
    )

    assert _pipx_specs(tmp_path) == [("synthetic.yml", "bandit==1.9.4.*")]


def test_only_the_pipx_command_is_read(tmp_path: Path) -> None:
    """A later `--spec` for a different command must not be read as a tool install."""
    workflow = tmp_path / "synthetic.yml"
    workflow.write_text(
        "name: synthetic\non: push\njobs:\n  probe:\n    runs-on: ubuntu-22.04\n"
        "    steps:\n      - run: |\n"
        "          pipx run --spec 'bandit[sarif]==1.9.4' bandit -r backend/\n"
        "          python check.py --spec schema.json\n",
        encoding="utf-8",
    )

    assert _pipx_specs(tmp_path) == [("synthetic.yml", "bandit[sarif]==1.9.4")]