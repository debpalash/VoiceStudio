"""Where work runs: this machine, or one node the user picked.

The scheduler underneath can rank many workers, and the hosted platform will
need that. The OSS product deliberately does not expose it. Here the user
chooses a single target from a list — ``Local``, or one of the machines they
enrolled — and that choice is the whole policy:

    GPU: [ Local ▾ ]        Local
                            desktop-4090    192.168.0.222:2222
                            laptop-m2       192.168.0.31:7443

Two reasons this beats automatic selection for a desktop app. It is
predictable — a user who sends a job to their 4090 can see that is where it
went, rather than discovering the scheduler preferred a laptop. And it is
explainable when it goes wrong: "your chosen worker is offline, this ran
locally" is a sentence; "least-busy ranking picked another node" is not.

Exactly one target is active at a time. Other enrolled workers may be
connected — they simply receive nothing, which is what standby means.

Fallback is not one rule but three, because "it ran locally instead" is a
kindness in the first case and a lie in the others:

  1. **Before dispatch** — the chosen worker is offline, disabled, paused, or
     gone — the work runs here, quietly, with the named reason this module
     produces. A user who picks their desktop and then walks over to switch it
     on should get their audio, not an error about infrastructure.
  2. **Mid-job, single-shot interactive work** raises instead. Silently
     redoing minutes of remote work on the wrong machine is not a fallback;
     the error carries a "run locally instead" that resubmits.
  3. **Multi-unit jobs** (audiobook chapters, batches) fall back per unit
     after N consecutive remote failures, with one aggregated notice — a 4090
     that goes to sleep at chapter 40 must not turn a working book into 160
     identical error rows.

Only rule 1 lives here; rules 2 and 3 belong to the job surfaces that own the
work, because only they know whether a unit has already started.

Targeting is also per operation. The scheduler will place anything a worker
advertises, but a job only reaches the scheduler where this side has a
producer for it — so ``decide(op=...)`` answers for the surface the user is
looking at rather than for the machine, and the badge cannot read
"gpu2 ● ready" on a tab whose work is 100% local.

Speech synthesis, chapter-at-a-time audiobook and Stories rendering, and
coarse ``dub_segments`` synthesis have remote producers. Dub assembly, ASR,
diarization, translation and RVC remain local. Dictation is
intentionally local regardless of the selected target because its latency is
the feature.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("omnivoice.worker")

# Sentinel for "run on this machine". Not a worker id, and never confusable
# with one: worker ids are 12 hex characters.
LOCAL = "local"

# Operations with a remote producer today. Ports land one at a time, and this
# set is what keeps the picker honest about which ones have arrived.
#
# `dub` / `batch` are the surfaces the user picks; `dub_segments` and
# `batch_segments` are their coarse worker ops. Both pairs belong here because the GPU work does
# leave this machine — listing only the worker op would make the Dub tab read
# "Local" while a remote card renders it.
#
# Dictation is deliberately absent and should stay that way: it runs ASR per
# utterance inside a live WebSocket loop, where a round trip per utterance
# would spend the one thing that route exists for.
REMOTE_OPERATIONS = frozenset(
    {
        "audiobook",
        "batch",
        "batch_segments",
        "clone",
        "dub",
        "dub_segments",
        "longform",
        "tts",
    }
)

# What a caller-supplied ``op`` must look like. The renderer names the SURFACE
# it is rendering (``design``, ``profile-preview``, ``compare``, ``asr``,
# ``dictation`` ...), and that set grows with the UI, so it cannot be an
# enumerated allow-list without 422-ing the next screen. The shape is the
# contract: a short lowercase identifier. Anything else is not a surface name.
OPERATION_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def valid_operation_name(op: str) -> bool:
    return OPERATION_NAME.fullmatch(op) is not None


# Only for the sentence the user reads; an unknown op falls back to its id
# rather than inventing a name for it.
_OP_LABELS = {
    "tts": "speech synthesis",
    "clone": "voice cloning",
    "batch": "batch dubbing",
    "batch_segments": "batch dubbing",
    "dub": "dubbing",
    "dub_segments": "dubbing",
    "audiobook": "audiobook rendering",
    "longform": "story rendering",
    "dictation": "dictation",
    "asr": "transcription",
}

_SETTING_KEY = "worker_target"


@dataclass(frozen=True)
class Target:
    """One selectable entry in the GPU picker."""

    id: str
    label: str
    endpoint: str = ""
    connected: bool = False
    available: bool = False
    detail: str = ""
    # ready | busy | offline — what the header dot is coloured on.
    status: str = "offline"
    latency_ms: float = 0.0
    active_tasks: int = 0
    max_tasks: int = 0
    cpu_percent: Optional[float] = None
    free_memory_bytes: Optional[int] = None
    system_memory_bytes: int = 0
    cpu_count: int = 0
    gpu_name: str = ""
    gpu_memory_bytes: int = 0
    gpu_utilization_percent: Optional[float] = None

    @property
    def is_local(self) -> bool:
        return self.id == LOCAL

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "endpoint": self.endpoint,
            "connected": self.connected,
            "available": self.available,
            "detail": self.detail,
            "is_local": self.is_local,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "active_tasks": self.active_tasks,
            "max_tasks": self.max_tasks,
            "cpu_percent": self.cpu_percent,
            "free_memory_bytes": self.free_memory_bytes,
            "system_memory_bytes": self.system_memory_bytes,
            "cpu_count": self.cpu_count,
            "gpu_name": self.gpu_name,
            "gpu_memory_bytes": self.gpu_memory_bytes,
            "gpu_utilization_percent": self.gpu_utilization_percent,
        }


def get_target_id() -> str:
    """The user's choice, or Local.

    Persisted, because a target that resets to Local on every app start would
    quietly send work to the wrong machine after a restart.
    """
    try:
        from services import settings_store  # noqa: PLC0415

        stored = (settings_store.get_text(_SETTING_KEY, "") or "").strip()
    except Exception:
        return LOCAL
    return stored or LOCAL


def set_target_id(target_id: str) -> str:
    """Record the user's choice. Returns what was actually stored."""
    from services import settings_store  # noqa: PLC0415

    chosen = (target_id or LOCAL).strip() or LOCAL
    settings_store.set_text(_SETTING_KEY, chosen)
    return chosen


def local_target(*, label: str = "Local") -> Target:
    # This machine is reachable by definition and has no network latency to
    # report — showing "0 ms" next to it would invite a false comparison.
    return Target(id=LOCAL, label=label, connected=True, available=True, status="ready")


def list_targets(control_plane=None) -> list[Target]:
    """Local first, then every enrolled worker.

    Revoked workers are omitted; a disabled or disconnected one is listed but
    not ``available``, so the picker can show it greyed rather than pretending
    the machine vanished.
    """
    targets = [local_target()]
    if control_plane is None:
        from worker.service import control_plane as default_plane  # noqa: PLC0415

        control_plane = default_plane

    try:
        from worker import registry  # noqa: PLC0415

        enrolled = registry.list_workers()
    except Exception:
        logger.debug("Could not list enrolled workers", exc_info=True)
        return targets

    pool = getattr(control_plane, "pool", None) if control_plane.running else None
    for record in enrolled:
        live = pool.get(record.id) if pool is not None else None
        host = record.host or {}
        gpu = (host.get("gpus") or [{}])[0]
        connected = live is not None and not live.stale()
        available, detail = _availability(record, live, pool)
        targets.append(
            Target(
                id=record.id,
                label=record.name or record.key_id,
                # The address it connected FROM beats any self-reported one.
                endpoint=(live.address if live and live.address else record.endpoint),
                connected=connected,
                available=available,
                detail=detail,
                status=_status_for(record, live, available),
                latency_ms=live.latency_ms if live else 0.0,
                active_tasks=live.capacity.active_tasks if live else 0,
                max_tasks=live.capacity.max_concurrent_tasks if live else 0,
                cpu_percent=live.capacity.cpu_percent if live else None,
                free_memory_bytes=live.capacity.free_memory_bytes if live else None,
                system_memory_bytes=int(host.get("system_memory_bytes") or 0),
                cpu_count=int(host.get("cpu_count") or 0),
                gpu_name=str(gpu.get("model") or ""),
                gpu_memory_bytes=int(gpu.get("memory_bytes") or 0),
                gpu_utilization_percent=live.capacity.gpu_utilization_percent if live else None,
            )
        )
    return targets


def _status_for(record, live, available: bool) -> str:
    """ready / busy / offline — the three states the header dot colours on.

    A worker that is connected but unusable for a config reason (disabled, not
    approved, paused) is NOT "ready"; calling it ready would promise work can
    go there when it cannot.
    """
    if live is None or live.stale():
        return "offline"
    if available:
        return live.status
    return "busy" if live.status == "busy" else "offline"


def _availability(record, live, pool) -> tuple[bool, str]:
    """Can this worker take work right now, and if not, why not?

    The reason is user-facing: it is what the picker shows under a greyed
    entry, so it has to name something the user can act on.
    """
    if record.revoked:
        return False, "removed"
    if not record.enabled:
        return False, "disabled"
    if record.consent_granted_at is None:
        return False, "not approved"
    if live is None:
        return False, "offline"
    if live.stale():
        return False, "not responding"
    if live.draining:
        return False, "shutting down"
    if pool is not None and pool.breakers.open_breakers(record.id):
        return False, "paused after repeated failures"
    return True, ""


@dataclass(frozen=True)
class Decision:
    """Where one job should run, and why."""

    remote: bool
    worker_id: Optional[str] = None
    label: str = "Local"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "remote": self.remote,
            "worker_id": self.worker_id,
            "label": self.label,
            "reason": self.reason,
        }


def supports_operation(op: Optional[str]) -> bool:
    """Does this kind of work have a remote path at all?

    ``None`` means "asking about the target itself, not about one job", which
    is what the picker's own menu wants.
    """
    return op is None or op in REMOTE_OPERATIONS


def _op_label(op: str) -> str:
    return _OP_LABELS.get(op, op)


def _unsupported_decision(op: str) -> Decision:
    return Decision(
        remote=False,
        reason=f"{_op_label(op)} does not run remotely yet — running locally",
    )


def status_for_operation(snapshot: dict, op: Optional[str]) -> dict:
    """Derive ``status(op=op)`` from the target-wide ``status()`` snapshot.

    ``decide`` depends on the operation only through :func:`supports_operation`:
    every supported operation resolves exactly like "the target as a whole", and
    an unsupported one is answered before reachability is consulted. So one
    snapshot serves every ``op``, which lets a status poll keep a single
    in-flight computation no matter what operation names callers send.
    """
    if not op:
        return snapshot
    derived = dict(snapshot, op=op)
    if snapshot.get("target") != LOCAL and not supports_operation(op):
        derived["active"] = _unsupported_decision(op).to_dict()
    return derived


def decide(control_plane=None, *, op: Optional[str] = None) -> Decision:
    """Resolve the user's choice against what is actually reachable.

    This is the single answer to "where does the next job run", used both by
    the generation path and by the header badge — so the badge cannot claim
    something the router will not do.

    ``op`` narrows it to one kind of work. An unported operation is answered
    before reachability is even consulted: whether the 4090 is awake is beside
    the point when nothing on this side would send it a dub.
    """
    target_id = get_target_id()
    if target_id == LOCAL:
        return Decision(remote=False, reason="chosen")

    if not supports_operation(op):
        return _unsupported_decision(op)

    if control_plane is None:
        from worker.service import control_plane as default_plane  # noqa: PLC0415

        control_plane = default_plane

    if not control_plane.running:
        return Decision(remote=False, reason="remote workers are turned off")

    for target in list_targets(control_plane):
        if target.id != target_id:
            continue
        if target.available:
            return Decision(
                remote=True, worker_id=target.id, label=target.label, reason="chosen"
            )
        # Chosen but unusable: run locally and say which machine was skipped.
        return Decision(
            remote=False,
            label="Local",
            reason=f"{target.label} is {target.detail or 'unavailable'} — running locally",
        )

    # The chosen worker no longer exists (removed on another device, or the
    # row was cleaned up). Fall back rather than stranding the user.
    return Decision(remote=False, reason="the chosen worker no longer exists — running locally")


def status(control_plane=None, *, op: Optional[str] = None) -> dict:
    """Everything the GPU picker needs, in one call.

    ``op`` is echoed back so a caller that switched tabs mid-request can tell
    which surface the answer describes, and ``remote_operations`` is what lets
    the menu say "gpu2 · TTS only" instead of implying it takes everything.
    """
    decision = decide(control_plane, op=op)
    return {
        "target": get_target_id(),
        "op": op or "",
        "active": decision.to_dict(),
        "remote_operations": sorted(REMOTE_OPERATIONS),
        "targets": [t.to_dict() for t in list_targets(control_plane)],
    }


__all__ = [
    "LOCAL",
    "OPERATION_NAME",
    "REMOTE_OPERATIONS",
    "Decision",
    "Target",
    "decide",
    "get_target_id",
    "list_targets",
    "local_target",
    "set_target_id",
    "status",
    "status_for_operation",
    "supports_operation",
    "valid_operation_name",
]
