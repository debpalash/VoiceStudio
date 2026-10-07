"""Restore backend module identity after ``sys.modules`` surgery.

Some suites purge ``core`` / ``api`` / ``services`` / ``main`` and re-import
them under a temporary ``OMNIVOICE_DATA_DIR``. Purging again afterwards is not
enough: test modules collected earlier still hold the ORIGINAL module objects,
while later in-test imports would resolve fresh twins. A test then patches one
copy (``core.db.DB_PATH``) while the code under test reads the other
(``services.dub_pipeline.db_conn``), so writes and reads hit different
databases (#2585 CI).

Restoring the snapshot puts every pre-existing module back, rebinds package
attributes to it, and drops modules first imported under the temporary
environment so they are re-imported cleanly when next needed.
"""
import sys

_BACKEND_PACKAGES = ("main", "core", "api", "services")


def is_backend_module(name: str) -> bool:
    return name in _BACKEND_PACKAGES or name.startswith(tuple(f"{pkg}." for pkg in _BACKEND_PACKAGES[1:]))


def snapshot() -> dict:
    return {name: mod for name, mod in sys.modules.items() if is_backend_module(name)}


def purge() -> None:
    for name in [name for name in sys.modules if is_backend_module(name)]:
        sys.modules.pop(name, None)


def restore(before: dict) -> None:
    for name in [name for name in sys.modules if is_backend_module(name) and name not in before]:
        mod = sys.modules.pop(name)
        parent, _, child = name.rpartition(".")
        owner = before.get(parent)
        # `from pkg import child` reads the package attribute before importing.
        if owner is not None and getattr(owner, child, None) is mod:
            delattr(owner, child)
    sys.modules.update(before)
    for name, mod in before.items():
        parent, _, child = name.rpartition(".")
        owner = before.get(parent)
        if owner is not None and mod is not None:
            setattr(owner, child, mod)
