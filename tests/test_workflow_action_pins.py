"""Each action reference is a full-SHA pin, and each action has exactly one pin.

The workflows are copied from each other, so a bump applied to one file left the
rest on the old pin: `actions/checkout` ran at v7.0.1 in 61 places and at v4.4.0
or v5.0.0 in four, `actions/setup-python` at v7.1.0 everywhere except the recipe
job (v5.6.0), and `astral-sh/setup-uv` at v6.8.0 in ten places and v3.2.4 in
seven. A pin that only some jobs get is a pin nobody can reason about.

References are found by parsing the YAML of every workflow and composite action and
collecting each value under a `uses` key (aliases resolve), not by scanning text, so
quoting, flow style, anchors and `uses : x` spacing cannot hide one and a `run:`
script that mentions `uses:` is never mistaken for one.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"

# A comment that names what the SHA is: a version, a release channel or a branch.
LABEL = re.compile(r"^(?:v?\d[\w.-]*|stable|nightly|latest|beta)$", re.IGNORECASE)
SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow_files() -> list[Path]:
    """Every workflow and composite-action file, `.yml` and `.yaml` alike: GitHub reads both."""
    return sorted(
        path
        for base in (WORKFLOWS, ACTIONS)
        if base.is_dir()
        for path in base.rglob("*")
        if path.suffix in {".yml", ".yaml"} and path.is_file()
    )


def _uses_nodes(node: yaml.Node, found: list[yaml.ScalarNode], path: frozenset[int]) -> None:
    """Collect every scalar value whose mapping key is `uses`, at any depth.

    The YAML composer resolves aliases to the anchored node, so `uses: *co` is seen
    as the reference it stands for. Only text inside a real `uses` key counts: a
    `run:` script or a comment that mentions `uses:` is never a mapping key.
    """
    if id(node) in path:
        return
    path = path | {id(node)}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "uses" and isinstance(value, yaml.ScalarNode):
                found.append(value)
            _uses_nodes(key, found, path)
            _uses_nodes(value, found, path)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _uses_nodes(item, found, path)


def _references(text: str, file: str) -> list[dict[str, str | None]]:
    """Remote action references in one YAML document, found by parsing it."""
    lines = text.splitlines()
    found: list[dict[str, str | None]] = []
    for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
        if document is None:
            continue
        nodes: list[yaml.ScalarNode] = []
        _uses_nodes(document, nodes, frozenset())
        for node in nodes:
            value = node.value.strip()
            if value.startswith(("./", "docker://")):
                continue
            action, _, ref = value.partition("@")
            # The version label is a comment, which the parser drops: read it from the
            # line the value ends on.
            tail = lines[node.end_mark.line][node.end_mark.column :]
            comment = re.match(r"\s*#\s*(.*)", tail)
            found.append(
                {
                    "file": file,
                    "action": action,
                    "ref": ref,
                    "label": (comment.group(1).strip() if comment else "") or None,
                }
            )
    return found


def _uses() -> list[dict[str, str | None]]:
    found: list[dict[str, str | None]] = []
    for path in _workflow_files():
        found.extend(_references(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT / ".github"))))
    return found


class _StrictLoader(yaml.SafeLoader):
    """A SafeLoader that refuses duplicate mapping keys instead of replacing them."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    """Reject a repeated key, comparing the keys as written.

    The comparison uses the scalar's source text, not the constructed object: YAML
    1.1 coerces `ON`, `on` and `YES` to the same value, so comparing constructed
    keys would report `{ON: "1", YES: "1"}` as a duplicate when it is not.
    """
    seen: set[str] = set()
    for key_node, _value in node.value:
        key = key_node.value if isinstance(key_node, yaml.ScalarNode) else repr(key_node.value)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def test_the_scan_finds_the_references_it_checks() -> None:
    """A pattern that silently matches nothing would pass every check below."""
    found = _uses()
    assert len(found) > 100, f"only {len(found)} action references were discovered"
    assert any(len(str(use["action"]).split("/")) == 3 for use in found), (
        "subpath actions such as gradle/actions/setup-gradle were not discovered"
    )


def test_every_reference_is_pinned_to_a_full_commit_sha() -> None:
    tags = [
        f"{use['file']}: {use['action']}@{use['ref']}"
        for use in _uses()
        if not SHA.match(str(use["ref"]))
    ]
    assert not tags, "these action references are not pinned to a full SHA:\n  " + "\n  ".join(tags)


def test_each_pin_names_the_version_it_holds() -> None:
    undocumented = [
        f"{use['file']}: {use['action']} has no usable version comment ({use['label']!r})"
        for use in _uses()
        if not use["label"] or not LABEL.match(str(use["label"]))
    ]
    assert not undocumented, "a SHA nobody can map back to a version is unmaintainable:\n  " + "\n  ".join(
        undocumented
    )


def _pins(uses: list[dict[str, str | None]]) -> dict[str, set[str]]:
    """action (lowercased) -> the SHAs it is pinned to.

    Repository names are case-insensitive on GitHub, so `actions/checkout` and
    `Actions/Checkout` are the same action and must not read as two pins.
    """
    pins: dict[str, set[str]] = {}
    for use in uses:
        if not SHA.match(str(use["ref"])):
            continue
        pins.setdefault(str(use["action"]).lower(), set()).add(str(use["ref"]))
    return pins


def test_mixed_case_references_are_one_action() -> None:
    uses = [
        {"file": "a.yml", "action": "actions/checkout", "ref": "a" * 40, "label": "v7.0.1"},
        {"file": "b.yml", "action": "Actions/Checkout", "ref": "b" * 40, "label": "v4.4.0"},
    ]
    pins = _pins(uses)
    assert list(pins) == ["actions/checkout"]
    assert len(pins["actions/checkout"]) == 2


def test_each_action_has_one_pin_across_all_workflows() -> None:
    drifted = {action: sorted(refs) for action, refs in _pins(_uses()).items() if len(refs) > 1}
    assert not drifted, (
        "these actions are pinned to more than one commit SHA, so a bump reached only "
        f"some workflows: {drifted}"
    )


def test_commented_out_references_are_ignored() -> None:
    """A disabled step is not a pin, and reporting it would send readers to dead text."""
    text = (
        "      # - uses: actions/checkout@v4\n"
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
        "      #   uses: actions/cache@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0\n"
    )
    found = _references(text, "probe.yml")
    assert [use["ref"] for use in found] == ["3d3c42e5aac5ba805825da76410c181273ba90b1"]


def test_quoted_uses_keys_are_discovered() -> None:
    """`"uses":` and `'uses':` are the same key to YAML and must not escape the pin check."""
    text = (
        '      - {"uses": "actions/checkout@v4"}\n'
        "      - {'uses': 'actions/cache@v3'}\n"
        "      - uses: actions/setup-python@v5\n"
    )
    assert [use["action"] for use in _references(text, "probe.yml")] == [
        "actions/checkout",
        "actions/cache",
        "actions/setup-python",
    ]


def _parse(text: str):
    return yaml.load(text, Loader=_StrictLoader)


def test_distinct_yaml_1_1_keys_are_not_duplicates() -> None:
    """`ON`, `on` and `YES` coerce to one value; as written they are different keys."""
    _parse('env: {ON: "1", YES: "1", on: "2"}\n')


def test_a_repeated_key_is_rejected() -> None:
    with pytest.raises(yaml.YAMLError):
        _parse("jobs:\n  probe:\n    with: 1\n    with: 2\n")


def test_every_workflow_parses_without_duplicate_keys() -> None:
    broken = []
    for path in _workflow_files():
        try:
            yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
        except yaml.YAMLError as error:
            broken.append(f"{path.name}: {error}")
    assert not broken, "workflows must parse and must not repeat a mapping key:\n  " + "\n  ".join(
        broken[:10]
    )

def test_space_before_the_colon_is_still_discovered() -> None:
    """`uses : x@v4` is the same key to YAML and must not escape the pin check."""
    found = _references("      - uses : actions/checkout@v4\n", "probe.yml")
    assert [use["ref"] for use in found] == ["v4"]
    assert not SHA.match(str(found[0]["ref"]))


def test_anchored_reference_is_resolved() -> None:
    text = "a:\n  - uses: &co actions/checkout@v4\n  - uses: *co\n"
    assert [(u["action"], u["ref"]) for u in _references(text, "probe.yml")] == [
        ("actions/checkout", "v4"),
        ("actions/checkout", "v4"),
    ]


def test_uses_text_in_scripts_and_comments_is_not_a_reference() -> None:
    text = (
        "steps:\n"
        "  - run: echo ok # uses: actions/checkout@v4\n"
        "  - run: |\n"
        "      uses: actions/cache@v3\n"
    )
    assert _references(text, "probe.yml") == []


def test_local_and_docker_references_are_not_remote_pins() -> None:
    text = "steps:\n  - uses: ./.github/actions/x\n  - uses: docker://alpine:3\n"
    assert _references(text, "probe.yml") == []


def test_version_label_is_read_from_the_value_line() -> None:
    text = f"steps:\n  - uses: actions/checkout@{'a' * 40} # v7.0.1\n"
    assert _references(text, "probe.yml")[0]["label"] == "v7.0.1"


def test_pins_resolve_to_the_version_they_name() -> None:
    """Opt-in: the checks above are shape-only, so a swapped SHA passes all of them.

    `test_each_pin_names_the_version_it_holds` matches the comment against a regex
    and never resolves it, so a well-formed pin whose label says `v7.0.1` but whose
    SHA belongs to another commit is accepted. Resolving the 30 distinct
    action/label pairs needs the GitHub API, which PR CI must not depend on, so
    this runs on demand:

        VOICESTUDIO_VERIFY_ACTION_PINS=1 GITHUB_TOKEN=... \
            pytest tests/test_workflow_action_pins.py

    `.github/workflows/action-pin-verify.yml` runs the same script weekly with a
    token, which is what keeps the check from being a file nobody executes.
    """
    if os.environ.get("VOICESTUDIO_VERIFY_ACTION_PINS") != "1":
        pytest.skip("set VOICESTUDIO_VERIFY_ACTION_PINS=1 to resolve pins over the network")
    script = ROOT / "scripts" / "verify_action_pins.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "these pins do not match the version comments they carry:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
