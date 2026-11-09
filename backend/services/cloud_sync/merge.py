"""Three-way merge of synced ``values`` objects (desktop-app-guide §7).

Per key: a side that changed wins; when both changed differently, local wins
and the key is reported as a conflict. Values compare as canonical JSON.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

_ABSENT = object()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _same(a: Any, b: Any) -> bool:
    if a is _ABSENT or b is _ABSENT:
        return a is b
    return canonical(a) == canonical(b)


@dataclass
class Merge:
    values: Optional[dict]
    conflicts: list[str] = field(default_factory=list)


def merge3(base: Optional[dict], local: dict, remote: dict) -> Merge:
    """Merge ``values``; ``base=None`` is a first sync (empty base)."""
    base = base or {}
    out: dict = {}
    conflicts: list[str] = []
    for key in sorted(set(base) | set(local) | set(remote)):
        b = base.get(key, _ABSENT)
        l = local.get(key, _ABSENT)  # noqa: E741
        r = remote.get(key, _ABSENT)
        if _same(l, r):
            chosen = l
        elif _same(b, l):
            chosen = r  # only remote changed
        elif _same(b, r):
            chosen = l  # only local changed
        elif l is _ABSENT:
            chosen = r  # removed locally, changed remotely: the change wins
        elif r is _ABSENT:
            chosen = l  # changed locally, removed remotely: the change wins
        else:
            chosen = l  # both changed differently: local wins
            conflicts.append(key)
        if chosen is not _ABSENT:
            out[key] = chosen
    return Merge(out, conflicts)


def merge_item(base: Optional[dict], local: Optional[dict], remote: Optional[dict]) -> Merge:
    """Item level: ``None`` is a deleted (or missing) item. An edit beats a delete."""
    if local is None and remote is None:
        return Merge(None)
    if local is None:
        if base is not None and _same(base, remote):
            return Merge(None)  # deleted here, untouched there
        return Merge(dict(remote))
    if remote is None:
        if base is not None and _same(base, local):
            return Merge(None)  # deleted there, untouched here
        return Merge(dict(local))
    return merge3(base, local, remote)
