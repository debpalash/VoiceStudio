"""Managed sidecar generations for non-destructive ROCm upgrades."""
import os
import shutil
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

_GENERATIONS = ".voicestudio-generations"
_MARKER = ".voicestudio-managed-generation"


def new_generation(spec, root: Path):
    """Create a fresh, separate generation; never move a Python environment."""
    container = root / _GENERATIONS
    container.mkdir(parents=True, exist_ok=True)
    if container.is_symlink() or container.resolve().parent != root.resolve():
        raise ValueError("Managed generation directory must remain inside its engine root")
    generation = container / uuid4().hex
    generation.mkdir(exist_ok=False)
    (generation / _MARKER).write_text(spec.engine_id, encoding="utf-8")
    relative = Path(_GENERATIONS) / generation.name / spec.checkout_dirname
    return replace(spec, checkout_dirname=str(relative))


def selected_generation(spec, root: Path, selected: str | None):
    """Accept only a marked, non-symlink generation inside this engine root."""
    if not selected or len(Path(spec.checkout_dirname).parts) != 1:
        return None
    candidate = Path(selected)
    try:
        relative = candidate.relative_to(root)
        if (len(relative.parts) != 3 or relative.parts[0] != _GENERATIONS
                or relative.parts[2] != spec.checkout_dirname):
            return None
        if len(relative.parts[1]) != 32 or any(c not in "0123456789abcdef" for c in relative.parts[1]):
            return None
        container = root / _GENERATIONS
        generation = container / relative.parts[1]
        if container.is_symlink() or container.resolve().parent != root.resolve():
            return None
        if generation.is_symlink() or candidate.is_symlink():
            return None
        if candidate.resolve().parent != generation.resolve():
            return None
        if (generation / _MARKER).read_text(encoding="utf-8") != spec.engine_id:
            return None
    except (OSError, ValueError):
        return None
    return candidate


def cleanup_failed_generation(spec, root: Path):
    """Remove only the generation created for this failed install."""
    parts = Path(spec.checkout_dirname).parts
    if len(parts) != 3 or parts[0] != _GENERATIONS:
        return
    checkout = root.joinpath(*parts)
    if selected_generation(replace(spec, checkout_dirname=parts[2]), root, str(checkout)) != checkout:
        return
    shutil.rmtree(checkout.parent)


def copy_preserved_checkout(source: Path, destination: Path, root: Path):
    """Copy managed source and user files without touching the live venv."""
    if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("The existing checkout must be inside its managed root")
    source_root = source.resolve()
    for directory, names, files in os.walk(source, followlinks=False):
        if Path(directory) == source and ".venv" in names:
            names.remove(".venv")
        for name in names + files:
            path = Path(directory) / name
            if path.is_symlink():
                target = Path(os.readlink(path))
                if target.is_absolute() or not path.resolve().is_relative_to(source_root):
                    raise ValueError("External or absolute checkout links require manual repair")
    def ignore(directory, names):
        return {name for name in names if Path(directory) == source and
                name in {".venv", ".voicestudio_install_complete"}}
    shutil.copytree(source, destination, symlinks=True, ignore=ignore)
