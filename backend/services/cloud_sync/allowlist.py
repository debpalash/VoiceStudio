"""What syncs, and where each synced value lives locally (sync-collections.md, S1).

An item's ``values`` are assembled from slots. Each slot reads one local value
and writes it back: a prefs key, a settings-table text row, a renderer
localStorage key mirrored into ``cloud_sync_local``, or one field of such a
key. Anything not listed here never syncs: secrets, paths, hardware and engine
choices, analytics IDs, and the device-local keys of sync-collections.md.

The ``dictation`` group (S1-23, recorded for vssaas): ``aecEnabled`` from
localStorage ``voicestudio.dictation-settings.v1`` plus prefs
``dictation.mode``, ``dictation.enabled`` and ``dictation.prompt``. The model
choices (``dictation.model_id``, ``dictation.silent_models``) and the input
device stay on the device.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core import prefs
from core import db as core_db
from services import settings_store
from services.cloud_sync.store import SyncStore

__all__ = [
    "ABSENT", "CHOICE_COLLECTIONS", "ITEMS", "Item", "PARTIAL_KEYS", "RAW_KEYS", "RENDERER_KEYS",
    "item", "item_for", "items_in", "read_values", "write_values",
]

ABSENT: Any = object()
Source = tuple[str, str]  # ("prefs" | "settings" | "local", key)


def _store() -> SyncStore:
    return SyncStore()


class Slot:
    """Reads and writes one local value; see Pref, Setting, Local and LocalField."""

    source: Source

    def read(self) -> Any:
        raise NotImplementedError

    def write(self, value: Any) -> None:
        raise NotImplementedError


@dataclass
class Pref(Slot):
    key: str

    @property
    def source(self) -> Source:
        return ("prefs", self.key)

    def read(self) -> Any:
        value = prefs.get(self.key, ABSENT)
        return ABSENT if value is None else value

    def write(self, value: Any) -> None:
        if value is ABSENT:
            if prefs.get(self.key) is not None:
                prefs.delete(self.key)
        else:
            prefs.set_(self.key, value)


@dataclass
class Setting(Slot):
    key: str

    @property
    def source(self) -> Source:
        return ("settings", self.key)

    def read(self) -> Any:
        value = settings_store.get_text(self.key)
        return ABSENT if value is None else value

    def write(self, value: Any) -> None:
        if value is ABSENT:
            settings_store.clear_text(self.key)
        else:
            settings_store.set_text(self.key, str(value))


@dataclass
class Local(Slot):
    """A whole renderer localStorage value (parsed JSON, or the raw string)."""

    key: str

    @property
    def source(self) -> Source:
        return ("local", self.key)

    def read(self) -> Any:
        value = _store().get_local(self.key)
        return ABSENT if value is None else value

    def write(self, value: Any) -> None:
        store = _store()
        wanted = None if value is ABSENT else value
        if store.get_local(self.key) == wanted:
            return  # unchanged: do not wake the renderer
        store.set_local(self.key, wanted, origin="sync")


@dataclass
class LocalField(Slot):
    """One field of a renderer localStorage object; the other fields are kept."""

    key: str
    name: str

    @property
    def source(self) -> Source:
        return ("local", self.key)

    def read(self) -> Any:
        whole = _store().get_local(self.key)
        if not isinstance(whole, dict) or self.name not in whole:
            return ABSENT
        return whole[self.name]

    def write(self, value: Any) -> None:
        store = _store()
        whole = store.get_local(self.key)
        whole = dict(whole) if isinstance(whole, dict) else {}
        if value is ABSENT:
            if self.name not in whole:
                return
            whole.pop(self.name)
        else:
            if whole.get(self.name, ABSENT) == value:
                return
            whole[self.name] = value
        store.set_local(self.key, whole, origin="sync")


@dataclass
class Item:
    collection: str
    item_id: str
    slots: dict[str, Slot] = field(default_factory=dict)
    reader: Optional[Callable[[], dict]] = None
    writer: Optional[Callable[[dict], None]] = None
    extra_sources: tuple[Source, ...] = ()
    prefixes: tuple[Source, ...] = ()
    #: Value keys this version reads and writes; others are kept as they came. ``None``: every key.
    keys: Optional[frozenset[str]] = None
    #: One fixed item (a settings group): a cloud tombstone never wipes the local values.
    singleton: bool = True

    @property
    def managed(self) -> Optional[frozenset[str]]:
        if self.keys is not None:
            return self.keys
        return frozenset(self.slots) if self.slots else None

    @property
    def sources(self) -> tuple[Source, ...]:
        seen: list[Source] = []
        for source in [s.source for s in self.slots.values()] + list(self.extra_sources):
            if source not in seen:
                seen.append(source)
        return tuple(seen)

    def read(self) -> dict:
        if self.reader:
            return self.reader()
        values = {}
        for name, slot in self.slots.items():
            value = slot.read()
            if value is not ABSENT:
                values[name] = value
        return values

    def write(self, values: dict) -> None:
        if self.writer:
            self.writer(values)
            return
        for name, slot in self.slots.items():
            slot.write(values.get(name, ABSENT))


# ── llm: provider + that provider's model and base URL, and skill routing ──
_LLM_PROVIDER = "llm.active_provider"


def _skill_rows() -> dict[str, dict[str, str]]:
    skills: dict[str, dict[str, str]] = {}
    with core_db.db_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'llm\\_skill.%' ESCAPE '\\'").fetchall()
    for key, value in rows:
        parts = key.split(".")
        if len(parts) == 3 and parts[2] in ("enabled", "provider"):
            skills.setdefault(parts[1], {})[parts[2]] = value
    return skills


def _read_llm() -> dict:
    values: dict[str, Any] = {}
    provider = settings_store.get_text(_LLM_PROVIDER)
    if provider:
        values["provider"] = provider
        model = settings_store.get_text(f"llm.model.{provider}")
        base_url = settings_store.get_text(f"llm.base_url.{provider}")
        if model:
            values["model"] = model
        if base_url:
            values["baseUrl"] = base_url
    skills = _skill_rows()
    if skills:
        values["skills"] = skills
    return values


def _write_llm(values: dict) -> None:
    provider = values.get("provider")
    if provider:
        settings_store.set_text(_LLM_PROVIDER, str(provider))
        for name, prefix in (("model", "llm.model."), ("baseUrl", "llm.base_url.")):
            if name in values:
                settings_store.set_text(prefix + provider, str(values[name]))
            else:
                settings_store.clear_text(prefix + provider)
    else:
        settings_store.clear_text(_LLM_PROVIDER)
    wanted = values.get("skills") or {}
    current = _skill_rows()
    for sid in set(current) | set(wanted):
        for part in ("enabled", "provider"):
            key = f"llm_skill.{sid}.{part}"
            value = (wanted.get(sid) or {}).get(part)
            if value is None:
                if part in current.get(sid, {}):
                    settings_store.clear_text(key)
            elif current.get(sid, {}).get(part) != value:
                settings_store.set_text(key, str(value))


# ── favorites: an ID array locally, a keyed map with first-seen times in sync ──
FAVORITES_KEY = "voicestudio.gallery.favorites.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_favorites() -> dict:
    store = _store()
    ids = store.get_local(FAVORITES_KEY)
    if not isinstance(ids, list):
        return {}
    meta = store.local_meta(FAVORITES_KEY)
    added = dict(meta.get("added_at") or {})
    values = {}
    changed = False
    for fav in ids:
        if not isinstance(fav, str):
            continue
        if fav not in added:
            added[fav] = _utc_now()
            changed = True
        values[fav] = {"added_at": added[fav]}
    if changed:
        with core_db.db_conn() as conn:
            conn.execute(
                "UPDATE cloud_sync_local SET meta_json=? WHERE key=?",
                (json.dumps({**meta, "added_at": added}), FAVORITES_KEY),
            )
    return values


def _write_favorites(values: dict) -> None:
    store = _store()
    meta = store.local_meta(FAVORITES_KEY)
    added = {fav: str((entry or {}).get("added_at") or _utc_now()) for fav, entry in values.items()}
    ordered = sorted(added, key=lambda fav: added[fav])
    store.set_local(FAVORITES_KEY, ordered, origin="sync", meta={**meta, "added_at": added})


_GENERATION_FIELDS = (
    "language", "instruct", "steps", "wavBits", "effectPreset", "cfg", "speed", "tShift",
    "posTemp", "classTemp", "layerPenalty", "denoise", "postprocess", "showOverrides", "autoPlay",
)
_CLONE = "voicestudio.clone.settings.v1"
_DICTATION_LOCAL = "voicestudio.dictation-settings.v1"
SHORTCUT_KEY = "voicestudio.shortcut.dictation"  # virtual: the renderer maps it to the capture bridge

ITEMS: tuple[Item, ...] = (
    Item("settings", "appearance", {
        "theme": Local("voicestudio.theme.v2"),
        "appearance": Local("voicestudio.appearance"),
        "locale": Local("voicestudio.locale"),
    }),
    Item("settings", "generation-defaults", {name: LocalField(_CLONE, name) for name in _GENERATION_FIELDS}),
    Item("settings", "review", {"reviewMode": Local("voicestudio.review-mode.v1")}),
    Item("settings", "transcription", {"mode": Local("voicestudio.transcription.mode")}),
    Item("settings", "dictation", {
        "aecEnabled": LocalField(_DICTATION_LOCAL, "aecEnabled"),
        "mode": Pref("dictation.mode"),
        "enabled": Pref("dictation.enabled"),
        "prompt": Pref("dictation.prompt"),
    }),
    Item("settings", "history", {"generationHistoryCap": Pref("generation_history_cap")}),
    Item(
        "settings", "llm", reader=_read_llm, writer=_write_llm,
        extra_sources=(("settings", _LLM_PROVIDER),),
        prefixes=(("settings", "llm.model."), ("settings", "llm.base_url."), ("settings", "llm_skill.")),
        keys=frozenset({"provider", "model", "baseUrl", "skills"}),
    ),
    Item("shortcuts", "global", {"dictation.toggle": Local(SHORTCUT_KEY)}),
    Item("favorites", "gallery", reader=_read_favorites, writer=_write_favorites,
         extra_sources=(("local", FAVORITES_KEY),)),
)

_BY_ID = {(item.collection, item.item_id): item for item in ITEMS}
_BY_SOURCE = {source: item for item in ITEMS for source in item.sources}

#: Renderer localStorage keys the renderer must mirror into the backend.
RENDERER_KEYS: tuple[str, ...] = tuple(
    sorted({key for item in ITEMS for kind, key in item.sources if kind == "local"})
)

#: Renderer keys stored as plain strings; every other renderer key holds JSON.
RAW_KEYS: frozenset[str] = frozenset({
    "voicestudio.locale", "voicestudio.review-mode.v1", "voicestudio.transcription.mode", SHORTCUT_KEY,
})

#: Renderer keys of which only some fields sync (LocalField): a pulled value merges
#: into the local object, so device-local fields such as the clone draft stay.
PARTIAL_KEYS: frozenset[str] = frozenset(
    slot.key for item in ITEMS for slot in item.slots.values() if isinstance(slot, LocalField)
)

#: Collections a ``choices`` toggle enables (sync-collections.md device-preferences).
CHOICE_COLLECTIONS = {"settings": ("settings",), "shortcuts": ("shortcuts",), "favorites": ("favorites",)}


def item(collection: str, item_id: str) -> Optional[Item]:
    return _BY_ID.get((collection, item_id))


def item_for(source: str, key: str) -> Optional[Item]:
    found = _BY_SOURCE.get((source, key))
    if found:
        return found
    for candidate in ITEMS:
        for kind, prefix in candidate.prefixes:
            if kind == source and key.startswith(prefix) and not key.startswith("secret."):
                return candidate
    return None


def items_in(collections: set[str]) -> list[Item]:
    return [i for i in ITEMS if i.collection in collections]


def read_values(collection: str, item_id: str) -> dict:
    return _BY_ID[(collection, item_id)].read()


def write_values(collection: str, item_id: str, values: dict) -> None:
    _BY_ID[(collection, item_id)].write(values)
