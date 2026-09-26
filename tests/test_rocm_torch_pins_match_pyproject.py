"""The ROCm reinstall must pin the same Torch stack the project resolves to.

An AMD source install does not come from ``uv.lock`` after setup swaps the CUDA
wheels for ROCm ones, so the pins used by ``scripts/setup.py`` must match the
project constraints.

A pin in two files stays correct only while someone remembers both.
``bootstrap.rs`` says "Keep in sync with [tool.uv.constraint-dependencies]",
and a comment cannot enforce itself — CLAUDE.md's convention is that a rule a
reviewer would have to remember belongs in a test. This is that test.

Failure here means an AMD user would get a different Torch stack from every
other platform, with no error at install time: the mismatch only shows up later
as ``operator torchvision::nms does not exist`` or a silent CPU fallback.
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYPROJECT = os.path.join(_ROOT, "pyproject.toml")
_SETUP = os.path.join(_ROOT, "scripts", "setup.py")

#: Packages whose ROCm reinstall must match the project's constraint. The
#: Torch trio specifically: they ship as one matched set, and mixing versions
#: across them is the failure this guards.
_TORCH_STACK = ("torch", "torchaudio", "torchvision")


def _constraint_pins() -> dict:
    """``{name: version}`` from ``[tool.uv.constraint-dependencies]``."""
    with open(_PYPROJECT, encoding="utf-8") as fh:
        src = fh.read()
    block = src.split("constraint-dependencies = [", 1)
    assert len(block) == 2, "constraint-dependencies block not found in pyproject.toml"
    body = block[1].split("]", 1)[0]
    pins = {}
    for name, version in re.findall(r'"([A-Za-z0-9_.-]+)==([^"]+)"', body):
        pins[name.lower()] = version
    return pins


def _rocm_reinstall_args() -> list:
    """The literal package pins in ``ROCM_TORCH_PINS``."""
    with open(_SETUP, encoding="utf-8") as fh:
        src = fh.read()
    marker = "ROCM_TORCH_PINS = ("
    assert marker in src, f"ROCM_TORCH_PINS renamed or removed from {_SETUP}"
    body = src.split(marker, 1)[1].split(")", 1)[0]
    return re.findall(r'"([^"]+)"', body)


def test_the_torch_stack_is_pinned_in_pyproject():
    """Guards the rest of this file from passing vacuously if the pins move."""
    pins = _constraint_pins()
    missing = [p for p in _TORCH_STACK if p not in pins]
    assert not missing, (
        f"{missing} left [tool.uv.constraint-dependencies]. If that is deliberate, "
        f"drop them from _TORCH_STACK here too — but an unpinned Torch package is "
        f"how #972 shipped an AMD install running on the CPU."
    )


def test_rocm_reinstall_pins_match_the_project_constraint():
    args = _rocm_reinstall_args()
    pins = _constraint_pins()
    named = {}
    for arg in args:
        if "==" in arg:
            name, _, version = arg.partition("==")
            named[name.lower()] = version

    problems = []
    for pkg in _TORCH_STACK:
        expected = pins.get(pkg)
        if expected is None:
            continue  # covered by the test above
        actual = named.get(pkg)
        if actual is None:
            unpinned = any(a == pkg for a in args)
            problems.append(
                f"  {pkg}: pyproject pins =={expected}, bootstrap.rs "
                + ("names it with NO version" if unpinned else "does not install it")
            )
        elif actual != expected:
            problems.append(
                f"  {pkg}: pyproject pins =={expected}, bootstrap.rs pins =={actual}"
            )

    assert not problems, (
        "The ROCm reinstall in scripts/setup.py has drifted "
        "from [tool.uv.constraint-dependencies] in pyproject.toml. An AMD user's "
        "install comes from that pip command, not from uv.lock, so they would run "
        "a different Torch stack from every other platform — and it fails later, "
        "at import, not at install:\n" + "\n".join(problems)
    )


def test_the_whole_stack_is_reinstalled_together():
    """Torch, torchaudio and torchvision ship as one matched set.

    Reinstalling a subset leaves the others as the CUDA wheels that the ROCm
    build cannot pair with — exactly the mismatch #1357 reported, arrived at
    from the other direction.
    """
    args = _rocm_reinstall_args()
    named = {a.partition("==")[0].lower() for a in args if "==" in a or a in _TORCH_STACK}
    missing = [p for p in _TORCH_STACK if p not in named]
    assert not missing, (
        f"the ROCm reinstall does not cover {missing}; those stay on the CUDA "
        f"wheels while the rest switch to ROCm"
    )
