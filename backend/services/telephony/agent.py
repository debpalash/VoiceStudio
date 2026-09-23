"""The call agent: holds a task-driven phone conversation in the user's voice.

A :class:`CallAgent` is a :class:`services.telephony.session.Responder`, so it
runs inside the same provider-agnostic media loop as the greeting:

    caller μ-law 8 kHz → :class:`Endpointer` (energy VAD, deterministic frame
    counts) → utterance → ASR (the capture/dictation engine) → LLM turn
    (streamed; the first sentence starts synthesis) → streaming TTS in the
    chosen voice → μ-law frames back to the caller.

Barge-in: once the caller has spoken for ``barge_frames`` while the agent is
speaking, the current turn is cancelled and a :class:`~session.Clear` drops
whatever the provider has buffered. The opening disclosure is never
interruptible — it is always heard in full.

Guardrails live in two places: the system prompt (stay on task, never invent
facts about the user, escalate, end politely, be truthful about being an AI)
and :func:`guard_sensitive`, a deterministic filter that stops the agent from
ever *speaking* a payment-card number or an unfamiliar long digit string,
whatever the model produced.

The LLM, ASR and TTS are injected through :class:`AgentDeps` so tests (and
future providers) can swap them without touching the loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections import deque
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

import numpy as np

from services.telephony.session import Clear, Mark

logger = logging.getLogger("omnivoice.telephony.agent")

OUTCOMES = ("booked", "done", "not_done", "needs_you", "failed")
ACTIONS = ("none", "end_call", "escalate")
AGENT_STATES = ("listening", "thinking", "speaking")
FRAME_SAMPLES = 160  # 20 ms at 8 kHz
#: Spoken instead of any sentence the sensitive-number guard blocks.
REFUSAL = "I'm not able to share that number over the phone."
#: Spoken before the agent gives up after repeated LLM failures.
APOLOGY = "Sorry, I'm having technical trouble, so I'll have to end the call here. Goodbye."
_MAX_LLM_FAILURES = 2
_END = object()


# ── Voice activity / endpointing ────────────────────────────────────────────


class Endpointer:
    """Energy VAD with an adaptive noise floor over 20 ms, 8 kHz PCM16 frames.

    Frame-count based (not wall-clock), so it behaves identically however the
    frames are delivered — including in tests. Emits ``"start"`` when speech
    begins, ``"barge"`` once an utterance has ``barge_frames`` of speech, and
    ``("end", pcm16)`` when ``end_frames`` of trailing silence close it (or it
    hits ``max_frames``). Utterances with too little speech are dropped as
    noise (a cough, a click).
    """

    def __init__(
        self,
        *,
        start_frames: int = 3,
        end_frames: int = 35,
        barge_frames: int = 10,
        min_speech_frames: int = 8,
        max_frames: int = 750,
        preroll_frames: int = 10,
        min_rms: float = 400.0,
        noise_ratio: float = 3.0,
    ):
        self.start_frames = start_frames
        self.end_frames = end_frames
        self.barge_frames = barge_frames
        self.min_speech_frames = min_speech_frames
        self.max_frames = max_frames
        self.min_rms = min_rms
        self.noise_ratio = noise_ratio
        self.noise = 100.0
        self.in_speech = False
        self._pre: deque[np.ndarray] = deque(maxlen=preroll_frames)
        self._run = 0
        self._buf: list[np.ndarray] = []
        self._speech = 0
        self._silence = 0

    def _is_speech(self, frame: np.ndarray) -> tuple[bool, float]:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) if frame.size else 0.0
        return rms >= max(self.min_rms, self.noise * self.noise_ratio), rms

    def push(self, frame: np.ndarray) -> list:
        speech, rms = self._is_speech(frame)
        events: list = []
        if not self.in_speech:
            self._pre.append(frame)
            if speech:
                self._run += 1
            else:
                self._run = 0
                self.noise = 0.95 * self.noise + 0.05 * rms
            if self._run >= self.start_frames:
                self.in_speech = True
                self._buf = list(self._pre)
                self._speech, self._silence = self._run, 0
                events.append("start")
            return events
        self._buf.append(frame)
        if speech:
            self._speech += 1
            self._silence = 0
        else:
            self._silence += 1
        if speech and self._speech >= self.barge_frames:
            # Repeated on every further speech frame: speech that started while
            # barge-in was not possible (the disclosure) can still barge in once
            # the agent's next reply starts playing.
            events.append("barge")
        if self._silence >= self.end_frames or len(self._buf) >= self.max_frames:
            keep = len(self._buf) - max(0, self._silence - 10)  # trim most trailing silence
            pcm = np.concatenate(self._buf[:keep]) if keep > 0 else np.zeros(0, np.int16)
            enough = self._speech >= self.min_speech_frames
            self.in_speech = False
            self._buf, self._run, self._speech, self._silence = [], 0, 0, 0
            self._pre.clear()
            if enough:
                events.append(("end", pcm.astype(np.int16)))
        return events


def upsample_to_16k(pcm8k: np.ndarray) -> np.ndarray:
    """8 kHz int16 → 16 kHz int16 for the ASR engines (they expect 16 kHz)."""
    if pcm8k.size == 0:
        return pcm8k.astype(np.int16)
    x = pcm8k.astype(np.float32) / 32768.0
    try:
        import torch
        import torchaudio

        y = torchaudio.functional.resample(torch.from_numpy(np.ascontiguousarray(x)), 8000, 16000).numpy()
    except Exception:  # noqa: BLE001 — torchaudio unavailable: linear interpolation
        n = x.size
        y = np.interp(np.arange(2 * n) / 2.0, np.arange(n), x).astype(np.float32)
    return np.clip(np.round(y * 32767.0), -32768, 32767).astype(np.int16)


# ── Sensitive-number guard ──────────────────────────────────────────────────

#: Separators a model may put between digit groups (incl. commas and Unicode dashes).
_DIGIT_RUN_RE = re.compile(r"\d(?:[\s\-.,_\u2010-\u2015\u2212]?\d){8,}")

#: Numbers the user wrote in the brief, including phone styles like "+1 (415) 555-0123".
_BRIEF_NUMBER_RE = re.compile(r"\d[\d\s().\-,_\u2010-\u2015\u2212]{7,}\d")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def guard_sensitive(sentence: str, brief: str) -> str:
    """Replace a sentence that would read out a card number, or any 9+ digit
    number the user did not put in the brief (IDs, account numbers), with
    :data:`REFUSAL`. A card number is refused even if it is in the brief."""
    allowed = {re.sub(r"\D", "", m.group()) for m in _BRIEF_NUMBER_RE.finditer(brief or "")}

    def _in_brief(digits: str) -> bool:
        # "+1 (415) 555-0123" in the brief allows "415 555 0123" spoken, and back.
        return any(a.endswith(digits) or digits.endswith(a) for a in allowed if min(len(a), len(digits)) >= 9)

    for match in _DIGIT_RUN_RE.finditer(sentence):
        digits = re.sub(r"\D", "", match.group())
        if (13 <= len(digits) <= 19 and _luhn_ok(digits)) or not _in_brief(digits):
            return REFUSAL
    return sentence


# ── Structured LLM reply ────────────────────────────────────────────────────

_THINK_OPEN_RE = re.compile(r"^\s*<(think|thinking|reasoning)>", re.IGNORECASE)
_TAG_RE = re.compile(r"(?:^|\s)(ACTION|OUTCOME)\s*:", re.IGNORECASE)
_SAY_RE = re.compile(r"^\s*SAY\s*:\s*", re.IGNORECASE)
_ACTION_VALUE_RE = re.compile(r"ACTION\s*:\s*([A-Za-z_\- ]+)", re.IGNORECASE)
_OUTCOME_VALUE_RE = re.compile(r"OUTCOME\s*:\s*([A-Za-z_\- ]+)", re.IGNORECASE)


def _norm_choice(value, choices: tuple[str, ...]) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"[\s\-]+", "_", value.strip().lower())
    for choice in choices:
        if value == choice or value.startswith(choice):
            return choice
    return None


class ReplyParser:
    """Incrementally parse the agent's reply format::

        SAY: <words to speak>
        ACTION: none | end_call | escalate
        OUTCOME: booked | done | not_done | needs_you

    ``feed`` returns say-text that is safe to speak now (so synthesis starts
    on the first sentence); ``finish`` returns the rest plus the action. Also
    tolerates a JSON object ``{"say", "action", "outcome"}``, plain text (all
    of it is speech), and a leading ``<think>`` block from reasoning models.
    """

    def __init__(self) -> None:
        self.raw = ""
        self.mode: str | None = None  # tagged | json | plain
        self._emitted = 0
        self.action = "none"
        self.outcome: str | None = None

    def _body(self) -> str | None:
        text = self.raw
        match = _THINK_OPEN_RE.match(text)
        if match:
            close = re.search(rf"</{match.group(1)}>", text, re.IGNORECASE)
            if not close:
                return None
            text = text[close.end():]
        return text.lstrip()

    def _decide_mode(self, body: str, final: bool) -> None:
        if self.mode or not body:
            return
        if body.startswith("{"):
            self.mode = "json"
        elif _SAY_RE.match(body):
            self.mode = "tagged"
        elif final or len(body) >= 4 or not "say:".startswith(body[:4].lower()):
            self.mode = "plain"

    def _say(self, body: str, final: bool) -> str:
        if self.mode == "json":
            return ""
        text = _SAY_RE.sub("", body, count=1) if self.mode == "tagged" else body
        tag = _TAG_RE.search(text)
        if tag:
            return text[: tag.start()]
        if final:
            return text
        # Hold back a trailing word that might be the start of "ACTION:".
        cut = max(text.rfind(" "), text.rfind("\n"))
        tail = text[cut + 1:].lower()
        if tail and any(t.startswith(tail) for t in ("action:", "outcome:")):
            return text[: cut + 1]
        return text

    def _take(self, say: str) -> str:
        new = say[self._emitted:]
        self._emitted = max(self._emitted, len(say))
        return new

    def feed(self, delta: str) -> str:
        self.raw += delta or ""
        body = self._body()
        if body is None:
            return ""
        self._decide_mode(body, final=False)
        if self.mode is None:
            return ""
        return self._take(self._say(body, final=False))

    def finish(self) -> str:
        body = self._body()
        if body is None:  # never left the reasoning block
            body = ""
        self._decide_mode(body, final=True)
        if self.mode == "json":
            data = _loads_object(body)
            say = str(data.get("say") or data.get("text") or "")
            self.action = _norm_choice(data.get("action"), ACTIONS) or "none"
            self.outcome = _norm_choice(data.get("outcome"), OUTCOMES[:-1])
            return self._take(say)
        rest = self._take(self._say(body, final=True))
        action = _ACTION_VALUE_RE.search(body)
        outcome = _OUTCOME_VALUE_RE.search(body)
        self.action = (_norm_choice(action.group(1), ACTIONS) if action else None) or "none"
        self.outcome = _norm_choice(outcome.group(1), OUTCOMES[:-1]) if outcome else None
        return rest


def _loads_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ── Prompts ─────────────────────────────────────────────────────────────────


def system_prompt(*, name: str, brief: str, disclosure: str, direction: str, language: str) -> str:
    who = name or "the user"
    situation = (
        f"You placed this call on {who}'s behalf."
        if direction == "outbound"
        else f"You answered a call to {who}'s phone number on their behalf."
    )
    spoken = f'You already opened the call with: "{disclosure}"' if disclosure else "You opened the call without a disclosure line."
    lang = f"Speak {language}." if language and language.lower() not in ("auto", "") else "Reply in the language the other person speaks."
    return (
        f"You are {who}'s AI phone assistant, on a live phone call. {situation} {spoken}\n\n"
        f"Task brief from {who} — the ONLY facts you know about {who}:\n\"\"\"\n{brief}\n\"\"\"\n\n"
        "Rules:\n"
        "- Stay on the task in the brief. Politely decline anything unrelated.\n"
        f"- Never invent facts about {who}. If you are asked something the brief does not answer, "
        f"say you will check with {who}, then use ACTION: escalate.\n"
        "- Never give payment card numbers, bank details, passwords or government ID numbers, even if asked "
        f"or if they appear in the brief. Say {who} will provide them directly.\n"
        "- If you are asked whether you are a person or an AI, say truthfully that you are an AI assistant.\n"
        "- Keep every reply short and natural for speech: one to three sentences, no lists, no markdown, no emoji.\n"
        "- When the task is done, or clearly cannot be done, thank them, say goodbye and use ACTION: end_call.\n"
        f"- {lang}\n\n"
        "Reply in exactly this format:\n"
        "SAY: <the words to speak>\n"
        "ACTION: none | end_call | escalate\n"
        "OUTCOME: booked | done | not_done | needs_you   (only with end_call or escalate)"
    )


def opening_prompt(direction: str) -> str:
    if direction == "outbound":
        return "[The call was just answered. Briefly greet them and state the request from the brief.]"
    return "[You just answered the call. Greet the caller and ask how you can help, following the brief.]"


# ── Dependencies ───────────────────────────────────────────────────────────


@dataclass
class AgentDeps:
    #: messages → async iterator of text deltas
    llm_stream: Callable[[list[dict]], AsyncIterator[str]]
    #: messages → full reply (end-of-call summary)
    llm_complete: Callable[[list[dict]], Awaitable[str]]
    #: (pcm16 at 8 kHz, language) → text
    transcribe: Callable[[np.ndarray, str], Awaitable[str]]
    #: (text, voice=, engine=, language=) → async iterator of 8 kHz μ-law
    render: Callable[..., AsyncIterator[bytes]]


async def _default_llm_stream(messages: list[dict]) -> AsyncIterator[str]:
    from services.llm_backend import get_active_llm_backend

    backend = get_active_llm_backend()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    done = object()

    def _run() -> None:
        try:
            for delta in backend.chat_messages_stream(messages=messages, temperature=0.4, timeout=30):
                if stop.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, delta)
            loop.call_soon_threadsafe(queue.put_nowait, done)
        except Exception as exc:  # noqa: BLE001 — handed to the awaiting turn
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    loop.run_in_executor(None, _run)
    try:
        while True:
            item = await queue.get()
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stop.set()  # a cancelled turn closes the provider stream at the next delta


async def _default_llm_complete(messages: list[dict]) -> str:
    from services.llm_backend import get_active_llm_backend

    backend = get_active_llm_backend()
    return await asyncio.to_thread(backend.chat_messages, messages=messages, temperature=0.2, timeout=45)


async def _default_transcribe(pcm8k: np.ndarray, language: str) -> str:
    from api.routers.capture_ws import _transcribe_buffer
    from services.asr_backend import capture_lease

    pcm16k = upsample_to_16k(pcm8k)
    with capture_lease():
        return await _transcribe_buffer([pcm16k.tobytes()], pcm_sr=16000)


def default_deps() -> AgentDeps:
    from services.telephony.session import render_ulaw

    return AgentDeps(
        llm_stream=_default_llm_stream,
        llm_complete=_default_llm_complete,
        transcribe=_default_transcribe,
        render=render_ulaw,
    )


#: Replaced by tests (fake LLM/ASR/TTS); production resolves real engines.
deps_factory: Callable[[], AgentDeps] = default_deps


# ── Recording (only when the user enabled it AND the disclosure says so) ────


class CallRecorder:
    """Stereo 8 kHz WAV: caller on the left, agent on the right. Agent audio
    is placed at the caller-clock position it was sent at; a barge-in clear
    drops what the caller never heard."""

    def __init__(self) -> None:
        self._caller: list[np.ndarray] = []
        self._caller_n = 0
        self._agent: list[tuple[int, np.ndarray]] = []
        self._agent_cursor = 0

    def caller(self, pcm: np.ndarray) -> None:
        self._caller.append(pcm)
        self._caller_n += pcm.size

    def agent(self, ulaw: bytes) -> None:
        from services.telephony.audio import ulaw2lin

        pcm = ulaw2lin(ulaw)
        start = max(self._agent_cursor, self._caller_n)
        self._agent.append((start, pcm))
        self._agent_cursor = start + pcm.size

    def clear(self) -> None:
        cut = self._caller_n
        self._agent = [(s, p[: max(0, cut - s)]) for s, p in self._agent if s < cut]
        self._agent_cursor = cut

    def write(self, path: str) -> None:
        import soundfile as sf

        n = max(self._caller_n, self._agent_cursor)
        stereo = np.zeros((n, 2), dtype=np.int16)
        if self._caller:
            left = np.concatenate(self._caller)
            stereo[: left.size, 0] = left
        for start, pcm in self._agent:
            stereo[start:start + pcm.size, 1] = pcm[: max(0, n - start)]
        sf.write(path, stereo, 8000, format="WAV", subtype="PCM_16")


# ── The agent ──────────────────────────────────────────────────────────────


class CallAgent:
    """One conversation. Created per media stream by ``services.telephony.calls``.

    Everything runs on the media stream's event loop; the control methods
    (:meth:`say`, :meth:`set_takeover`, :meth:`hangup`) are thread-safe and may
    be called from the main API's request handlers.
    """

    def __init__(self, call, deps: AgentDeps):
        self.call = call
        self.deps = deps
        self.max_seconds = max(60, int(call.max_minutes) * 60)
        self.state = "listening"
        self.recorder = CallRecorder() if call.record else None
        self._vad = Endpointer()
        self._history: list[dict] = []
        self._turn: asyncio.Task | None = None
        self._manual: deque[str] = deque()
        self._awaiting: str | None = None
        self._protected_mark: str | None = None
        self._seq = 0
        self._ending = False
        self._reply_after = False
        self._llm_failures = 0
        self._spoken: list[str] = []
        self._frame_rest = np.zeros(0, dtype=np.int16)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._out: asyncio.Queue | None = None
        self._asr_q: asyncio.Queue | None = None
        self._bg: list[asyncio.Task] = []

    # ── Responder surface ──

    async def speak(self) -> AsyncIterator:
        self._loop = asyncio.get_running_loop()
        self._out = asyncio.Queue()
        self._asr_q = asyncio.Queue()
        self._bg = [asyncio.create_task(self._asr_worker())]
        self.call.bind_agent(self)
        self.call.set_agent_state(self.state)
        self._start_turn(self._opening_turn)
        try:
            while True:
                item = await self._out.get()
                if item is _END:
                    return
                if self.recorder is not None:
                    if isinstance(item, bytes):
                        self.recorder.agent(item)
                    elif isinstance(item, Clear):
                        self.recorder.clear()
                yield item
        finally:
            self._ending = True
            for task in [self._turn, *self._bg]:
                if task is not None and not task.done():
                    task.cancel()
            self.call.unbind_agent(self)

    async def on_inbound_audio(self, ulaw: bytes) -> None:
        from services.telephony.audio import ulaw2lin

        pcm = np.concatenate([self._frame_rest, ulaw2lin(ulaw)])
        whole = pcm.size - pcm.size % FRAME_SAMPLES
        self._frame_rest = pcm[whole:]
        if self.recorder is not None and whole:
            self.recorder.caller(pcm[:whole])
        for i in range(0, whole, FRAME_SAMPLES):
            for event in self._vad.push(pcm[i:i + FRAME_SAMPLES]):
                if event == "barge":
                    self._barge_in()
                elif isinstance(event, tuple) and self._asr_q is not None and not self._ending:
                    self._asr_q.put_nowait(event[1])

    async def on_mark(self, name: str) -> None:
        if name == self._protected_mark:
            self._protected_mark = None
        if name == self._awaiting:
            self._awaiting = None
            if self.state == "speaking":
                self._set_state("listening")

    # ── Thread-safe controls ──

    def _call_soon(self, fn, *args) -> bool:
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        loop.call_soon_threadsafe(fn, *args)
        return True

    def say(self, text: str) -> bool:
        return self._call_soon(self._say_now, text)

    def set_takeover(self, enabled: bool) -> bool:
        return self._call_soon(self._takeover_now, enabled)

    def hangup(self) -> bool:
        return self._call_soon(self._hangup_now)

    # ── Internals ──

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.call.set_agent_state(state)

    def _drain_output(self) -> bool:
        """Drop queued audio/marks; returns True if the end was queued."""
        ended = False
        kept = []
        while self._out is not None and not self._out.empty():
            item = self._out.get_nowait()
            if item is _END:
                ended = True
                kept.append(item)
        for item in kept:
            self._out.put_nowait(item)
        return ended

    def _start_turn(self, factory) -> None:
        previous = self._turn
        if previous is not None and not previous.done():
            previous.cancel()
        self._turn = asyncio.get_running_loop().create_task(self._run_turn(previous, factory))

    async def _run_turn(self, previous, factory) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        self._spoken = []
        try:
            await factory()
            self._llm_failures = 0
        except asyncio.CancelledError:
            if self._spoken:
                self._record_agent(" ".join(self._spoken), interrupted=True)
            raise
        except Exception as exc:  # noqa: BLE001 — a failed turn must not end the call loop
            logger.warning("Call agent turn failed (%s)", type(exc).__name__)
            self._llm_failures += 1
            self.call.publish({"type": "agent_state", "state": "listening", "error": "turn_failed"})
            if self._llm_failures >= _MAX_LLM_FAILURES and not self._ending:
                self.call.set_outcome("failed")
                await self._speak_sentences([APOLOGY])
                self._record_agent(APOLOGY)
                self._end_call()
                return
            self._set_state("listening")
        if self._ending:
            return
        if self._manual:
            self._turn = asyncio.get_running_loop().create_task(self._run_turn(None, self._manual_turn))
        elif self._reply_after and not self.call.takeover:
            self._reply_after = False
            self._turn = asyncio.get_running_loop().create_task(self._run_turn(None, self._llm_turn))

    async def _speak_sentences(self, source, *, protected: bool = False) -> list[str]:
        """Speak sentences from a list or an asyncio.Queue (None ends it)."""
        spoken = self._spoken
        start = len(spoken)
        if isinstance(source, list):
            items = iter(source)

            async def _next():
                return next(items, None)
        else:
            _next = source.get
        while True:
            sentence = await _next()
            if sentence is None:
                break
            sentence = guard_sensitive(sentence.strip(), self.call.brief)
            if not sentence:
                continue
            if len(spoken) == start:
                self._set_state("speaking")
            async for chunk in self.deps.render(
                sentence, voice=self.call.profile_id, engine=self.call.engine, language=self.call.language
            ):
                await self._out.put(chunk)
            spoken.append(sentence)
            self.call.agent_partial(" ".join(spoken[start:]))
        if len(spoken) > start:
            self._seq += 1
            name = f"agent-{self._seq}"
            self._awaiting = name
            if protected:
                self._protected_mark = name
            await self._out.put(Mark(name))
        return spoken[start:]

    def _record_agent(self, text: str, **extra) -> None:
        if text:
            self.call.add_turn("agent", text, **extra)

    def _messages(self) -> list[dict]:
        prompt = system_prompt(
            name=self.call.user_name,
            brief=self.call.brief,
            disclosure=self.call.disclosure,
            direction=self.call.direction,
            language=self.call.language,
        )
        return [{"role": "system", "content": prompt}, *self._history]

    async def _opening_turn(self) -> None:
        disclosure = self.call.disclosure
        if disclosure:
            await self._speak_sentences([disclosure], protected=True)
            self._record_agent(disclosure, source="disclosure")
            self._spoken = []
        self._history.append({"role": "user", "content": opening_prompt(self.call.direction)})
        if not self.call.takeover:
            await self._llm_turn()

    async def _llm_turn(self) -> None:
        from services.sentence_chunker import SentenceChunker

        self._set_state("thinking")
        parser = ReplyParser()
        chunker = SentenceChunker(language=(self.call.language or "en"), aggressive_first_flush=True)
        sentences: asyncio.Queue = asyncio.Queue()
        messages = self._messages()

        async def _produce() -> None:
            try:
                async for delta in self.deps.llm_stream(messages):
                    for sentence in chunker.push(parser.feed(delta)):
                        await sentences.put(sentence)
                for sentence in chunker.push(parser.finish()) + chunker.flush():
                    await sentences.put(sentence)
            finally:
                await sentences.put(None)

        producer = asyncio.create_task(_produce())
        try:
            spoken = await self._speak_sentences(sentences)
            await asyncio.gather(producer)  # re-raises an LLM failure
        finally:
            if not producer.done():
                producer.cancel()
        say = " ".join(spoken)
        self._record_agent(say)
        self._history.append({"role": "assistant", "content": f"SAY: {say}\nACTION: {parser.action}"})
        if parser.action in ("end_call", "escalate"):
            self.call.set_outcome(parser.outcome or ("needs_you" if parser.action == "escalate" else "done"))
            self._end_call()
        elif not spoken:
            self._set_state("listening")

    async def _manual_turn(self) -> None:
        while self._manual and not self._ending:
            text = self._manual.popleft()
            spoken = await self._speak_sentences([text])
            self._record_agent(" ".join(spoken), source="manual")
            self._history.append({"role": "assistant", "content": f"SAY: {text}\nACTION: none"})
            self._spoken = []

    async def _asr_worker(self) -> None:
        while True:
            pcm = await self._asr_q.get()
            try:
                text = (await self.deps.transcribe(pcm, self.call.language) or "").strip()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — skip the utterance, keep listening
                logger.warning("Call transcription failed (%s)", type(exc).__name__)
                continue
            if text and not self._ending:
                self._on_caller_text(text)

    def _on_caller_text(self, text: str) -> None:
        self.call.add_turn("caller", text)
        self._history.append({"role": "user", "content": text})
        if self.call.takeover:
            return
        turn = self._turn
        if turn is not None and not turn.done() and self.state == "speaking":
            # Still speaking (the disclosure, or the caller did not talk long
            # enough to barge in): answer once this turn has been spoken.
            self._reply_after = True
            return
        self._start_turn(self._llm_turn)

    def _barge_in(self) -> None:
        if self._ending or self._protected_mark is not None or self.state != "speaking":
            return
        turn = self._turn
        if turn is not None and not turn.done():
            turn.cancel()
        else:
            self.call.mark_last_agent_interrupted()
        self._drain_output()
        self._out.put_nowait(Clear())
        self._awaiting = None
        self._set_state("listening")

    def _say_now(self, text: str) -> None:
        if self._ending or not text:
            return
        self._manual.append(text)
        turn = self._turn
        if turn is None or turn.done() or self.state == "thinking":
            self._start_turn(self._manual_turn)

    def _takeover_now(self, enabled: bool) -> None:
        if enabled and self.state == "thinking" and self._turn is not None and not self._turn.done():
            self._turn.cancel()
            self._set_state("listening")

    def _hangup_now(self) -> None:
        if self._ending:
            return
        turn = self._turn
        if turn is not None and not turn.done():
            turn.cancel()
        self._drain_output()
        self._out.put_nowait(Clear())
        self._end_call()

    def _end_call(self) -> None:
        """Finish after everything queued has played (the media loop sends its
        end mark once ``speak`` returns, and closes when the caller heard it)."""
        if not self._ending:
            self._ending = True
            self._out.put_nowait(_END)
