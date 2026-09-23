"""Call-agent sessions: placing calls, tracking them, and keeping the record.

A :class:`CallSession` exists from the moment the user places a call (or an
incoming call is answered in agent mode) until it is finalized. It is shared by
three parties that may run on different event loops/threads:

- the main API (``api.routers.calls``): create, observe (SSE), take over, hang up;
- the telephony gateway's webhooks: provider status, the media stream;
- the :class:`~services.telephony.agent.CallAgent` driving the conversation.

So every mutation takes the session lock and events are handed to each SSE
subscriber's own loop with ``call_soon_threadsafe``.

Records persist in the local ``call_sessions`` table (full numbers stay on
this machine; the API only ever returns a masked number). Nothing here starts a
call on its own: every outbound call begins with an explicit ``POST /calls``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from services.telephony import config

logger = logging.getLogger("omnivoice.telephony.calls")

TERMINAL_STATUSES = ("completed", "busy", "no_answer", "failed", "canceled")
_STATUS_RANK = {"queued": 0, "initiated": 1, "ringing": 2, "in_progress": 3}
#: A call not answered by now is abandoned (lost status callbacks, a stream
#: that never connected). Twilio gives up ringing long before this.
_UNANSWERED_TTL_S = 300
_SUMMARY_TIMEOUT_S = 45.0
_UNCONNECTED_SUMMARY = {
    "busy": "The line was busy.",
    "no_answer": "No one answered.",
    "canceled": "The call was canceled before it connected.",
    "failed": "The call could not be connected.",
    "completed": "The call ended before the agent connected.",
}


class CallError(Exception):
    """A user-correctable refusal; mapped to an HTTP error by the router."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra

    def detail(self) -> dict:
        return {"code": self.code, "message": str(self), **self.extra}


def mask_number(number: str) -> str:
    """``+14155550123`` → ``+1•••••••0123``."""
    number = number or ""
    if len(number) <= 6:
        return "•" * len(number)
    return number[:2] + "•" * (len(number) - 6) + number[-4:]


@dataclass
class CallSession:
    id: str
    direction: str  # outbound | inbound
    remote_number: str
    from_number: str
    brief: str
    profile_id: str
    engine: str = ""
    language: str = ""
    disclosure: str = ""
    user_name: str = ""
    max_minutes: int = 10
    record: bool = False
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    outcome: str | None = None
    summary: str = ""
    provider_call_id: str = ""
    takeover: bool = False
    agent_state: str | None = None
    recording_path: str = ""
    error: str = ""
    timeline: list = field(default_factory=list)
    transcript: list = field(default_factory=list)
    finalized: bool = False
    stream_connected: bool = False

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._agent = None
        self.timeline.append({"status": self.status, "t": round(time.time(), 3)})

    # ── Serialisation ──

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.ended_at or time.time()
        return round(max(0.0, end - self.started_at), 1)

    def as_record(self, *, transcript: bool = False) -> dict:
        with self._lock:
            rec = {
                "id": self.id,
                "direction": self.direction,
                "to_masked": mask_number(self.remote_number),
                "status": self.status,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "duration_s": self.duration_s,
                "profile_id": self.profile_id,
                "brief": self.brief,
                "outcome": self.outcome,
                "summary": self.summary,
                "disclosure": self.disclosure,
                "takeover": self.takeover,
                "agent_state": None if self.finalized else self.agent_state,
                "recording": bool(self.recording_path),
                "error": self.error or None,
            }
            if transcript:
                rec["transcript"] = [dict(turn) for turn in self.transcript]
                rec["timeline"] = [dict(item) for item in self.timeline]
            return rec

    # ── Events ──

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            if self.finalized:
                for event in self._closing_events():
                    queue.put_nowait(event)
            else:
                self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(lp, q) for lp, q in self._subscribers if q is not queue]

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # that subscriber's loop is gone
                self.unsubscribe(queue)

    def _elapsed(self) -> float:
        return round(time.time() - (self.started_at or self.created_at), 2)

    def _closing_events(self) -> list[dict]:
        return [
            {"type": "outcome", "outcome": self.outcome, "summary": self.summary},
            {"type": "ended", "call": self.as_record()},
        ]

    # ── Mutations (thread-safe) ──

    def set_status(self, status: str) -> bool:
        """Advance the status; never regresses and never leaves a terminal state."""
        with self._lock:
            if self.status in TERMINAL_STATUSES or status == self.status:
                return False
            if status not in TERMINAL_STATUSES and _STATUS_RANK.get(status, -1) <= _STATUS_RANK.get(self.status, -1):
                return False
            self.status = status
            now = time.time()
            if status == "in_progress" and self.started_at is None:
                self.started_at = now
            self.timeline.append({"status": status, "t": round(now, 3)})
        store_save(self)
        self.publish({"type": "status", "status": status, "t": self._elapsed()})
        return True

    def add_turn(self, speaker: str, text: str, **extra) -> None:
        turn = {"speaker": speaker, "text": text, "t": self._elapsed(), **extra}
        with self._lock:
            self.transcript.append(turn)
        store_save(self)
        self.publish({"type": "transcript", "speaker": speaker, "text": text, "final": True, "t": turn["t"], **extra})

    def agent_partial(self, text: str) -> None:
        self.publish({"type": "transcript", "speaker": "agent", "text": text, "final": False, "t": self._elapsed()})

    def mark_last_agent_interrupted(self) -> None:
        with self._lock:
            for turn in reversed(self.transcript):
                if turn.get("speaker") == "agent":
                    turn["interrupted"] = True
                    break

    def set_agent_state(self, state: str) -> None:
        with self._lock:
            self.agent_state = state
        self.publish({"type": "agent_state", "state": state})

    def set_outcome(self, outcome: str) -> None:
        with self._lock:
            if self.outcome is None:
                self.outcome = outcome

    def bind_agent(self, agent) -> None:
        with self._lock:
            self._agent = agent

    def unbind_agent(self, agent) -> None:
        with self._lock:
            if self._agent is agent:
                self._agent = None

    @property
    def agent(self):
        with self._lock:
            return self._agent


# ── Persistence (local SQLite, table call_sessions) ─────────────────────────

_COLUMNS = (
    "id", "direction", "remote_number", "remote_masked", "from_number", "provider", "provider_call_id",
    "status", "brief", "profile_id", "engine", "language", "disclosure", "max_minutes", "outcome",
    "summary", "transcript_json", "timeline_json", "recording_path", "error", "created_at",
    "started_at", "ended_at", "duration_s",
)
#: Built once from the fixed column tuple above (no caller input).
_UPSERT_SQL = (
    f"INSERT OR REPLACE INTO call_sessions ({','.join(_COLUMNS)}) "  # nosec B608 — constant column names
    f"VALUES ({','.join('?' for _ in _COLUMNS)})"
)


def store_save(session: CallSession) -> None:
    from core.db import db_conn

    with session._lock:
        values = (
            session.id, session.direction, session.remote_number, mask_number(session.remote_number),
            session.from_number, "twilio", session.provider_call_id, session.status, session.brief,
            session.profile_id, session.engine, session.language, session.disclosure, session.max_minutes,
            session.outcome, session.summary, json.dumps(session.transcript), json.dumps(session.timeline),
            session.recording_path, session.error, session.created_at, session.started_at,
            session.ended_at, session.duration_s if session.finalized else None,
        )
    try:
        with db_conn() as conn:
            conn.execute(_UPSERT_SQL, values)
    except Exception:  # noqa: BLE001 — a DB hiccup must not drop a live call
        logger.warning("Could not save call %s", session.id, exc_info=True)


def _row_record(row, *, transcript: bool) -> dict:
    status = row["status"]
    rec = {
        "id": row["id"],
        "direction": row["direction"],
        "to_masked": row["remote_masked"],
        "status": status,
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_s": row["duration_s"],
        "profile_id": row["profile_id"],
        "brief": row["brief"],
        "outcome": row["outcome"],
        "summary": row["summary"] or "",
        "disclosure": row["disclosure"] or "",
        "takeover": False,
        "agent_state": None,
        "recording": bool(row["recording_path"]),
        "error": row["error"] or None,
    }
    if transcript:
        rec["transcript"] = _json_list(row["transcript_json"])
        rec["timeline"] = _json_list(row["timeline_json"])
    return rec


def _json_list(raw) -> list:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _mark_orphans(conn) -> None:
    """A non-final row with no live session was cut off by a restart/crash."""
    with _registry_lock:
        live = set(_sessions)
    rows = conn.execute(
        "SELECT id FROM call_sessions WHERE status NOT IN "
        "('completed', 'busy', 'no_answer', 'failed', 'canceled')"
    ).fetchall()
    for (cid,) in rows:
        if cid not in live:
            conn.execute(
                "UPDATE call_sessions SET status='failed', outcome=COALESCE(outcome,'failed'), "
                "error=CASE WHEN error='' THEN 'VoiceStudio stopped during the call' ELSE error END, "
                "ended_at=COALESCE(ended_at, created_at) WHERE id=?",
                (cid,),
            )


def list_records(limit: int = 50) -> list[dict]:
    from core.db import db_conn

    sweep()
    with db_conn() as conn:
        _mark_orphans(conn)
        rows = conn.execute(
            "SELECT * FROM call_sessions ORDER BY created_at DESC LIMIT ?", (max(1, min(200, limit)),)
        ).fetchall()
    out = []
    for row in rows:
        live = get_live(row["id"])
        out.append(live.as_record() if live else _row_record(row, transcript=False))
    return out


def get_record(call_id: str) -> dict | None:
    from core.db import db_conn

    sweep()
    live = get_live(call_id)
    if live is not None:
        return live.as_record(transcript=True)
    with db_conn() as conn:
        _mark_orphans(conn)
        row = conn.execute("SELECT * FROM call_sessions WHERE id=?", (call_id,)).fetchone()
    return _row_record(row, transcript=True) if row else None


def recording_path(call_id: str) -> str | None:
    from core.db import db_conn

    with db_conn() as conn:
        row = conn.execute("SELECT recording_path FROM call_sessions WHERE id=?", (call_id,)).fetchone()
    path = row["recording_path"] if row else ""
    return path if path and os.path.isfile(path) else None


def delete_record(call_id: str) -> bool:
    from core.db import db_conn

    if get_live(call_id) is not None:
        raise CallError(409, "call_active", "Hang up before deleting this call")
    path = recording_path(call_id)
    with db_conn() as conn:
        deleted = conn.execute("DELETE FROM call_sessions WHERE id=?", (call_id,)).rowcount
    if path:
        try:
            os.unlink(path)
        except OSError:
            logger.warning("Could not delete a call recording")
    return bool(deleted)


# ── Live registry ───────────────────────────────────────────────────────────

_sessions: dict[str, CallSession] = {}
_registry_lock = threading.Lock()


def get_live(call_id: str) -> CallSession | None:
    with _registry_lock:
        return _sessions.get(call_id)


def _register(session: CallSession) -> CallSession:
    with _registry_lock:
        _sessions[session.id] = session
    store_save(session)
    return session


def _unregister(session: CallSession) -> None:
    with _registry_lock:
        if _sessions.get(session.id) is session:
            del _sessions[session.id]


def active_agent_calls() -> int:
    """Live agent calls in either direction: ``max_concurrent`` limits them all."""
    with _registry_lock:
        return sum(1 for s in _sessions.values() if not s.finalized)


def has_agent_capacity() -> bool:
    sweep()
    return active_agent_calls() < config.load_call_settings().max_concurrent


def reset_state() -> None:
    """Tests: forget every live session."""
    with _registry_lock:
        _sessions.clear()


def sweep(now: float | None = None) -> None:
    """Close sessions that can no longer connect (lost callbacks)."""
    now = time.time() if now is None else now
    with _registry_lock:
        stale = [
            s for s in _sessions.values()
            if not s.finalized and not s.stream_connected and now - s.created_at > _UNANSWERED_TTL_S
        ]
    for session in stale:
        finish_unconnected(session, "failed", error="The call never connected")


# ── Checks shared by readiness and placing a call ───────────────────────────


def llm_status() -> tuple[bool, str]:
    try:
        from services import llm_backend

        bid = llm_backend.active_backend_id()
        if bid == "off":
            return False, "Set up an LLM in Settings → LLM Providers (a local Ollama or LM Studio works)."
        ok, detail = llm_backend.get_active_llm_backend().is_available()
        return bool(ok), detail
    except Exception as exc:  # noqa: BLE001
        return False, f"LLM unavailable ({type(exc).__name__})"


def asr_status() -> tuple[bool, str]:
    try:
        from services.asr_backend import asr_model_missing_detail, asr_model_missing_error

        missing = asr_model_missing_error(purpose="dictation")
        if missing:
            return False, asr_model_missing_detail(missing)
        return True, "Speech recognition is ready"
    except Exception as exc:  # noqa: BLE001
        return False, f"Speech recognition unavailable ({type(exc).__name__})"


def eligible_profile(profile_id: str):
    """The voice profile, if it may place outbound calls: the user's own
    verified voice, or a designed (not cloned) voice. Raises CallError."""
    from core.db import db_conn

    if not profile_id:
        raise CallError(400, "missing_profile", "Choose a voice for the call")
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, name, kind, verified_own_voice FROM voice_profiles WHERE id=?", (profile_id,)
        ).fetchone()
    if row is None:
        raise CallError(404, "profile_not_found", "That voice profile does not exist")
    if not (row["verified_own_voice"] or (row["kind"] or "clone") == "design"):
        raise CallError(
            403,
            "voice_not_allowed",
            "Outbound calls can only use your own verified voice or a designed voice. "
            "Verify this profile as your own voice (Voices → profile → Verify it's you), "
            "or choose a designed voice.",
        )
    return row


def count_eligible_profiles() -> int:
    from core.db import db_conn

    with db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM voice_profiles WHERE verified_own_voice=1 OR kind='design'"
        ).fetchone()
    return int(row[0] if row else 0)


def readiness() -> list[dict]:
    from services.telephony import gateway

    cfg = config.load()
    settings = config.load_call_settings()
    token = config.has_auth_token()
    listener = gateway.state()
    items = []

    missing = [n for n, ok in (("Account SID", cfg.account_sid), ("Auth Token", token)) if not ok]
    items.append({
        "id": "credentials",
        "ok": not missing,
        "detail": "Twilio credentials saved" if not missing else f"Add your Twilio {' and '.join(missing)} in Integrations → Twilio",
    })
    if not cfg.public_base_url:
        tunnel = (False, "Add your public tunnel URL in Integrations → Twilio")
    elif not cfg.enabled or not listener.get("running"):
        tunnel = (False, "Turn on the Twilio integration so the telephony listener runs, and start your tunnel")
    else:
        tunnel = (True, f"Listening; the tunnel must forward {cfg.public_base_url} to {listener.get('tunnel_target')}")
    items.append({"id": "tunnel", "ok": tunnel[0], "detail": tunnel[1]})
    items.append({
        "id": "number",
        "ok": bool(settings.from_number),
        "detail": f"Calls come from {mask_number(settings.from_number)}" if settings.from_number
        else "Enter your Twilio phone number in call settings",
    })
    ok, detail = llm_status()
    items.append({"id": "llm", "ok": ok, "detail": detail})
    ok, detail = asr_status()
    items.append({"id": "asr", "ok": ok, "detail": detail})
    try:
        voices = count_eligible_profiles()
    except Exception:  # noqa: BLE001
        voices = 0
    items.append({
        "id": "voice",
        "ok": voices > 0,
        "detail": f"{voices} voice(s) can place calls" if voices
        else "Verify a voice profile as your own voice, or design a voice",
    })
    return items


# ── Outbound ────────────────────────────────────────────────────────────────


def _require_ready(cfg: config.TwilioConfig, settings: config.CallSettings) -> None:
    from services.telephony import gateway

    if not cfg.account_sid or not config.has_auth_token():
        raise CallError(409, "not_configured", "Add your Twilio Account SID and Auth Token first")
    if not cfg.enabled or not cfg.public_base_url:
        raise CallError(409, "integration_disabled", "Turn on the Twilio integration first")
    if not gateway.state().get("running"):
        raise CallError(409, "listener_not_running", "The telephony listener is not running; turn the integration off and on")
    if not settings.from_number:
        raise CallError(409, "missing_from_number", "Enter your Twilio phone number in call settings")
    ok, detail = llm_status()
    if not ok:
        raise CallError(409, "llm_unavailable", detail)


def _speaker_name(settings: config.CallSettings, profile_row) -> str:
    return settings.user_name or (profile_row["name"] if profile_row is not None else "") or ""


#: Affirmative recording notices ("this call is recorded", "may be recorded",
#: "we are recording this call"). A negated one ("is not recorded") never counts.
_RECORDING_NOTICE_RE = re.compile(
    r"\b(?:is|are|will\s+be|may\s+be|might\s+be|can\s+be|being|gets?)\s+(?:being\s+)?recorded\b"
    r"|\b(?:we|i)(?:'re|\s+am|\s+are|'m)?\s+recording\b"
    r"|\brecording\s+(?:this|the)\s+call\b",
    re.IGNORECASE,
)
_NEGATED_RECORDING_RE = re.compile(
    r"(?:\b(?:not|never|no)\b|n['’]t\b)[^.!?]{0,30}\brecord", re.IGNORECASE
)


def disclosure_announces_recording(disclosure: str) -> bool:
    text = disclosure or ""
    return bool(_RECORDING_NOTICE_RE.search(text)) and not _NEGATED_RECORDING_RE.search(text)


def _recording_allowed(settings: config.CallSettings, disclosure: str) -> bool:
    """Recording needs the user's opt-in AND a disclosure that tells the other
    person it is recorded (consent laws differ; the docs explain)."""
    return settings.record_calls and disclosure_announces_recording(disclosure)


def create_outbound(
    *,
    to: str,
    brief: str,
    profile_id: str,
    engine: str = "",
    language: str = "",
    disclosure: str | None = None,
    max_minutes: int = 10,
) -> CallSession:
    cfg = config.load()
    settings = config.load_call_settings()
    try:
        number = config.normalize_phone_number(to)
        brief = config.normalize_brief(brief)
        custom = None if disclosure is None else config.normalize_disclosure(disclosure)
    except config.ConfigError as exc:
        raise CallError(400, exc.code, str(exc)) from exc
    if number == settings.from_number:
        raise CallError(400, "invalid_number", "The number to call is your own Twilio number")
    row = eligible_profile(profile_id)
    _require_ready(cfg, settings)
    if not has_agent_capacity():
        raise CallError(409, "busy", "Another call is in progress; hang it up or wait for it to finish")
    name = _speaker_name(settings, row)
    text = config.render_disclosure(settings.disclosure_template, name) if custom is None else custom
    session = CallSession(
        id=uuid.uuid4().hex,
        direction="outbound",
        remote_number=number,
        from_number=settings.from_number,
        brief=brief,
        profile_id=profile_id,
        engine=engine or "",
        language=language or "",
        disclosure=text,
        user_name=name,
        max_minutes=max(1, min(30, int(max_minutes))),
        record=_recording_allowed(settings, text),
    )
    return _register(session)


def dial(session: CallSession) -> None:
    """Ask Twilio to place the call (blocking; run it in a thread)."""
    from services.telephony import twilio as provider

    cfg = config.load()
    try:
        sid = provider.create_call(
            cfg.account_sid,
            config.auth_token(),
            to=session.remote_number,
            from_=session.from_number,
            url=f"{cfg.webhook_url}?call={session.id}",
            status_callback=f"{cfg.public_base_url}{provider.STATUS_PATH}?call={session.id}",
            time_limit_s=session.max_minutes * 60 + 30,
        )
    except provider.TwilioAPIError as exc:
        finish_unconnected(session, "failed", error=str(exc))
        raise CallError(502, "twilio_error", str(exc), twilio_code=exc.code) from exc
    with session._lock:
        if not session.provider_call_id:
            session.provider_call_id = sid
    session.set_status("initiated")


# ── Inbound (agent mode) ────────────────────────────────────────────────────


def create_inbound(call_sid: str, caller: str) -> CallSession:
    cfg = config.load()
    settings = config.load_call_settings()
    row = None
    if cfg.voice_id:
        from core.db import db_conn

        with db_conn() as conn:
            row = conn.execute("SELECT name FROM voice_profiles WHERE id=?", (cfg.voice_id,)).fetchone()
    name = _speaker_name(settings, row)
    text = config.render_disclosure(settings.disclosure_template, name)
    session = CallSession(
        id=uuid.uuid4().hex,
        direction="inbound",
        remote_number=caller if config.E164_RE.match(caller or "") else "",
        from_number="",
        brief=settings.inbound_brief or "Take a message: ask who is calling and what it is about.",
        profile_id=cfg.voice_id,
        engine=cfg.engine,
        language=cfg.language,
        disclosure=text,
        user_name=name,
        max_minutes=max(1, min(30, config.max_call_seconds() // 60 or 1)),
        record=_recording_allowed(settings, text),
        provider_call_id=call_sid,
    )
    session.set_status("ringing")
    return _register(session)


# ── Provider webhooks ───────────────────────────────────────────────────────


def claim_outbound(session_id: str, call_sid: str) -> CallSession | None:
    """The voice webhook for an outbound call: bind the CallSid (or check it)."""
    session = get_live(session_id)
    if session is None or session.direction != "outbound" or session.finalized:
        return None
    with session._lock:
        if session.provider_call_id and session.provider_call_id != call_sid:
            return None
        session.provider_call_id = call_sid
    return session


def on_provider_status(session_id: str, call_sid: str, provider_status: str) -> None:
    session = get_live(session_id)
    if session is None or session.finalized:
        return
    with session._lock:
        if session.provider_call_id and session.provider_call_id != call_sid:
            return
        session.provider_call_id = session.provider_call_id or call_sid
    status = (provider_status or "").strip().lower().replace("-", "_")
    if status in ("queued", "initiated", "ringing", "in_progress"):
        session.set_status(status)
    elif status in TERMINAL_STATUSES and not session.stream_connected:
        finish_unconnected(session, status)
    # A terminal status for a connected call: the media stream finalizes it.


def connect_stream(session_id: str, call_sid: str):
    """The media stream started: returns the agent to run, or None to refuse."""
    from services.telephony import agent as agent_mod

    session = get_live(session_id)
    if session is None or session.finalized:
        return None
    with session._lock:
        if session.stream_connected or session.provider_call_id != call_sid:
            return None
        session.stream_connected = True
    session.set_status("in_progress")
    return agent_mod.CallAgent(session, agent_mod.deps_factory())


# ── Finalization ────────────────────────────────────────────────────────────


def _transcript_text(session: CallSession) -> str:
    who = {"agent": "Assistant", "caller": "Other person"}
    return "\n".join(f"{who.get(t['speaker'], t['speaker'])}: {t['text']}" for t in session.transcript)


def _summary_messages(session: CallSession) -> list[dict]:
    name = session.user_name or "the user"
    return [
        {
            "role": "system",
            "content": (
                f"You write the after-call note for {name}. Their AI assistant made a phone call with this brief:\n"
                f'"""\n{session.brief}\n"""\n'
                "Read the transcript and reply with ONLY a JSON object: "
                '{"outcome": "booked" | "done" | "not_done" | "needs_you", "summary": "<1-3 plain sentences>"}. '
                'Use "booked" for a confirmed booking or appointment, "done" when the task was otherwise completed, '
                f'"needs_you" when {name} must follow up themselves, "not_done" otherwise. '
                "State concrete details that were agreed (times, names, prices, reference numbers)."
            ),
        },
        {"role": "user", "content": _transcript_text(session) or "(nothing was said)"},
    ]


async def _summarize(session: CallSession, deps) -> tuple[str | None, str]:
    from services.telephony.agent import OUTCOMES, _loads_object, _norm_choice

    if not session.transcript:
        return None, ""
    try:
        raw = await asyncio.wait_for(deps.llm_complete(_summary_messages(session)), timeout=_SUMMARY_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — the call still gets a record
        logger.warning("Call summary failed (%s)", type(exc).__name__)
        return None, ""
    raw = (raw or "").strip()
    if "{" not in raw:  # a model that ignored the format: its prose is the summary
        return None, raw[:2000]
    data = _loads_object(raw)
    return _norm_choice(data.get("outcome"), OUTCOMES[:-1]), str(data.get("summary") or "").strip()[:2000]


def _write_recording(session: CallSession, agent) -> None:
    recorder = getattr(agent, "recorder", None) if agent is not None else None
    if recorder is None:
        return
    from core.config import DATA_DIR

    folder = os.path.join(DATA_DIR, "calls")
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{session.id}.wav")
        recorder.write(path)
        session.recording_path = path
    except Exception:  # noqa: BLE001
        logger.warning("Could not write the call recording", exc_info=True)


def _complete(session: CallSession, status: str) -> None:
    with session._lock:
        if session.status not in TERMINAL_STATUSES:
            session.status = status
            session.timeline.append({"status": status, "t": round(time.time(), 3)})
        session.ended_at = session.ended_at or time.time()
        session.finalized = True
        closing = session._closing_events()
    store_save(session)
    session.publish({"type": "status", "status": session.status, "t": session._elapsed()})
    for event in closing:
        session.publish(event)
    _unregister(session)


async def finish(session: CallSession, stream_outcome: str, agent=None) -> None:
    """The media stream ended: summarize, persist, notify, forget."""
    from services.telephony import agent as agent_mod

    if session.finalized:
        return
    session.ended_at = time.time()
    failed = stream_outcome in ("error", "engine_unavailable", "synthesis_failed", "rejected_stream", "busy")
    if failed:
        session.error = session.error or stream_outcome
    outcome, summary = await _summarize(session, agent_mod.deps_factory())
    with session._lock:
        if failed:
            session.outcome = session.outcome or "failed"
        session.outcome = session.outcome or outcome or "not_done"
        session.summary = summary
    _write_recording(session, agent)
    _complete(session, "failed" if failed and not session.transcript else "completed")


def finish_unconnected(session: CallSession, status: str, *, error: str = "") -> None:
    if session.finalized:
        return
    with session._lock:
        session.outcome = session.outcome or ("failed" if status == "failed" else "not_done")
        session.summary = session.summary or _UNCONNECTED_SUMMARY.get(status, "")
        session.error = session.error or error
    _complete(session, status)


# ── Control (main API) ──────────────────────────────────────────────────────


def say(session: CallSession, text: str) -> None:
    text = (text or "").strip()
    if not text:
        raise CallError(400, "missing_text", "Enter what to say")
    if len(text) > config.MAX_DISCLOSURE_CHARS:
        raise CallError(400, "text_too_long", f"Keep it under {config.MAX_DISCLOSURE_CHARS} characters")
    agent = session.agent
    if agent is None or not agent.say(text):
        raise CallError(409, "not_connected", "The call is not connected yet")


def set_takeover(session: CallSession, enabled: bool) -> None:
    with session._lock:
        session.takeover = bool(enabled)
    agent = session.agent
    if agent is not None:
        agent.set_takeover(bool(enabled))
    session.publish({"type": "agent_state", "state": session.agent_state or "listening", "takeover": bool(enabled)})


def hangup(session: CallSession) -> None:
    """End the call now. A connected call ends through the agent (its media
    stream closes and Twilio hangs up); one still ringing is canceled via
    Twilio's REST API."""
    from services.telephony import twilio as provider

    agent = session.agent
    if agent is not None and agent.hangup():
        return
    if session.provider_call_id:
        cfg = config.load()
        try:
            provider.end_call(
                cfg.account_sid, config.auth_token(), session.provider_call_id,
                answered=session.status == "in_progress",
            )
        except provider.TwilioAPIError as exc:
            raise CallError(502, "twilio_error", str(exc), twilio_code=exc.code) from exc
    if not session.stream_connected:
        finish_unconnected(session, "canceled")
