"""The torch trio must stay coupled on the `uv pip install` paths (#1357).

`pyproject.toml` declares `torch>=2.4`, `torchaudio>=2.4`, `torchvision>=0.19`
independently, because no PEP 508 syntax expresses "these three move together".
The coupling lives in `[tool.uv] constraint-dependencies`.

That setting is part of the **project** API — `uv sync`, `uv lock`, `uv run`.
`uv pip install` is the pip-compatible interface and ignores it. Both install
paths that use `uv pip install` (the Colab notebook and the Docker
image) therefore resolved the trio on its bare lower bounds, free to upgrade
torch while leaving a torchvision built against an older ABI in place:

    RuntimeError: operator torchvision::nms does not exist

That is #1357, reported on Colab, where the stale torchvision sat in the
preinstalled `/usr/local/lib/python3.12/dist-packages/`.

Verified directly rather than assumed: with the pin present only in
`constraint-dependencies`, `uv pip install --dry-run .` into a clean
environment resolves torchvision **0.28.0**, not the pinned 0.23.0.

So the pin is passed explicitly via `--constraint`, and these tests fail if the
file drifts from pyproject, if either call site stops passing it, or if the
Docker guard stops covering torchvision.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CONSTRAINTS = _ROOT / "deploy" / "torch-constraints.txt"
_DOCKERFILE = _ROOT / "deploy" / "Dockerfile"
_NOTEBOOK = _ROOT / "notebooks" / "OmniVoice_Studio_Colab.ipynb"

_PACKAGES = ("torch", "torchaudio", "torchvision")


def _pins(text: str) -> dict:
    """`{name: version}` for every `name==version` line, comments stripped."""
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


@pytest.fixture(scope="module")
def project_constraints() -> dict:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return _pins("\n".join(data["tool"]["uv"]["constraint-dependencies"]))


@pytest.fixture(scope="module")
def file_constraints() -> dict:
    return _pins(_CONSTRAINTS.read_text(encoding="utf-8"))


def test_the_constraints_file_exists(file_constraints):
    assert file_constraints, f"{_CONSTRAINTS} has no pins"


@pytest.mark.parametrize("pkg", _PACKAGES)
def test_every_member_of_the_trio_is_pinned(pkg, file_constraints):
    """A trio with one member unpinned is the bug: the unpinned one is exactly
    the one free to drift out of ABI compatibility with the other two."""
    assert pkg in file_constraints, f"{pkg} missing from {_CONSTRAINTS.name}"


def test_the_file_matches_pyproject(project_constraints, file_constraints):
    """Two sources of truth that disagree would ship `uv sync` users one torch
    and Docker/Colab users another, with no error anywhere."""
    for pkg in _PACKAGES:
        assert file_constraints.get(pkg) == project_constraints.get(pkg), (
            f"{pkg} drifted: {_CONSTRAINTS.name} says {file_constraints.get(pkg)}, "
            f"pyproject [tool.uv] constraint-dependencies says "
            f"{project_constraints.get(pkg)}"
        )


def test_the_pins_carry_no_local_version(file_constraints):
    """`==2.8.0+cu128` would match only the CUDA build and force a reinstall on
    the ROCm image and on Colab. Bare `==2.8.0` matches every local segment
    (PEP 440), which is what lets one file serve all three."""
    for pkg in _PACKAGES:
        assert "+" not in file_constraints[pkg], (
            f"{pkg} pins a local version ({file_constraints[pkg]}), which would "
            f"clobber the vendor-built wheel it is supposed to preserve"
        )


# ── the call sites ────────────────────────────────────────────────────────

def test_the_dockerfile_passes_the_constraint():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    install_start = text.index("RUN uv pip install")
    install_end = text.index("\n\n", install_start)
    install = text[install_start:install_end]
    assert "--constraint" in install and "torch-constraints.txt" in install, (
        "Docker installs without the torch constraint, so the trio can "
        f"drift again:\n{install}"
    )


def test_docker_install_and_runtime_use_the_guarded_python():
    """#1274: ROCm's `python3` had HIP torch, while `--system` installed and
    bare `uvicorn` launched through `/usr/bin/python` with CUDA torch."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    install_start = text.index("RUN uv pip install")
    install_end = text.index("\n\n", install_start)
    install = text[install_start:install_end]
    assert '--python "$(command -v python3)"' in install
    assert "--system" not in install
    assert "EXPOSE 3900" in text
    assert 'CMD ["python3", "backend/docker_entrypoint.py", "--health-check"]' in text
    assert 'ENTRYPOINT ["python3", "backend/docker_entrypoint.py"]' in text


def test_docker_docs_do_not_assume_the_run_name_for_compose():
    docs = (_ROOT / "docs" / "install" / "docker.md").read_text(encoding="utf-8")
    assert "docker exec <container> python3" in docs
    assert "torch.cuda.get_device_name(0) if ok else 'unavailable'" in docs
    for compose_name in ("omnivoice-studio", "omnivoice-studio-gpu", "omnivoice-studio-rocm"):
        assert compose_name in docs


def test_the_dockerfile_copies_the_constraints_file():
    """A --constraint pointing at a path the build context never copied fails
    the build, but only once someone rebuilds — pin it here instead."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^COPY .*torch-constraints\.txt", text, re.MULTILINE), (
        "Dockerfile references the constraints file but never COPYs it"
    )


def test_the_docker_guard_covers_torchvision():
    """The pre-existing guard asserted on torch and torchaudio only — omitting
    the one package that actually broke in #1357."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    guard = [ln for ln in text.splitlines() if "torch.version.hip" in ln or "torchvision.ops" in ln]
    assert guard, "the GPU-flavour guard is gone"
    assert "torchvision.ops" in text, (
        "the guard does not exercise torchvision's C++ ops, so an ABI mismatch "
        "would still ship and surface at runtime as torchvision::nms"
    )


def test_the_colab_notebook_passes_the_constraint():
    nb = json.loads(_NOTEBOOK.read_text(encoding="utf-8"))
    installs = [
        "".join(c["source"]) for c in nb["cells"]
        if c["cell_type"] == "code" and '"uv", "pip", "install"' in "".join(c["source"])
    ]
    assert installs, "no uv pip install cell found in the Colab notebook"
    for src in installs:
        args = _install_argv(src)
        assert args, "could not parse the install command's argument list"
        # The ARGUMENT LIST, not the cell text: a substring check passes when
        # --constraint is deleted from run([...]) but its explanatory comment
        # survives, which is the shape this whole PR is about (CodeRabbit).
        assert "--constraint" in args, (
            "the Colab install cell resolves the torch trio unconstrained — the "
            "exact path #1357 was reported on"
        )
        i = args.index("--constraint")
        assert args[i + 1:i + 2] == ["deploy/torch-constraints.txt"], (
            f"--constraint is not followed by the constraints file: {args}"
        )


def _install_argv(cell_source: str) -> list:
    """String literals of the `run([...])` argument list in a notebook cell.

    Parsed rather than pattern-matched so a comment mentioning --constraint
    cannot satisfy the assertion above.
    """
    import ast

    tree = ast.parse(cell_source)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and node.args
                and isinstance(node.args[0], ast.List)):
            argv = [e.value for e in node.args[0].elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "install" in argv and "pip" in argv:
                return argv
    return []


# --- [tool.uv] override-dependencies on the same `uv pip install` paths (#3275) ---
# The override that lifts gradio's `pillow<13` cap lives in pyproject, which
# `uv pip install` applies only from the project directory with a recent uv
# (run elsewhere, it installs Pillow 12.3.0), so both paths pass it explicitly.
_OVERRIDES = _ROOT / "deploy" / "uv-overrides.txt"


def _requirement_lines(path):
    return sorted(
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_the_overrides_file_matches_pyproject():
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = sorted(pyproject["tool"]["uv"].get("override-dependencies", []))
    assert declared, "pyproject declares no override-dependencies; drop deploy/uv-overrides.txt too"
    assert _requirement_lines(_OVERRIDES) == declared


def test_the_dockerfile_passes_copies_and_checks_the_overrides():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    install_start = text.index("RUN uv pip install")
    install = text[install_start:text.index("\n\n", install_start)]
    assert "--override deploy/uv-overrides.txt" in install, install
    assert re.search(r"^COPY .*uv-overrides\.txt", text, re.MULTILINE)
    guard = text[text.index("# Guard: every security override"):]
    assert "open('deploy/uv-overrides.txt')" in guard and "specifier.contains" in guard


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
@pytest.mark.parametrize("installed", ["13.0.0", "12.3.0"])
def test_docker_override_guard_parses_lines_and_enforces_versions(
    tmp_path, monkeypatch, capsys, line_ending, installed,
):
    """Execute the shipped guard: a cold image must parse its override file,
    including line endings, while a below-floor install must still fail."""
    import importlib.metadata
    import shlex

    text = _DOCKERFILE.read_text(encoding="utf-8")
    start = text.index('RUN python3 -c "from importlib.metadata import version;')
    command = text[start:text.index("\n\n", start)].replace("\\\n", " ")
    args = shlex.split(command)
    assert args[:3] == ["RUN", "python3", "-c"]
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/uv-overrides.txt").write_bytes(
        line_ending.join(["# Security overrides", "", "  pillow>=13.0.0  ", ""]).encode()
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: installed)
    if installed == "12.3.0":
        with pytest.raises(AssertionError, match="pillow 12.3.0 does not satisfy pillow>=13.0.0"):
            exec(args[3], {})
    else:
        exec(args[3], {})
        assert "uv overrides applied: pillow 13.0.0" in capsys.readouterr().out


def test_the_colab_notebook_passes_the_overrides():
    nb = json.loads(_NOTEBOOK.read_text(encoding="utf-8"))
    installs = [
        "".join(c["source"]) for c in nb["cells"]
        if c["cell_type"] == "code" and '"uv", "pip", "install"' in "".join(c["source"])
    ]
    for src in installs:
        args = _install_argv(src)
        i = args.index("--override")
        assert args[i + 1:i + 2] == ["deploy/uv-overrides.txt"], args


def test_the_colab_notebook_updates_a_reused_clone():
    # A clone from an earlier session lacked deploy/uv-overrides.txt, so the
    # install cell failed on the file it now passes (#3281 review).
    nb = json.loads(_NOTEBOOK.read_text(encoding="utf-8"))
    src = next("".join(c["source"]) for c in nb["cells"] if "# 2a. Clone the repo" in "".join(c["source"]))
    reuse = src[src.index("# 2a."):src.index("else:", src.index("# 2a."))]
    assert '"pull", "--ff-only"' in reuse
    assert 'shutil.rmtree(os.path.join(REPO_DIR, "frontend", "dist")' in reuse


# --- the remaining `[tool.uv] constraint-dependencies` (security floors) ---
# `uv pip install` ignores them too, so a base image's older compatible
# package would survive below the floor that `uv sync` enforces (#3857).
_SECURITY = _ROOT / "deploy" / "security-constraints.txt"


def test_the_security_constraints_match_pyproject():
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = sorted(
        c for c in pyproject["tool"]["uv"]["constraint-dependencies"]
        if re.split(r"[<>=!~;\s\[]", c, maxsplit=1)[0].lower() not in _PACKAGES
    )
    assert declared, "pyproject declares no security floors; drop deploy/security-constraints.txt too"
    assert _requirement_lines(_SECURITY) == declared


def _security_floors_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("security_floors", _ROOT / "deploy" / "security_floors.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dockerfile_raises_and_checks_the_security_floors():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    install_start = text.index("RUN uv pip install")
    install = text[install_start:text.index("\n\n", install_start)]
    assert "--constraint deploy/security-constraints.txt" in install, install
    assert re.search(r"^COPY .*security-constraints\.txt.*security_floors\.py", text, re.MULTILINE)
    floors = text[text.index('RUN floors="$(python3 deploy/security_floors.py requirements)"'):]
    floors = floors[:floors.index("\n\n")]
    # Upgrades what the main install left below its floor, then fails the build on any.
    assert "uv pip install" in floors and "$floors" in floors
    assert floors.rstrip().endswith("python3 deploy/security_floors.py check")
    assert floors.index("security_floors.py requirements") < floors.index("security_floors.py check")


@pytest.mark.parametrize("installed, below", [
    ({"pyasn1": "0.6.5"}, []),
    ({"pyasn1": "0.6.4"}, ["pyasn1>=0.6.5"]),
    ({"msgpack": "1.1.1", "pyasn1": "0.6.5"}, ["msgpack>=1.2.3"]),
    ({}, []),
])
def test_security_floors_report_only_installed_packages_below_floor(tmp_path, installed, below):
    """A base image's below-floor package is reported for upgrade; a package
    the image does not install (an optional extra) is skipped."""
    mod = _security_floors_module()
    path = tmp_path / "floors.txt"
    path.write_text("# floors\r\n\r\n  pyasn1>=0.6.5  \nmsgpack>=1.2.3\nhydra-core>=1.3.8\n", encoding="utf-8")
    low = mod.below_floor(mod.floors(path), installed)
    assert [f"{r.name}{r.specifier}" for r, _ in low] == below


def test_security_floors_skip_floors_whose_marker_does_not_apply(tmp_path):
    """A platform-specific floor must not be enforced on other platforms."""
    mod = _security_floors_module()
    path = tmp_path / "floors.txt"
    path.write_text("foo>=2; sys_platform == 'no-such-platform'\nbar>=2; python_version >= '3'\n", encoding="utf-8")
    assert [r.name for r in mod.floors(path)] == ["bar"]
    assert [r.name for r, _ in mod.below_floor(mod.floors(path), {"foo": "1", "bar": "1"})] == ["bar"]


def test_security_floors_cli_check_and_requirements(monkeypatch, capsys):
    from types import SimpleNamespace

    mod = _security_floors_module()
    monkeypatch.setattr(mod.md, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "MsgPack"}, version="1.1.1"),
        SimpleNamespace(metadata={"Name": None}, version="0"),
    ])
    assert mod.main(["x", "requirements"]) == 0
    assert capsys.readouterr().out.split() == ["msgpack>=1.2.3"]
    assert mod.main(["x", "check"]) == 1
    assert "msgpack 1.1.1 does not satisfy msgpack>=1.2.3" in capsys.readouterr().err
    monkeypatch.setattr(mod.md, "distributions", lambda: [SimpleNamespace(metadata={"Name": "msgpack"}, version="1.2.3")])
    assert mod.main(["x", "requirements"]) == 0 and capsys.readouterr().out == ""
    assert mod.main(["x", "check"]) == 0
    assert "security floors hold: msgpack 1.2.3" in capsys.readouterr().out


def test_the_colab_notebook_passes_the_security_constraints():
    nb = json.loads(_NOTEBOOK.read_text(encoding="utf-8"))
    installs = [
        "".join(c["source"]) for c in nb["cells"]
        if c["cell_type"] == "code" and '"uv", "pip", "install"' in "".join(c["source"])
    ]
    assert installs
    for src in installs:
        args = _install_argv(src)
        pairs = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--constraint"]
        assert "deploy/security-constraints.txt" in pairs, args
        # Packages Colab already ships are raised to their floors, then checked.
        tree_src = src[src.index('"deploy/security_floors.py", "requirements"'):]
        assert "security floor upgrades" in tree_src
        assert '"deploy/security_floors.py", "check"' in tree_src
