# Implementation Spec — TASK #32: Opt-in Phone Calls (Agentic Voice)

> **Historical design reference.** Paths, status and instructions below describe
> the original implementation snapshot. For current behavior see the
> [calls guide](../../calls.md); follow current repository rules for new changes.

## TL;DR

Ship the §R1 **v3 telephony** rung of the agentic-voice workflow: outbound phone calls placed by an embedded [pipecat](https://github.com/pipecat-ai/pipecat) (BSD-2) pipeline through a **user-supplied carrier** (Telnyx/Twilio), driven by the agent's own LLM, speaking through a **consent-locked** VoiceStudio clone. The whole feature is **deferred-by-design and opt-in by construction** (no carrier credentials → the surface does not exist), and lands with the §R1 guardrails from `docs/competitive-analysis.md:994-1002` in the **same PR series**: (1) non-removable disclosure preamble, (2) consent-locked profile requirement, (3) AudioSeal always-on with no toggle, (4) destination allowlist + daily call cap + no bulk-dial API, (5) local immutable call log with a two-party-consent recording warning, and (6) honest jurisdiction notice in docs. (Note: `competitive-analysis.md:994-1002` actually enumerates **six** guardrails — the spec's "five guardrails in code" + the docs notice as #6.) This is a major new surface; the bulk of the work is the guardrail scaffolding (DB, config, gating) which is testable without ever placing a real call, plus an honest jurisdiction-notice docs page. The actual carrier leg is a thin, lazily-imported adapter behind those gates.

Grounding: this milestone is already explicitly named and scoped in the codebase — `docs/agentic-voice.md:12-15` defers it, and `docs/competitive-analysis.md:950-1015` (§R1) specifies the runtime choice, telephony honesty (`:962-970`), binding regulatory constraints (`:981-992`), the concrete guardrails (`:994-1002`), and the v1→v2→v3 scope ladder (`:1004-1015`). v1 (provider recipe, shipped as `docs/agentic-voice.md` + `tests/test_agentic_provider_contract.py`) and the consent lock (`voice_profiles.verified_own_voice`, migration `0003`) already exist; this task is v3 (`competitive-analysis.md:1012-1015`).

**Why this is allowed to exist despite the local-first guarantee:** VoiceStudio's core promise is "no *required* cloud calls, accounts, or API keys" (CLAUDE.md). Telephony is a **non-default, opt-in-by-construction** surface: the 100% of installs that never touch it see byte-for-byte identical behaviour (no new default dep, `state==DISABLED`, every `/telephony/*` route absent). The one unavoidable non-local hop (the PSTN) is the **user's own** paid carrier account, never a VoiceStudio-owned account or telemetry endpoint — exactly the honesty already accepted in `docs/agentic-voice.md:12-15` and `competitive-analysis.md:962-965`. This is the same "opt-in escape hatch for power users, invisible to everyone else" shape the CLAUDE.md stack guidance blesses for phone-calls-class features. See the **Constraints** section for the rule-by-rule mapping.

## Problem

Users running the agentic-voice workflow (VoiceStudio as a local TTS/STT provider for their own pipecat/LiveKit agent — see `docs/agentic-voice.md`) want VoiceStudio to also place a real **outbound phone call** in their cloned voice (the "my own voice, my own errand" case: call a restaurant, a clinic's IVR, etc.). Today that is impossible inside VoiceStudio and is explicitly deferred:

- `docs/agentic-voice.md:12-15` — "Outbound phone calls are a separate, deferred milestone (they need a paid carrier — there is no fully-local path to the PSTN) and ship only behind explicit consent guardrails. See the roadmap in `docs/competitive-analysis.md` (§R1)."
- There is **no fully-local path to the PSTN** (`competitive-analysis.md:962-964`). Reaching a phone number always requires a paid carrier / SIP trunk. That directly tensions the project's **local-first guarantee** (CLAUDE.md: "No required cloud calls, accounts, or API keys").
- It is also **heavily regulated** (`competitive-analysis.md:981-992`): FCC 24-17 (cloned voices are "artificial" under TCPA, $500–1,500/call private right of action), Texas SB 140 (AI disclosure in first 30 s), Tennessee ELVIS Act (liability extends to **tool providers** — i.e. us), and **EU AI Act Article 50 applies from 2026-08-02** (must disclose AI + mark generative audio machine-readably; the OSS exemption does **not** cover Art. 50 — `competitive-analysis.md:986-989`).

So the problem is: how do we offer this capability **without** (a) breaking the local-first promise for the 95% who never use it, (b) creating robocall infrastructure, or (c) exposing VoiceStudio (and its users) to tool-provider liability.

## Goal / Non-goals

### Goals
1. A **single outbound call** placed via embedded pipecat + a user-supplied carrier, from a consent-locked voice profile, fully gated behind opt-in.
2. **All §R1 guardrails enforced in code**, not docs:
   - Non-removable spoken disclosure preamble at call start.
   - Call refused unless the chosen profile is `verified_own_voice = 1` (`core/db.py:52`).
   - AudioSeal watermark always-on for agentic output (no toggle path for this call type).
   - Destination allowlist + per-day call cap; **no bulk/batch dial API surface ever**.
   - Local immutable call-log row per attempt; two-party-consent warning required before any audio recording.
3. **Opt-in by construction**: with no carrier credentials configured, `/telephony/*` returns `404`/`501` and the Settings panel shows only an explainer — feature literally does not exist by default. Satisfies the CLAUDE.md "default features work on every platform" rule by being a non-default, env/Settings-gated feature with identical behavior on macOS/Windows/Linux.
4. **Honest jurisdiction docs** (`docs/telephony.md`) created **in the same PR** (docs-sync hard rule).
5. Backward-compatible DB migration (`0006_telephony.py`) with a tested upgrade path.

### Non-goals
- **Inbound** calls / receiving a number. (Not in §R1; out of scope.)
- **Bulk/campaign/auto-dialer** anything. Architecturally excluded — there is no list-dial endpoint, by design (guardrail 4).
- LiveKit SIP at scale (`competitive-analysis.md:957` — only if self-hosted SIP becomes a priority; not now).
- Live Discord voice-channel bot (that is §R1 **v2**, `competitive-analysis.md:1008-1011`, a separate task).
- Shipping carrier SDKs as hard runtime deps. They stay **optional/lazy-imported extras** (no entry under `[project] dependencies` in `pyproject.toml`).
- Bundling carrier credentials or any VoiceStudio-owned account. The user always brings their own Telnyx/Twilio.
- **Concurrent / parallel calls.** First slice places **one call at a time per backend process**; a second `POST /telephony/calls` while one is `placing`/`ringing`/`in_progress` is refused `reason:"call_in_progress"` (see §Concurrency edge cases). This is also a robocall-misuse mitigation, not just an engineering simplification.
- **Retry / redial on failure.** A failed call writes a terminal `failed` row and stops. No automatic redial (a redial loop would be a campaign vector). The user re-initiates manually, which consumes another daily-cap slot.
- **No new default runtime dependency, and no `phonenumbers` dep** unless `uv tree` already resolves it (it does **not** today — verified). The E.164 normalizer is pure-Python regex (see §Destination validation + §Constraints / CodeQL).

## Design

### Runtime choice (settled in §R1, `competitive-analysis.md:956`)
- **pipecat** (BSD-2) embedded as a Python library inside the existing FastAPI process. No extra server. It provides local VAD (Silero), turn-taking, barge-in, and OpenAI-compatible TTS/STT services that already point at our own `/v1/audio/*` (`docs/agentic-voice.md:37-53`, `examples/agentic/pipecat_minimal.py`). pipecat's `OpenAITTSService` takes a `base_url` and defaults to 24 kHz, matching VoiceStudio's default speech output (`competitive-analysis.md:956`, `docs/agentic-voice.md:24,46`).
- The carrier leg uses pipecat's **Telnyx or Twilio serializer** over a media WebSocket (`competitive-analysis.md:1013-1015`). Telnyx preferred (cheaper, ~$0.005–0.007/min vs Twilio ~$0.014/min — `competitive-analysis.md:963`).
- All carrier/pipecat imports are **lazy** (mirrors `backend/mcp_server.py:31-48` `_ensure_mcp()` and `backend/services/watermark.py:43-53` `_check_available()`), so the feature costs zero import time and zero deps unless actually used. **Cross-platform note:** pipecat + carrier WS are pure-Python over the network — no OS-specific code path — so the *enabled* behaviour is identical on macOS/Windows/Linux (see §Constraints).

### "Opt-in by construction" gate — the master switch is a **5-state machine**, not a boolean
A new `backend/services/telephony/config.py` resolves carrier config from the **encrypted settings store** (carrier secrets) + **prefs** (non-secret toggles). Use the existing `core.prefs.resolve(key, *, env=None, default=None)` (`backend/core/prefs.py:75-82`), which reads **env var first, then `prefs.json`, then default** in a single call — so the env/prefs precedence is a single `resolve()` invocation, not two branches. **Note the env precedence gotcha (`prefs.py:78-81`):** `resolve` only returns the env value when `os.environ.get(env)` is **truthy** (`if v: return v` at `:80-81`); setting `OMNIVOICE_TELEPHONY=0`/`false`/`""` does **not** force-disable — an empty/`"0"` string is falsy-as-string but `resolve` returns the *prefs* value in those cases (it only short-circuits on a truthy env string). The config resolver must therefore coerce the resolved value with an explicit `_truthy()` helper (`str(v).lower() in {"1","true","yes","on"}`) so `"0"`/`"false"`/`""` from either env **or** prefs all read as disabled. Document that `OMNIVOICE_TELEPHONY` is an *enable* override only; to hard-disable, leave it unset and set the pref false.

The resolver computes a single enum `TelephonyState`, exposed by `config.state()`:

| State | Condition | Effect |
|---|---|---|
| `DISABLED` | `telephony.enabled` resolves falsy (default) | All endpoints except `GET /telephony/status` → 404. `/status` → `{enabled:false, available:<dep-bool>, has_credentials:<bool>}`. **This is the default for 100% of installs.** |
| `DEPS_MISSING` | enabled **and** `_telephony_available()` is False (pipecat/carrier extra not importable) | `/status` → `{enabled:true, available:false}`. Mutating + call endpoints → 501 with the `uv sync --extra telephony` hint. Allowlist read/write **still works** (it's just DB rows; lets the user prepare before installing). |
| `NO_CREDENTIALS` | enabled, deps present, but no complete carrier credential set in `settings` | `/status` → `{enabled:true, available:true, has_credentials:false, carrier:null}`. `/preflight` and `/calls` → 4xx `reason:"no_credentials"`. Allowlist + settings endpoints work. |
| `PARTIAL_CREDENTIALS` | enabled, deps present, **some but not all** required secrets for the selected carrier present | Treated as `NO_CREDENTIALS` for call purposes (`reason:"no_credentials"`) but `/status` surfaces `credential_fields_missing:[...]` so the UI can show exactly which field is blank. Never log which field. (A half-saved Twilio config — SID set, auth-token blank — is a common real failure; do not silently fall through to a "ready" state.) |
| `READY` | enabled, deps present, complete credentials, **and** at least the carrier client constructs without raising | `/status` → `{enabled:true, available:true, has_credentials:true, carrier:"telnyx"\|"twilio"}`. Full surface live. |

`_telephony_available()` mirrors `watermark._check_available()` (`watermark.py:43-53`): memoize the `try: import pipecat … except ImportError` result in a module global, log once at INFO, never raise. Because pipecat pulls heavy transitive deps, the import probe must be **import-only** (no model load) so `/status` stays fast.

If state is not `READY`, the router is mounted but **inert per the table above**. This is the same "mounted-but-inert-unless-configured" pattern as `BearerKeyMiddleware` being a no-op when `OMNIVOICE_API_KEY` is unset (`backend/main.py:614-636`, the `if not key: return await self.app(...)` early-out at `:634-636`) and the MCP server's opt-out at `backend/main.py:780`. The gate is re-evaluated **per request** (cheap: a `resolve()` + memoized import bool + one indexed `settings` read), so a user who enables telephony in Settings and saves credentials gets a live surface without a backend restart — except `_telephony_available()` is memoized for the process lifetime, so installing the extra at runtime **does** require a restart (document this; surface it in the 501 body: "restart the backend after installing the extra").

#### `config.py` exact module surface (signatures the router + tests bind to)
```python
# backend/services/telephony/config.py
from __future__ import annotations
import enum
from typing import Optional, TypedDict

class TelephonyState(str, enum.Enum):       # str-mixin → JSON-serializes as its value
    DISABLED            = "DISABLED"
    DEPS_MISSING        = "DEPS_MISSING"
    NO_CREDENTIALS      = "NO_CREDENTIALS"
    PARTIAL_CREDENTIALS = "PARTIAL_CREDENTIALS"
    READY               = "READY"

# Per-carrier required secret field names (stored Fernet-encrypted in `settings`).
# These keys are the carrier-secret names passed to set/get/clear_carrier_secret.
CARRIER_FIELDS: dict[str, list[str]] = {
    "telnyx": ["telnyx_api_key", "telnyx_connection_id", "telnyx_from_number"],
    "twilio": ["twilio_account_sid", "twilio_auth_token", "twilio_from_number"],
}

class StatusDict(TypedDict):
    enabled: bool
    available: bool                  # _telephony_available() (pipecat extra importable)
    carrier: Optional[str]           # "telnyx" | "twilio" | None
    daily_cap: int                   # resolved int (>=0); default 5
    calls_today: int                 # carrier-reaching attempts in host-local day
    has_credentials: bool            # complete set for selected carrier
    credential_fields_missing: list[str]   # subset of CARRIER_FIELDS[carrier]; [] when complete
    watermark_available: bool        # watermark._check_available()
    record_calls: bool               # the telephony.record_calls pref default
    call_in_progress: bool           # a placing/ringing/in_progress row exists
    state: str                       # TelephonyState value

DEFAULT_DAILY_CAP = 5

def _truthy(v) -> bool: ...                  # str(v).lower() in {"1","true","yes","on"}
def _telephony_available() -> bool: ...      # memoized import probe, never raises
def state() -> TelephonyState: ...           # the per-request gate
def selected_carrier() -> Optional[str]: ... # resolve("telephony.carrier"); None if unset/invalid
def resolved_daily_cap() -> int: ...         # int(resolve("telephony.daily_cap", default=5)); clamps <0 → 0
def credential_fields_missing(carrier: str) -> list[str]: ...  # blank/absent fields for carrier
def build_status() -> StatusDict: ...        # assembled GET /telephony/status body
```
`daily_cap`/`record_calls` are stored via `prefs.set_(...)` as JSON (so an int stays an int); `resolved_daily_cap()` must still tolerate a string-typed value (env override) — `int(str(v))` inside a `try/except ValueError → DEFAULT_DAILY_CAP`. `calls_today` and `call_in_progress` are computed by the call-log queries in §Daily-cap / §Concurrency (single indexed `SELECT` each, both using `idx_telephony_calls_status` + `idx_telephony_calls_created`).

### Call lifecycle (single call) — full state machine
`telephony_calls.status` transitions through exactly these states. **Every** terminal path writes a terminal status; there is no path that leaves a row stuck non-terminal after the request returns.

```
                 ┌─ refused (preflight/guardrail fail) ─────────────► [terminal]
 (request) ──────┤
                 └─ placing ─┬─ ringing ─┬─ in_progress ─┬─ completed ─► [terminal]
                             │           │               └─ failed ────► [terminal]
                             │           └─ failed (no answer/busy) ───► [terminal]
                             └─ failed (carrier connect error) ────────► [terminal]
```

1. **Preflight** (`POST /telephony/preflight`): validate destination is on the allowlist, daily cap not exceeded, profile is `verified_own_voice`, carrier reachable, **no call already in progress**. Returns a `call_plan` (no call placed, **no row written** — preflight is read-only by design so a UI can poll it without polluting the immutable log). This is where every guardrail is checked *before* a dollar is spent or a number is dialed. Preflight failures return the refusal contract with `ok:false` but do **not** persist a row (only an actual `POST /calls` persists a refused row, so the log = "attempts to actually place").
2. **Place** (`POST /telephony/calls`): re-validate **all** guardrails server-side (never trust a prior preflight — the allowlist/cap/consent could have changed in the gap; preflight is advisory). If any guardrail fails here, write an immutable `telephony_calls` row with `status='refused'` + `refused_reason=...` and return 4xx. Otherwise create the row (`status='placing'`), build the pipecat pipeline (LLM + our TTS/STT + carrier serializer), connect the media WS. **First audio frame is always the disclosure preamble** synthesized through `/v1/audio/speech` (handler `create_speech(req: SpeechRequest)` at `backend/api/routers/openai_compat.py:251-252`, router prefix `/v1/audio` at `:37`) with AudioSeal forced on — generated server-side, not supplied by the agent, so it cannot be removed (guardrail 1+3). Emits progress on the event bus via `core.event_bus.emit("telephony", {...})` (`backend/core/event_bus.py:44`), reused by the Settings UI's existing WS listener (`api/routers/events.py`, `/ws/events` at `:25-26` — note it is a **WebSocket**, not SSE).
3. **In-call**: pipecat runs the conversation loop; every synthesized utterance flows through the watermark embed (reusing `backend/services/watermark.embed_watermark`, but with the agentic-output override that ignores the user's `watermark.invisible` pref — see §Watermark below).
4. **End**: update the call-log row terminal status + `duration_s` + `ended_at`; never delete it (immutable; guardrail 5).
5. **Recording** (optional, default OFF): if the user opted to record, the **two-party-consent warning is added to the disclosure preamble text** and the recording flag is stamped on the call-log row.

### Edge cases & failure paths — every "and then…" spelled out

These are acceptance-level behaviors, each with a defined, tested outcome. No path is hand-waved.

**A. Gating / configuration edge cases**
- **Enabled pref set but credentials wiped after the fact** → state drops `READY`→`NO_CREDENTIALS`; in-flight call (if any) is allowed to finish (it already holds a constructed carrier client); new `/calls` → `reason:"no_credentials"`.
- **`OMNIVOICE_TELEPHONY=0` env set** → because of the `resolve` truthiness gotcha above, this does **not** force-disable on its own; the `_truthy()` coercion makes `"0"` read disabled. Test asserts `OMNIVOICE_TELEPHONY=0` ⇒ `state()==DISABLED` regardless of prefs.
- **Extra installed but `audioseal` missing** → call can still be placed; `embed_watermark(force=True)` is a graceful no-op (`watermark.py:112` `_check_available()` still gates even under `force`); `telephony_calls.watermarked` is written `0` (honest provenance — see Watermark §). `/status` and the call-log UI surface `watermark_available:false` so the user knows the machine-readable mark is absent and only the spoken disclosure binds.
- **`settings` table read fails mid-request** (SQLite locked / corrupt) → credential helper returns `None` (mirrors `get_hf_token`'s `except Exception: return None` at `settings_store.py:69-73`), so state degrades to `NO_CREDENTIALS` and the call is refused `reason:"no_credentials"` rather than 500. Never expose the SQLite error text.
- **Fernet `InvalidToken` on a carrier secret** (omnivoice_data/ copied across machines) → same fallback as `get_hf_token` (`:62-68`): treat as absent, state `NO_CREDENTIALS`, log a warning that does **not** include ciphertext.

**B. Destination / E.164 validation edge cases** (`POST /telephony/allowlist` and `/calls`)
- **Non-E.164 input** (missing `+`, letters, extension `;ext=`, alphameric SIP URI) → 422 `reason:"invalid_destination"`; row **not** added to allowlist; for `/calls`, a refused row is written with `refused_reason='invalid_destination'` (the attempt is logged) — except when the input fails *parse* so hard there's no normalizable `destination` to store, in which case return 422 without a row (a row requires a non-null `destination NOT NULL`).
- **Normalization** must be deterministic: store the canonical `+<countrycode><number>` form (strip spaces, dashes, parens, leading `00`/`011` international prefixes → `+`). The allowlist match in `/preflight` + `/calls` compares the **normalized** destination against the **normalized** stored `e164` — so `+1 (555) 010-0000`, `+15550100000`, and `0015550100000` all match the same allowlist row. Do not store both raw and normalized; normalize on the way in (allowlist add) and on the way to the matcher (call). Use a small pure-Python normalizer (no `phonenumbers` dep — verified absent in `uv tree`; a strict regex normalizer is the path, documented as "format-checked, not carrier-validated").
- **CodeQL py/polynomial-redos (binding):** the destination string is **user input reachable by a regex** (`POST /telephony/allowlist`, `POST /telephony/calls` bodies), so the normalizer regex must be ReDoS-clean exactly like the existing guarded patterns in `backend/services/audiobook.py:36,40`, `backend/services/ssml_lite.py:59`, and `backend/services/pronunciation.py:21-22`. Concretely: (a) prefer a **single non-backtracking pass** — first strip non-digits/separators with `re.sub(r"[ \t().\-]", "", s)` (literal character class, no quantifier overlap), normalize a leading `00`/`011` to `+`, then validate the cleaned form against an **anchored, fixed-shape** pattern with **no overlapping `\d*`/`.+` runs** and **no nested quantifiers**: `^\+\d{8,15}$` (E.164 caps the national+country number at 15 digits and floors at a sane 8 to reject 1–2 digit junk, so the bounds are literals, not an unbounded `+`). Do not write a pattern like `(\+?\d+)+` or `[\d\s\-]*\d+` where two quantified subexpressions can match the same characters — that is the polynomial-backtracking shape CodeQL flags. Mirror the in-code stripping the existing parsers use (they strip first, then match a literal-anchored remainder). Add a one-line `# CodeQL py/polynomial-redos: …` justification comment next to the compiled pattern, matching the house style in those files. A test in `test_telephony_guardrails.py` feeds a pathological adversarial input (long run of `+`/digits/separators) and asserts the normalizer returns in bounded time and yields `invalid_destination`, not a hang.
- **Duplicate allowlist add** → idempotent: `INSERT OR REPLACE` keyed on the normalized `e164`; updating only the `label`. Return 200 (not 201) on a row that already existed, 201 on a fresh insert, so the UI can tell. (The add handler `SELECT 1 FROM telephony_allowlist WHERE e164=?` before the `INSERT OR REPLACE` to decide the status code.)
- **Allowlist delete of a number not present** → 404 (so the UI doesn't silently think it deleted something). Deleting an allowlisted number that has historical `telephony_calls` rows **does not** touch those rows (call log is immutable and not FK-linked to the allowlist).
- **Emergency / special numbers** (911, 112, 999, short codes, premium-rate 1-900) → **hard-refused even if a user adds them to the allowlist.** A static denylist in `services/telephony/disclosure.py`-adjacent `_BLOCKED_PREFIXES` blocks emergency + premium-rate destinations at preflight *and* place with `reason:"blocked_destination"` and a refused row. This is a safety guardrail the allowlist cannot override. The denylist match is a **literal prefix set membership test on the already-normalized digits** (`any(digits.startswith(p) for p in _BLOCKED_PREFIXES)` over a `frozenset[str]`, not a regex) — so it adds **no** new user-input-reachable regex surface. Documented in `docs/telephony.md`.

**C. Daily-cap edge cases** (guardrail 4)
- **Definition of "today"**: the cap counts `telephony_calls` rows whose `created_at` falls in the **current calendar day in the backend host's local timezone** (matches how a user reasons about "calls today"). Document the timezone explicitly in `docs/telephony.md`; do **not** use UTC silently (a user in UTC-8 placing a call at 5pm would otherwise see it counted against "tomorrow"). Compute the day boundary once per request from `time.localtime()` → epoch of local midnight via `time.mktime((y,m,d,0,0,0,0,0,-1))`, then `SELECT COUNT(*) FROM telephony_calls WHERE created_at >= ? AND status IN ('placing','ringing','in_progress','completed','failed')`. **Cross-platform note:** `time.localtime()` resolves the host TZ identically on macOS/Windows/Linux (no OS-specific branch), so the cap boundary is the same default behaviour everywhere — satisfying the "default behaviour identical across platforms" rule for the part of this opt-in feature that *is* on whenever the feature is enabled.
- **Which rows count toward the cap**: rows with `status IN ('placing','ringing','in_progress','completed','failed')` — i.e. **any attempt that reached the carrier**. `status='refused'` rows do **not** count (a guardrail refusal shouldn't burn the user's daily budget — otherwise a misconfigured allowlist could lock them out). This means `cap_exceeded` is itself only ever returned **after** the allowlist/consent/dest checks pass, so the refusal-reason precedence is fixed (see §Refusal precedence).
- **Cap = 0** → telephony is enabled but the user has throttled to zero; every `/calls` → `reason:"cap_exceeded"`. Valid configuration (a soft kill-switch); not an error.
- **Race: two `/calls` arrive simultaneously at cap-1** → because we serialize calls (one-in-flight, §Concurrency) the count is read inside the same critical section that flips a process-level "call active" guard, so the second request sees either `call_in_progress` or the incremented count. Test asserts no path lets `cap+1` real attempts through.

**D. Concurrency / single-call-at-a-time**
- A process-level `asyncio.Lock` (`backend/services/telephony/pipeline.py` module global `_call_lock = asyncio.Lock()`) guards call placement; the lock is acquired **before** the in-flight DB check and held until the call reaches a terminal status. A second `POST /calls` while one is active → 409 `reason:"call_in_progress"` (no row written; it never reached the carrier). The handler uses `if _call_lock.locked(): return 409` (non-blocking probe) rather than awaiting the lock, so the second request fails fast instead of queueing (queueing would be a campaign vector). The lock is **always released** in a `finally` even if the pipeline raises (otherwise the feature self-deadlocks after one crash). `asyncio.Lock` is platform-neutral (pure stdlib), so the single-call invariant holds identically on all three OSes.
- **Backend restart with a non-terminal row** (process killed mid-call → row stuck at `placing`/`in_progress`) → on `telephony` router import / first `/status` call, a **reconciliation sweep** runs `UPDATE telephony_calls SET status='failed', refused_reason='interrupted', ended_at=:now WHERE status IN ('placing','ringing','in_progress')` (it cannot have survived a restart). This keeps the immutable log honest and clears any stale "in progress" that would block new calls. The sweep is an UPDATE (terminal status), never a DELETE — preserving immutability.

**E. Mid-call failure paths** (each ends in a terminal `failed` row + an event-bus emit so the UI stops showing "in progress")
- **Carrier WS never connects** (bad creds at carrier, network) → row `placing`→`failed`, `refused_reason='carrier_connect_failed'`, generic message to the user (no carrier error body, which can leak account hints).
- **Callee never answers / busy / rejected** → the carrier serializer reports the SIP/call status; row → `failed` with `refused_reason='no_answer'`/`'busy'`/`'rejected'`. `duration_s` may be 0.
- **TTS engine errors on the disclosure preamble** (the very first frame) → the call is **aborted before any audio is sent** (we never connect a call we can't disclose on); row → `failed`, `refused_reason='disclosure_synthesis_failed'`. This is a hard requirement: a call must never reach a human without the disclosure having been synthesized. If `create_speech` returns empty/zero-length audio for the preamble, treat it the same as a synthesis failure.
- **TTS engine errors mid-conversation** (after disclosure) → pipecat surfaces the frame error; the adapter ends the call gracefully (`completed` if past disclosure with non-trivial duration, else `failed`), logs the engine error sans secrets, emits a terminal event. The disclosure already played, so legal posture is intact.
- **Watermark embed throws on a mid-call frame** → `embed_watermark` already swallows and passes through the original waveform (`watermark.py:140-142`). Under `force=True` for telephony, a thrown embed means that **frame** went out unwatermarked. We do **not** abort the call for a single failed-embed frame (audibly dropping mid-sentence is worse), but we flip a per-call `watermark_degraded` flag and, on call end, if **any** frame failed to embed, write `watermarked=0` (honest) and emit a warning event. Document this in `docs/telephony.md`.
- **Event loop / WS listener gone** → `event_bus.emit` is fire-and-forget and drops on `QueueFull` or no-loop (`event_bus.py:62-64,72-80`); a missing UI listener must **never** stall or fail the call. The call's terminal status is the source of truth (DB), not the event.
- **User closes the Settings tab mid-call** → call continues to completion server-side (it's not bound to the WS listener); the immutable row records the true outcome; on reopen the UI refetches the log.
- **Carrier mid-call drop / one-leg hangup** → terminal `completed` with whatever `duration_s` elapsed, unless before disclosure finished (then `failed`).

**F. Recording-flag edge cases** (guardrail 5)
- Recording defaults OFF (`telephony.record_calls` pref default `false`, **and** per-call `record:false` default in the `/calls` body). Both must be considered: a per-call `record:true` is only honored if the user has acknowledged the two-party-consent warning **for this call** (the request must carry an explicit `record:true`; the UI gates that behind the warning checkbox). The recording notice is appended to the spoken disclosure (so the callee hears it) **and** `recorded=1` is stamped on the row. There is no "record without notice" path.
- **`record:true` but profile/cap/allowlist refusal** → no recording happens (call never placed); the refused row has `recorded=0`.
- **Where recordings go**: out of scope to implement a recording sink in the first slice — `record:true` only (a) appends the consent notice to the disclosure and (b) sets the carrier's recording flag via the serializer if supported; VoiceStudio does not store the audio locally in slice 1 (avoids holding call audio = privacy + storage surface). Document this honestly: "record" means "ask the carrier to record on its side, and disclose it," not "VoiceStudio saves a WAV." If the carrier doesn't support it, `record:true` still adds the spoken notice and stamps the row, and `docs/telephony.md` states the carrier dependency.

**G. Empty / boundary inputs**
- **Empty `agent_prompt`** → allowed; pipecat runs with a default minimal system prompt. The disclosure still plays first regardless of prompt.
- **`agent_prompt` attempting to instruct "do not disclose you are AI"** → irrelevant: the disclosure is the first synthesized frame, generated server-side from `disclosure_text()`, before any LLM turn. The prompt cannot suppress or reorder it (tested in `test_telephony_disclosure.py`). Optionally, scan-and-warn is **not** added (we don't moderate prompts), but the architectural guarantee holds.
- **Empty allowlist + `/calls`** → `reason:"not_allowlisted"` (an empty allowlist means *nothing* is dialable; a sane safe default, not an error).
- **Profile deleted between preflight and place** → `/calls` re-reads the profile; missing profile → 404 `reason:"profile_not_found"` (distinct from `not_verified`); **no row written** (FK is declarative; SQLite `PRAGMA foreign_keys=ON` at `db.py:17` would reject an insert referencing a now-deleted `profile_id`, so we validate profile existence *before* the insert and return 404 without a row when the profile is gone). Tested.

### Refusal-reason precedence (deterministic, single-valued `reason`)
When multiple guardrails would fail, the response returns **exactly one** `reason`, chosen by this fixed precedence so tests and the UI are deterministic:
1. `no_credentials` (can't do anything)
2. `call_in_progress` (single-call lock)
3. `invalid_destination` (can't even normalize)
4. `blocked_destination` (emergency/premium — safety, before user-config checks)
5. `not_allowlisted`
6. `profile_not_found`
7. `not_verified`
8. `cap_exceeded` (checked **last**, since refused attempts don't consume the cap)

The same precedence is shared by `/preflight` and `/calls` (preflight returns it as advisory; place re-derives it and persists a refused row for reasons 4–8, where a normalizable `destination` exists).

### Watermark: always-on for agentic output

**Current signature** (`backend/services/watermark.py:96-100`):
```python
@torch.no_grad()
def embed_watermark(
    waveform: torch.Tensor,
    sample_rate: int,
    message: Optional[list[int]] = None,
) -> torch.Tensor:
```
It self-gates at `:112` (`if not is_enabled() or not _check_available(): return waveform`), where `is_enabled()` (`:78-80`) reads `resolve("watermark.invisible", default=True) is not False`. For telephony output we must bypass the user toggle (guardrail 3 — `competitive-analysis.md:998`).

**New signature** (parameter-additive, **keyword-only** so positional callers can never accidentally pass it):
```python
@torch.no_grad()
def embed_watermark(
    waveform: torch.Tensor,
    sample_rate: int,
    message: Optional[list[int]] = None,
    *,
    force: bool = False,
) -> torch.Tensor:
    # gate at :112 becomes:
    if (not force and not is_enabled()) or not _check_available():
        return waveform
    # ...existing try/except passthrough at :115-142 unchanged...
```
`force=True` skips **only** the `is_enabled()` check; it still respects `_check_available()` (graceful if AudioSeal not installed) **and** still falls through the existing `try/except` at `:115-142` that returns the original waveform on any embed error. So `force=True` has three per-frame outcomes, all handled: (a) embedded; (b) AudioSeal not installed → original waveform, unmarked, `watermark_available:false`; (c) embed raised → original waveform, unmarked, `watermark_degraded` flagged. Outcomes (b)/(c) drive `telephony_calls.watermarked=0`.

**Backward-compatible by construction:** the new param defaults `force=False` and is keyword-only, preserving every existing caller verbatim:
- `backend/api/routers/generation.py:461-463` — dispatched through the GPU pool: `await loop.run_in_executor(_gpu_pool, embed_watermark, audio_tensor, sample_rate)`. The telephony path must use `functools.partial(embed_watermark, force=True)` (or a lambda) when threading the kwarg through `run_in_executor`, since `run_in_executor` cannot pass keyword args directly.
- `backend/api/routers/dub_generate.py:485,750` — unchanged.

### Prerequisite spikes (gate v3 GA — `competitive-analysis.md:966-970`)
Two spikes are **prerequisites before we promise call quality**, and become tests/docs in this work:
- **(a) TTFA benchmark**: time-to-first-audio of our engines in a streaming pipeline vs the ~600 ms p95 voice-to-voice budget (`competitive-analysis.md:967-968`). Ship as `tests/test_telephony_ttfa.py` (skipped unless `OMNIVOICE_TELEPHONY_BENCH=1`), and a documented result in `docs/telephony.md`. Edge case: if no GPU is present in the bench environment, the test records the CPU number and `xfail`s the budget assertion rather than hard-failing (CPU TTFA will not meet 600 ms; that's expected and documented, not a regression).
- **(b) Watermark survival through 8 kHz G.711**: embed → downsample to 8 kHz µ-law → upsample → `detect_watermark`. `detect_watermark(waveform, sample_rate) -> dict` (`backend/services/watermark.py:145-219`) returns exactly:
  ```python
  {"is_watermarked": bool,      # confidence > 0.5
   "confidence": float,         # rounded to 4dp
   "message_bits": str,         # decoded 16-bit string, "" if none
   "is_omnivoice": bool,        # decoded bits == OMNI_MESSAGE
   "source": "VoiceStudio" | "unknown"}
  # OR, when AudioSeal is absent / detection raises:
  {"is_watermarked": False, "confidence": 0.0, "message_bits": "",
   "is_omnivoice": False, "error": "<reason>"}   # note the `error` key, no `source`
  ```
  Spike b is **untested anywhere** (`competitive-analysis.md:968-970`); phone-band downsampling may strip the AudioSeal mark. Ship as `tests/test_watermark_phoneband.py`. Three documented outcomes: (i) mark survives (`confidence > 0.5`, `is_omnivoice` true) → docs claim machine-readable marking holds; (ii) mark stripped → docs state the honest limitation and we fall back to the spoken disclosure as the binding marker (the disclosure preamble already satisfies Texas/FCC/Art.50(1) — `competitive-analysis.md:994-995`); (iii) `audioseal` not installed (`_check_available()` False → the `error`-keyed dict) → test skips and docs note the mark is conditional on the optional dep.

### Frontend — all panel states enumerated
A **Settings → Telephony (advanced)** panel as a new `frontend/src/components/settings/TelephonyPanel.jsx` (the Settings panels are **`.jsx`**, not `.tsx` — see siblings `RemoteBackendPanel.jsx`, `ApiKeysPanel.jsx`, `MCPBindingsPanel.jsx`, `SharingPanel.jsx` in `frontend/src/components/settings/`; wired into the tab list in `frontend/src/pages/Settings.jsx`). It is an opt-in expander, not a default tab. The panel renders one of the five backend states (read from `GET /telephony/status`), each with a defined UI:
- **`DISABLED`**: explainer + jurisdiction warning + "Enable telephony" affordance only. No allowlist, no log. This is what every default install shows.
- **`DEPS_MISSING`**: explainer + the exact `uv sync --extra telephony` command + a "restart the backend after installing" note (because `_telephony_available()` is process-memoized). Allowlist editor is shown (rows are just DB and can be prepared), call placement is disabled with a tooltip.
- **`NO_CREDENTIALS` / `PARTIAL_CREDENTIALS`**: write-only carrier-credential form (modeled on `ApiKeysPanel.jsx`: POST to set, never echoes the secret back), with `credential_fields_missing` highlighting which fields are blank for `PARTIAL_CREDENTIALS`. "I understand the legal responsibility" checkbox gates the form submit.
- **`READY`**: destination allowlist editor, daily-cap field, recording toggle (default off, with the two-party-consent warning shown inline before it can be toggled on), and the immutable call log (read-only, with terminal/refused status + reason + timestamp + `watermarked`/`recorded` badges). A live "call in progress" banner driven by the `telephony` event-bus events on `/ws/events`; on a terminal event it refetches the log.
- **Error/empty states**: `/status` fetch failure → a non-blocking "couldn't reach backend" message, panel falls back to read-only DISABLED appearance (never crashes the Settings page). Empty allowlist → an empty-state hint ("Add a number you own or have permission to call"). Empty call log → empty-state ("No calls placed yet").

**Backend prefs/settings are the source of truth — no localStorage state migration needed.** All telephony state (enabled toggle, carrier, daily cap, record default, allowlist, call log) lives in `prefs.json` / the encrypted `settings` table / DB, fetched fresh from `GET /telephony/status` and `/allowlist` on panel mount. Unlike `RemoteBackendPanel.jsx` (the one settings panel that persists URL+key in `localStorage:5,19-20,45-48`), `TelephonyPanel` deliberately does **not** persist carrier secrets or call state in `localStorage` (secrets must stay Fernet-encrypted server-side; the call log is immutable server-side). If the panel ever caches a non-secret UI preference in `localStorage` (e.g. "last-viewed log page"), it must read it **lazily with a default** (`localStorage.getItem(k) || <default>`, the pattern at `RemoteBackendPanel.jsx:19`) so a fresh install / cleared storage / older-version key shape never crashes the panel — satisfying the backward-compatible-data rule for client-side state.

The carrier-credential form mirrors `ApiKeysPanel.jsx`'s write-only secret handling. **All strings via i18n `t('...')`** keyed into `frontend/src/i18n/locales/en.json`, per the CLAUDE.md localization hard rule — **no hardcoded user-facing text, no CJK outside the i18n layer.** This includes every refusal `reason`, every status state label, the disclosure-preview text shown in the UI, and the legal-responsibility copy. **i18n architecture note (verified):** only `en.json` ships in the main bundle and is the `fallbackLng: 'en'` (`frontend/src/i18n/index.ts:7,59,64`); the other 20 locale JSONs (`ar, de, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, sv, th, tr, uk, vi, zh-CN, zh-TW`) are **lazy-loaded** and fall back to `en` per-key when a key is missing. Therefore the **load-bearing requirement is adding the telephony keys to `en.json`**; translating them into the other locales is desirable but their absence degrades gracefully to English (i18next fallback), it does not break the panel or trip `test_no_hardcoded_cjk.py`. Adding the English keys is what makes `tests/test_no_hardcoded_cjk.py` pass without new allowlist entries (no CJK literal ever lands in component code).

## Integration points (file:line)

| Concern | Location | Action |
|---|---|---|
| Milestone scope + guardrails (source of truth) | `docs/competitive-analysis.md:950-1015` (§R1); guardrails at `:994-1002`; scope ladder at `:1004-1015` | Implement v3 rung; keep doc in sync. |
| Deferral note to flip to "shipped, opt-in" | `docs/agentic-voice.md:12-15` | Update wording + link to new `docs/telephony.md` (docs-sync). |
| Watermark embed (add `force=`, keyword-only) | `backend/services/watermark.py:96-142`; self-gate at `:112`; `is_enabled()` at `:78-80`; try/except passthrough at `:115-142` | Add `*, force: bool=False`; gate becomes `if (not force and not is_enabled()) or not _check_available(): return waveform`. Param-additive + keyword-only (backward-compatible). |
| Watermark availability/detect (reuse for spike b) | `backend/services/watermark.py:43-53` (`_check_available`), `:145-219` (`detect_watermark`, dict shape pinned above) | Reuse `detect_watermark` in `test_watermark_phoneband.py`. |
| Existing watermark callers (must stay compatible) | `backend/api/routers/generation.py:461-463` (dispatched via `_gpu_pool` `run_in_executor`), `backend/api/routers/dub_generate.py:485,750` | `force=False` default + keyword-only preserves them; telephony path threads `force=True` via `functools.partial` through `run_in_executor`. |
| Provider TTS/STT the call leg speaks through | `backend/api/routers/openai_compat.py` — router prefix `/v1/audio` (`:37`), `create_speech(req: SpeechRequest)` handler (`:251-252`), `SpeechRequest` schema (`:43-111`), voices list (`:458-459`) | No change; pipecat points its OpenAI services here (`examples/agentic/pipecat_minimal.py`, `docs/agentic-voice.md:37-53`). The disclosure preamble is one `create_speech`-shaped call. |
| Consent lock the call gates on | `voice_profiles.verified_own_voice` (`backend/core/db.py:52`); set/clear in `backend/api/routers/profiles.py` — `record_consent` (`:325`, sets `verified_own_voice=1` at `:365`) / `revoke_consent` (`:381`, sets `=0` at `:390`); migration `backend/migrations/versions/0003_voice_profile_consent.py` | Read-only gate: `SELECT verified_own_voice FROM voice_profiles WHERE id=?`; refuse if `!= 1`. Re-read at place time (consent can be revoked between preflight and place). |
| Encrypted carrier-credential storage | `backend/services/settings_store.py:24-34` (`_fernet()`), `:37-73` (`get_hf_token`, `InvalidToken`→None at `:62-68`, SQLite-error→None at `:69-73`), `:76-98` (`set_hf_token` INSERT OR REPLACE), `:101-108` (`clear_hf_token`), `:123-165` (`get_text`/`set_text` reserved-key guard) | Add `set_carrier_secret(name: str, value: str) -> None` / `get_carrier_secret(name: str) -> Optional[str]` / `clear_carrier_secret(name: str) -> None` that **generalize** the `_fernet()` encrypt/decrypt path over the same `settings` table, keyed `f"carrier_secret.{name}"`, with the same `InvalidToken`→None and SQLite-error→None fallbacks. A reserved-key guard (mirroring `:131,153`) forbids `name == "hf_token"`. |
| Opt-in "inert by default" precedent | `backend/main.py:614-636` (`BearerKeyMiddleware`, `if not key:` early-out at `:634-636`); `remote_api_key()` `backend/api/dependencies.py:80`; `require_loopback()` `:52`; MCP opt-out `backend/main.py:780` | Mirror: telephony router inert unless `READY`, per the 5-state table. Reuse `require_loopback` as a router-level `Depends` (matching `mcp_bindings.py:18`). |
| Lazy-import precedent | `backend/mcp_server.py:31-48` (`_ensure_mcp()`); `backend/services/watermark.py:43-53` (`_check_available`, memoizes `_audioseal_available`) | Mirror for `pipecat`/carrier SDK in `services/telephony/config.py` (`_telephony_available()`, memoized) + `services/telephony/pipeline.py`. |
| Router registration | `backend/main.py:742-773` (`include_router` block, ends with the mcp_bindings router at `:773`, before the MCP mount at `:775`) | Add `from api.routers import telephony` + `app.include_router(telephony.router)` in this block. Trigger the non-terminal-row reconciliation sweep on first `/status`. |
| New migration sits after | `backend/migrations/versions/0005_unified_profiles.py` (head; `revision="0005_unified_profiles"` at `:34`, `down_revision="0004_mcp_client_bindings"` at `:35`) | New `0006_telephony.py` with `revision="0006_telephony"`, `down_revision="0005_unified_profiles"` (revision IDs are the **full slug string**). Follow the `_has_column`/`IF EXISTS` style (here: `CREATE TABLE IF NOT EXISTS` up, `DROP TABLE IF EXISTS` down). |
| Base schema for fresh installs | `backend/core/db.py:38-160` (`_BASE_SCHEMA`, `executescript` at `:212`); `db_conn()` context manager at `:21-35` (commits on clean exit, rolls back + re-raises on exception, always closes); `PRAGMA foreign_keys=ON` at `:17` | Add `telephony_calls` + `telephony_allowlist` `CREATE TABLE IF NOT EXISTS` + indexes inside `_BASE_SCHEMA` (converge with migration, same as `mcp_client_bindings` at `:152-159`). The `profile_id` FK is enforced. |
| Event bus for call progress (Settings UI) | `backend/core/event_bus.py:44-64` — `emit(kind: str, payload: dict|None) -> None` (NOT `publish`); event dict is `{"kind", "ts": time.time(), **payload}`; fire-and-forget, drops on `QueueFull`/no-loop (`:62-64,72-80`); `kind` docstring enum at `:50-51`; emitted like `backend/api/routers/profiles.py:373` | Call `emit("telephony", {...})` on each state transition (payload shape pinned in §SSE/WS events). Extend the allowed-`kind` enumeration in the `emit` docstring (`:50-51`) to add `telephony`. WS delivery is `ws_events` at `api/routers/events.py:25-26` (`/ws/events`). The call must never depend on a listener existing. |
| Per-agent voice binding (MCP-driven calls, future) | `backend/services/mcp_bindings.py:104` (`resolve_voice(client_id, explicit_profile_id)`) | Reuse precedence if an MCP tool ever triggers a call; not in scope for first slice. |
| Prefs (non-secret toggles) | `backend/core/prefs.py` — `get(key, default)` (`:58`), `set_(key, value)` (`:62`), `delete(key)` (`:68`), `resolve(key, *, env=None, default=None)` (`:75-82`, env-truthy gotcha at `:78-81`: `if v: return v`) | Store `telephony.enabled` (bool), `telephony.daily_cap` (int), `telephony.record_calls` (bool), `telephony.carrier` (str) via `set_`; read via `resolve(...)` + `_truthy()` coercion (so `"0"`/`""`/`"false"` read disabled from both env and prefs). `resolve` only takes `env=` for the `telephony.enabled` read (`OMNIVOICE_TELEPHONY`). |
| Secret redaction in logs (gap to close) | `backend/core/logging_filter.py:25` (`_HF_TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{30,}")`) — **only redacts `hf_` tokens**, NOT a general `*TOKEN*/*KEY*/*SECRET*` pattern; `install_redaction_filter` at `:60-71` | The existing filter will **not** catch Telnyx/Twilio secrets. Either add a carrier-token pattern (Twilio `AC[a-f0-9]{32}`/`SK[a-f0-9]{32}`, Telnyx `KEY[A-Za-z0-9]{...}`) to `logging_filter.py` or, simpler, never log carrier secrets at all in the telephony adapter. Any pattern added must be ReDoS-clean (literal prefix + bounded `[A-Za-z0-9]{N}` like the existing `hf_` rule). Do not claim the existing filter already covers them. |
| i18n strings (load-bearing locale) | `frontend/src/i18n/locales/en.json` (ships in main bundle; the `fallbackLng:'en'` per `i18n/index.ts:7,59,64`); 20 lazy-loaded sibling locales | Add all telephony keys to `en.json` (required). Translating the 20 siblings is optional — missing keys fall back to `en` and do not trip `test_no_hardcoded_cjk.py`. |
| Embedded agent example | `examples/agentic/pipecat_minimal.py` (exists) | Add a sibling `examples/agentic/telephony_outbound.py` skeleton (carrier serializer wiring). |
| Optional-dependency extra precedent | `pyproject.toml:114-146` (`[project.optional-dependencies]`; `supertonic` extra at `:144-146` uses an exact `==` pin) | Add a `telephony` extra alongside; not in `[project] dependencies`. |
| ReDoS-safe regex precedent (for the E.164 normalizer) | `backend/services/audiobook.py:33-42` (`_HEADING_RE`, `_VOICE_RE` — strip-first then literal-anchored, no overlapping quantifiers); `backend/services/ssml_lite.py:59`; `backend/services/pronunciation.py:21-22` | Mirror the house pattern: a single bounded, anchored pass over already-stripped input (`^\+\d{8,15}$`), with a `# CodeQL py/polynomial-redos` justification comment. |
| Router/Pydantic shape precedent | `backend/api/routers/mcp_bindings.py:15-53` (`APIRouter(prefix=..., dependencies=[Depends(require_loopback)])`; `BaseModel` bodies; `ValueError → HTTPException(400)`; `delete → 404 on miss`) | Model `telephony.py`'s router + Pydantic bodies on this exact shape. |
| Provider contract test (don't let `/v1/audio/*` drift) | `tests/test_agentic_provider_contract.py` (exists, 4.1K) | Extend / add a telephony-flavored contract assertion (see Test plan #10). |
| Client-side state lazy-default precedent | `frontend/src/components/settings/RemoteBackendPanel.jsx:19-20` (`localStorage.getItem(k) || ''`) | If `TelephonyPanel` caches any non-secret UI state, use the lazy-with-default pattern (never crash on missing/old key shape). No secrets in localStorage. |

## API / data shapes

### New DB tables (migration `0006_telephony.py` + `_BASE_SCHEMA`)
```sql
-- Immutable per-attempt call log (guardrail 5). Rows are append-only;
-- terminal status/duration/ended_at is the only UPDATE; no DELETE path in the app.
CREATE TABLE IF NOT EXISTS telephony_calls (
    id              TEXT PRIMARY KEY,           -- str(uuid.uuid4())[:12]  (matches generation_history's [:8] convention, wider for collision headroom)
    profile_id      TEXT NOT NULL,              -- must be verified_own_voice; FK enforced (PRAGMA foreign_keys=ON)
    destination     TEXT NOT NULL,              -- normalized +E.164, must be on allowlist; NOT NULL means a parse-fail refusal writes no row
    carrier         TEXT NOT NULL,              -- 'telnyx' | 'twilio'
    status          TEXT NOT NULL,              -- placing|ringing|in_progress|completed|failed|refused
    refused_reason  TEXT NOT NULL DEFAULT '',   -- one §Refusal-precedence reason OR 'interrupted'|'carrier_connect_failed'|'no_answer'|'busy'|'rejected'|'disclosure_synthesis_failed'; '' for non-refused/non-failed terminal
    disclosure_text TEXT NOT NULL DEFAULT '',   -- the exact preamble spoken (provenance); '' only for pre-disclosure refusals
    recorded        INTEGER NOT NULL DEFAULT 0, -- 1 only if two-party-consent ack'd AND carrier-side recording requested
    watermarked     INTEGER NOT NULL DEFAULT 1, -- 1 only if EVERY frame embedded; 0 if AudioSeal absent or any frame failed embed
    duration_s      REAL NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,              -- time.time() at row insert (used for daily-cap + DESC ordering)
    ended_at        REAL DEFAULT NULL,          -- time.time() at terminal transition
    FOREIGN KEY (profile_id) REFERENCES voice_profiles(id)
);
CREATE INDEX IF NOT EXISTS idx_telephony_calls_created ON telephony_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_telephony_calls_status  ON telephony_calls(status);  -- reconciliation sweep + in-flight + daily-cap counting

-- Destination allowlist (guardrail 4). A call to a number not here is refused.
CREATE TABLE IF NOT EXISTS telephony_allowlist (
    e164        TEXT PRIMARY KEY,               -- normalized +E.164
    label       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL                   -- time.time() at insert
);
```
**FK consequence (do not hand-wave):** because `PRAGMA foreign_keys=ON` (`db.py:17`), inserting a `telephony_calls` row whose `profile_id` no longer exists in `voice_profiles` will raise (and `db_conn()` rolls back + re-raises at `:28-33`). Therefore a refusal for a **deleted** profile (`profile_not_found`) returns 404 **without** writing a row; all other refusals reference a still-existing `profile_id` and write a row normally. Tested in `test_telephony_guardrails.py`.

Migration follows the additive, idempotent pattern of `0003`/`0004`/`0005`: `CREATE TABLE IF NOT EXISTS` on upgrade (`op.execute(sa.text(...))`), `DROP TABLE IF EXISTS` on downgrade; no-op on fresh installs because `_BASE_SCHEMA` (`core/db.py:38-160`) already has them — exactly how `mcp_client_bindings` converges between alembic `0004` and `_BASE_SCHEMA:152-159` (documented in `core/db.py:148-151,221-232`). Set `revision="0006_telephony"`, `down_revision="0005_unified_profiles"` (full slug strings, matching the `0003→0004→0005` chain). **Backward-compatible-data invariant (CLAUDE.md):** this is the *only* schema change in the task, it is purely additive (two new tables, no ALTER on an existing table, no data backfill), and existing `omnivoice_data/` keeps working without manual migration — the alembic upgrade is the tested path (Test #7). **Downgrade edge case:** `DROP TABLE` must be `DROP TABLE IF EXISTS` (idempotent downgrade), and the docs note that a downgrade discards the immutable call log — acceptable because downgrade is a developer/CI path, not a user path (users only ever upgrade; the CLAUDE.md rule is about the upgrade path being lossless, which it is — the new tables start empty).

### REST endpoints (`backend/api/routers/telephony.py`, `APIRouter(prefix="/telephony", tags=["telephony"], dependencies=[Depends(require_loopback)])`)

Every request/response body below is the **exact** JSON shape. Pydantic models named for the implementer.

```
GET    /telephony/status              -> 200 StatusBody (always reachable; the rest gate on state())
POST   /telephony/settings            -> 200 StatusBody  (idempotent; returns the recomputed status)
GET    /telephony/allowlist           -> 200 [AllowlistRow, ...]   (created_at DESC)
POST   /telephony/allowlist           -> 201 AllowlistRow (fresh) | 200 AllowlistRow (existing) | 422 RefusalBody
DELETE /telephony/allowlist/{e164}    -> 200 {"deleted": "<normalized e164>"} | 404 {"detail": "..."}
POST   /telephony/preflight           -> 200 PreflightOK | 4xx RefusalBody   (NO call placed, NO row written)
POST   /telephony/calls               -> 200 CallRow | 4xx RefusalBody       (places ONE call)
GET    /telephony/calls               -> 200 CallLogPage                     (read-only, paginated, created_at DESC)
```
**Absent by design** (tested-for-absence): no `POST /telephony/calls/batch`, no list-dial, no CSV import, no `DELETE /telephony/calls/{id}`, no `PATCH`/`PUT` on a call row (guardrails 4 + 5, `competitive-analysis.md:999-1000`). `POST /telephony/calls` accepts exactly **one** `destination`. Absent routes → 404 (route truly does not exist) or 405 (method not allowed on an existing path).

#### `GET /telephony/status` → `StatusBody`
```jsonc
// Always 200. Shape is the full StatusDict regardless of state; absent-field
// invariants per the 5-state table.
{
  "enabled": true,
  "available": true,                       // pipecat extra importable
  "carrier": "telnyx",                     // "telnyx" | "twilio" | null
  "daily_cap": 5,
  "calls_today": 1,                        // carrier-reaching attempts in host-local day
  "has_credentials": true,
  "credential_fields_missing": [],         // e.g. ["twilio_auth_token"] in PARTIAL_CREDENTIALS
  "watermark_available": true,             // watermark._check_available()
  "record_calls": false,                   // the telephony.record_calls pref default
  "call_in_progress": false,
  "state": "READY"                         // DISABLED|DEPS_MISSING|NO_CREDENTIALS|PARTIAL_CREDENTIALS|READY
}
// DISABLED minimal form (no carrier creds, default install):
{ "enabled": false, "available": false, "carrier": null, "daily_cap": 5,
  "calls_today": 0, "has_credentials": false, "credential_fields_missing": [],
  "watermark_available": <bool>, "record_calls": false, "call_in_progress": false,
  "state": "DISABLED" }
```

#### `POST /telephony/settings` — body `SettingsBody` (all fields optional; only present fields are written)
```jsonc
// Request
{
  "enabled":      true,                    // -> prefs.set_("telephony.enabled", bool)
  "carrier":      "telnyx",                // -> prefs.set_("telephony.carrier", "telnyx"|"twilio"); 422 on other values
  "daily_cap":    5,                       // -> prefs.set_("telephony.daily_cap", int>=0); 422 on negative
  "record_calls": false,                   // -> prefs.set_("telephony.record_calls", bool)
  // carrier secrets are write-only and routed to settings_store, never echoed back:
  "credentials":  { "telnyx_api_key": "KEY...", "telnyx_connection_id": "...", "telnyx_from_number": "+1..." }
}
// Pydantic:
//   class SettingsBody(BaseModel):
//       enabled:      Optional[bool] = None
//       carrier:      Optional[Literal["telnyx","twilio"]] = None
//       daily_cap:    Optional[int] = Field(default=None, ge=0)
//       record_calls: Optional[bool] = None
//       credentials:  Optional[dict[str, str]] = None   # keys validated against CARRIER_FIELDS[carrier]
// Response: 200 StatusBody (recomputed). Unknown credential keys -> 422
//   {"ok": false, "reason": "invalid_credential_field"}. Empty-string credential value -> clear_carrier_secret(name).
```

#### `GET /telephony/allowlist` → `[AllowlistRow]` ; `POST` body `AllowlistAddBody`
```jsonc
// AllowlistRow
{ "e164": "+15550100000", "label": "My clinic", "created_at": 1749830400.0 }
// POST request:  class AllowlistAddBody(BaseModel): destination: str; label: str = ""
{ "destination": "+1 (555) 010-0000", "label": "My clinic" }
// POST 201 (fresh) / 200 (already existed) -> AllowlistRow with normalized e164.
// Non-normalizable -> 422 RefusalBody {"ok": false, "reason": "invalid_destination"}.
// Emergency/premium prefix -> 422 RefusalBody {"ok": false, "reason": "blocked_destination"}.
```

#### `POST /telephony/preflight` and `POST /telephony/calls` — shared request `CallBody`
```jsonc
// class CallBody(BaseModel):
//     profile_id:   str = Field(..., min_length=1)
//     destination:  str = Field(..., min_length=1)
//     record:       bool = False
//     agent_prompt: Optional[str] = Field(default=None, max_length=4000)
{ "profile_id": "abc123def456", "destination": "+15550100000",
  "record": false, "agent_prompt": "Ask whether table-for-two at 7pm is available." }
```

#### `POST /telephony/preflight` → `PreflightOK` | `RefusalBody` (no row ever written)
```jsonc
// 200 PreflightOK
{
  "ok": true,
  "call_plan": {
    "profile_id": "abc123def456",
    "destination": "+15550100000",          // normalized
    "carrier": "telnyx",
    "disclosure_text": "This call uses an AI-generated voice.",   // exactly what would be spoken
    "record": false
  }
}
// 4xx RefusalBody (advisory; NO row)
{ "ok": false, "reason": "not_allowlisted" }
```

#### `POST /telephony/calls` → `CallRow` | `RefusalBody`
```jsonc
// 200 CallRow (call accepted & placing; the immutable row, in its current/terminal state).
// Note: /calls returns synchronously once the row is created in 'placing'; live progress
// and the terminal status arrive via the `telephony` WS events + a /calls refetch.
{
  "id": "9f2c1a7b3d4e",
  "profile_id": "abc123def456",
  "destination": "+15550100000",
  "carrier": "telnyx",
  "status": "placing",
  "refused_reason": "",
  "disclosure_text": "This call uses an AI-generated voice.",
  "recorded": false,                         // INTEGER 0/1 in DB, surfaced as JSON bool
  "watermarked": true,                       // INTEGER 0/1 in DB, surfaced as JSON bool
  "duration_s": 0.0,
  "created_at": 1749830460.0,
  "ended_at": null
}
// 4xx RefusalBody. HTTP status per reason:
//   no_credentials       -> 400, no row
//   call_in_progress     -> 409, no row
//   invalid_destination  -> 422, row IF a normalizable destination exists else no row
//   blocked_destination  -> 422, refused row
//   not_allowlisted      -> 403, refused row
//   profile_not_found    -> 404, NO row (FK would reject)
//   not_verified         -> 403, refused row
//   cap_exceeded         -> 429, refused row
{ "ok": false, "reason": "cap_exceeded" }
```

#### `GET /telephony/calls` → `CallLogPage`
```jsonc
// Query: ?offset=0&limit=50  (limit clamped to [1,200], default 50; never an unbounded dump)
{
  "calls": [ /* CallRow, ... ordered created_at DESC */ ],
  "offset": 0,
  "limit": 50,
  "total": 1            // COUNT(*) of telephony_calls (for UI pagination)
}
// Empty log -> {"calls": [], "offset": 0, "limit": 50, "total": 0}
```

#### `RefusalBody` — the single-valued, non-localized refusal enum
```jsonc
// `reason` is a STABLE machine-readable enum (never localized on the wire).
// The UI maps each value to a t('telephony.refusal.<reason>') key for display.
{ "ok": false,
  "reason": "no_credentials" | "call_in_progress" | "invalid_destination"
          | "blocked_destination" | "not_allowlisted" | "profile_not_found"
          | "not_verified" | "cap_exceeded" }
```

**State-dependent HTTP behaviour (the 5-state machine, mirrored in HTTP):**
- `DISABLED`: every endpoint except `GET /telephony/status` → `404 {"detail":"Telephony is disabled. Enable it in Settings → Telephony."}` (detail string is server-side and English-only; the *user-facing* rendering of this state in the panel goes through i18n `t()`).
- `DEPS_MISSING`: `/status` 200; `/allowlist*` + `/settings` 200; `/preflight` + `/calls` → `501 {"detail":"pipecat telephony extra not installed. Run: uv sync --extra telephony, then restart the backend."}` (matches the `uv sync --extra <name>` convention used for `supertonic` — `pyproject.toml:132-134`; the restart hint reflects the process-memoized `_telephony_available()`).
- `NO_CREDENTIALS`/`PARTIAL_CREDENTIALS`: `/preflight` + `/calls` → 400 `{"ok":false,"reason":"no_credentials"}`; everything else 200.
- `READY`: full surface; `/calls` may still return any §Refusal-precedence reason.

### SSE/WS events — `emit("telephony", payload)` (delivered over `/ws/events`, `events.py:25-26`)
The event bus produces `{"kind":"telephony", "ts": <float>, **payload}` (the `kind`+`ts` wrapper is added by `emit` at `event_bus.py:53-57`). **Pinned `payload` shapes per transition** (the frontend keys off `call_id` + `status`):
```jsonc
// placing  (immediately after row insert)
{ "kind":"telephony", "ts":1749830460.0, "call_id":"9f2c1a7b3d4e", "status":"placing", "destination":"+15550100000" }
// ringing
{ "kind":"telephony", "ts":1749830462.0, "call_id":"9f2c1a7b3d4e", "status":"ringing" }
// in_progress  (disclosure played, conversation live)
{ "kind":"telephony", "ts":1749830465.0, "call_id":"9f2c1a7b3d4e", "status":"in_progress" }
// terminal (completed | failed) — carries the fields the UI badges need
{ "kind":"telephony", "ts":1749830520.0, "call_id":"9f2c1a7b3d4e", "status":"completed",
  "duration_s":55.0, "watermarked":true, "refused_reason":"" }
// watermark degradation warning (non-terminal, advisory)
{ "kind":"telephony", "ts":1749830500.0, "call_id":"9f2c1a7b3d4e", "status":"in_progress",
  "warning":"watermark_degraded" }
```
On any terminal event the panel refetches `GET /telephony/calls`. The DB row is the source of truth; events are a "refetch this" signal only (consistent with `event_bus.py:8-10` and the `events.py:9` docstring). A dropped/late event never affects call outcome.

### Disclosure preamble (non-removable, generated server-side)
```python
# backend/services/telephony/disclosure.py
from __future__ import annotations

# ASCII-English server-side string table — there is NO backend t() helper today
# (i18n lives entirely in frontend/src/i18n/, i18next, fallbackLng:'en'). Keep
# this table plain-ASCII English (test_no_hardcoded_cjk.py — no CJK literal).
_DISCLOSURE: dict[str, dict[str, str]] = {
    "en": {
        "ai_voice": "This call uses an AI-generated voice.",
        "recording_notice": "This call may be recorded. By staying on the line you consent to recording.",
    },
    # other locales optional; missing locale falls back to "en"
}

def disclosure_text(record: bool, locale: str = "en") -> str:
    """First spoken frame. Never empty, never raises (defeats guardrail 1).
    Unknown locale -> 'en' fallback (mirrors frontend fallbackLng:'en')."""
    table = _DISCLOSURE.get(locale, _DISCLOSURE["en"])
    base = table.get("ai_voice") or _DISCLOSURE["en"]["ai_voice"]
    if record:
        notice = table.get("recording_notice") or _DISCLOSURE["en"]["recording_notice"]
        return f"{base} {notice}"
    return base
```
Spoken as the **first** TTS frame via a `create_speech`-shaped synthesis (`openai_compat.py:251`, `SpeechRequest` with the consent-locked profile's voice + `force=True` watermark), before any agent turn. Stored verbatim in `telephony_calls.disclosure_text` for provenance. The agent prompt cannot suppress or precede it. **Failure path:** if synthesis raises or returns zero-length audio, the call is aborted before the carrier leg connects (`refused_reason='disclosure_synthesis_failed'`, terminal `failed` row); a call must never reach a human without a disclosed preamble. **Server-side i18n note (verified):** do **not** assume a Python `t()` exists — these strings are the only telephony "user-facing" text the backend emits, and they live in the ASCII-English `_DISCLOSURE` table with `en` fallback (option (b)); alternatively (option (a)) the frontend may pass a pre-rendered `disclosure_text` string at call-placement time, but the server-side table is the authoritative fallback so the disclosure is never blank regardless of caller locale.

### Carrier-secret storage helpers (`settings_store.py`, generalizing the `_fernet()` path)
```python
# names are CARRIER_FIELDS members, e.g. "telnyx_api_key", "twilio_auth_token".
# Stored under settings key f"carrier_secret.{name}", Fernet-encrypted like hf_token.
def set_carrier_secret(name: str, value: str) -> None: ...   # empty value -> clear_carrier_secret(name)
def get_carrier_secret(name: str) -> Optional[str]: ...      # InvalidToken -> None; SQLite error -> None (no ciphertext logged)
def clear_carrier_secret(name: str) -> None: ...
# Reserved-key guard: name == "hf_token" -> raise ValueError (mirrors set_text guard at settings_store.py:153).
```

## Test plan

All call-placing tests use a **fake carrier transport** (a `FakeSerializer` that records frames instead of hitting a network) — **no real PSTN call in CI, ever**.

1. **`tests/test_telephony_gating.py`** — the full 5-state matrix:
   - `DISABLED` (default): `GET /telephony/status` → `{enabled:false, state:"DISABLED", ...}` (the minimal-form body above); `POST /telephony/calls` → 404.
   - `OMNIVOICE_TELEPHONY=0` env ⇒ still `DISABLED` (the `_truthy` coercion; guards the `resolve` env-truthiness gotcha at `prefs.py:80-81`).
   - `DEPS_MISSING`: enabled + import probe forced False → `/calls` 501 with the exact `uv sync --extra telephony, then restart the backend.` detail; allowlist/settings still 200.
   - `NO_CREDENTIALS` / `PARTIAL_CREDENTIALS`: `/calls` → 400 `{"ok":false,"reason":"no_credentials"}`; `/status.credential_fields_missing` surfaces the blank field name (e.g. `["twilio_auth_token"]`) in PARTIAL.
   - SQLite read failure on credentials → degrades to `NO_CREDENTIALS`, no 500, no error text leaked.
   - `StatusBody` shape assertion: every key in the pinned `StatusDict` is present in every state.
2. **`tests/test_telephony_guardrails.py`**:
   - Non-allowlisted number → 403 `reason:"not_allowlisted"`, refused row written.
   - Profile `verified_own_voice=0` → 403 `reason:"not_verified"`, refused row written.
   - Profile deleted before place → 404 `reason:"profile_not_found"`, **no row** (FK constraint respected; `db_conn()` would roll back).
   - `daily_cap` reached → 429 `reason:"cap_exceeded"`; cap counts only attempts that reached the carrier (refused rows don't count); `cap=0` refuses every call.
   - Daily-cap day boundary is **host-local timezone**, computed from `time.localtime()`+`time.mktime(local-midnight)` (assert a call just before/after local midnight lands in the right day; assert the same logic runs without an OS branch).
   - Emergency / premium number on the allowlist → still 422 `reason:"blocked_destination"` (allowlist cannot override the safety denylist; `_BLOCKED_PREFIXES` startswith test).
   - **E.164 normalizer ReDoS resistance**: feed a pathological adversarial destination (long run of `+`, digits, and separators) and assert the normalizer returns in bounded time and yields `invalid_destination` (guards CodeQL py/polynomial-redos; the regex is `^\+\d{8,15}$`, anchored/bounded/non-overlapping per §Destination validation).
   - Allowlist add idempotency: fresh → 201, duplicate (same normalized e164) → 200 with updated label; delete-missing → 404.
   - Refusal-reason **precedence** is single-valued and deterministic (a request failing multiple gates returns the highest-precedence reason; assert the §precedence ordering explicitly).
   - Second concurrent `/calls` while one is active → 409 `reason:"call_in_progress"`, no row (`_call_lock.locked()` probe).
   - **No batch endpoint exists** — `POST /telephony/calls/batch` → 404/405 (architectural guarantee).
   - Call log immutable: no DELETE route; `DELETE /telephony/calls/{id}` → 405; no PATCH/PUT.
   - **Reconciliation sweep**: seed a `placing`/`in_progress` row, re-init the router/process → row flipped to `failed` + `refused_reason='interrupted'` + `ended_at` set (UPDATE, never DELETE).
   - `GET /telephony/calls` pagination: `limit` clamped to [1,200]; `total` present; ordering `created_at DESC`; empty → `{"calls":[],"total":0,...}`.
3. **`tests/test_telephony_disclosure.py`** — first synthesized frame text == `disclosure_text(record, locale)`; `record=True` appends the two-party-consent notice; agent prompt cannot reorder/suppress it; unknown `locale` falls back to non-empty `en`; synthesis failure on the preamble → call aborted `disclosure_synthesis_failed` (terminal `failed` row, no human reached).
4. **`tests/test_telephony_watermark.py`** — every synthesized frame in the fake pipeline passes through `embed_watermark(..., force=True)`; `telephony_calls.watermarked==1` when AudioSeal present and all frames embed. Assert `force=True` bypasses a `watermark.invisible=False` pref (the override of the user toggle at `watermark.py:78-80,112`). Assert that with AudioSeal absent (`_check_available()` False) the call still places and `watermarked` is written `0` + `/status.watermark_available:false`. Assert one mid-call embed exception flips the call's `watermarked` to `0` (and emits the `warning:"watermark_degraded"` event) without aborting the call. Assert non-telephony callers (`generation.py:461-463`, `dub_generate.py`) are unaffected by the new keyword-only param (default `force=False` → identical behaviour; positional call signature unchanged).
5. **`tests/test_watermark_phoneband.py`** (spike b) — embed → 8 kHz G.711 µ-law round-trip → `detect_watermark`; assert the returned dict shape (the pinned `is_watermarked`/`confidence`/`message_bits`/`is_omnivoice`/`source` keys, or the `error`-keyed dict when AudioSeal absent) and record `confidence`/`is_omnivoice`. Skipped if AudioSeal not installed (use `watermark._check_available()`); result feeds the docs honesty note (survive / stripped / dep-absent — all three documented).
6. **`tests/test_telephony_ttfa.py`** (spike a) — gated on `OMNIVOICE_TELEPHONY_BENCH=1`; asserts TTFA p95 measured and < budget on GPU, `xfail`s (records the number) on CPU / no-GPU environments rather than hard-failing.
7. **`tests/test_telephony_migration.py`** — `0005_unified_profiles → 0006_telephony` upgrade then downgrade is clean (downgrade `DROP TABLE IF EXISTS`); fresh `_BASE_SCHEMA` already has both tables with the pinned columns/indexes (double-apply is a no-op); existing `omnivoice_data/` untouched; re-running upgrade on an already-migrated DB is idempotent. (Follows the migration-roundtrip pattern used for prior migrations.)
8. **`tests/test_no_hardcoded_cjk.py`** — must still pass (telephony + disclosure strings go through i18n / the `en` server-side `_DISCLOSURE` table; no CJK literal in component or backend code; **no new `_ALLOWED_FILES` allowlist entries** needed — the disclosure table is ASCII English, the panel strings are `t()` keys).
9. **Frontend `TelephonyPanel.test.jsx`** (vitest `.jsx`, matching siblings `ApiKeysPanel.test.jsx`, `SharingPanel.test.jsx`, `PerformancePanel.test.jsx`, `AppearancePanel.test.jsx` under `frontend/src/components/settings/`) — each of the 5 states (read from a mocked `GET /telephony/status` `StatusBody`) renders its defined UI; `/status` fetch failure → non-blocking message, no crash; empty allowlist + empty call log render their empty-states; recording toggle gated behind the two-party-consent warning; legal-responsibility checkbox gates the credential form submit; `credential_fields_missing` highlights the right field in PARTIAL; if any non-secret UI state is cached in `localStorage`, a missing/old key reads its default and the panel does not crash; `bunx vitest run` green (per merge-discipline memory).
10. **Contract**: extend `tests/test_agentic_provider_contract.py` (already pins the `/v1/audio/*` request shape per `docs/agentic-voice.md:28-29`) — or add `tests/test_telephony_provider_contract.py` — pinning that the embedded pipeline points TTS/STT at `localhost:3900/v1` and that the disclosure synthesis call conforms to the `SpeechRequest` schema (`openai_compat.py:43-111`), so the existing `/v1/audio/*` shape can't silently drift under it.

## Constraints

Each relevant VoiceStudio hard rule (CLAUDE.md) and how this spec satisfies it:

- **Local-first guarantee (CLAUDE.md: "No required cloud calls, accounts, or API keys")**: opt-in by construction — with no carrier creds the feature is absent (`DISABLED`/`NO_CREDENTIALS`, `state()` table). No VoiceStudio-owned account, no telemetry endpoint, no required cloud call for non-users; carrier secrets live only Fernet-encrypted in the local `settings` table (keyed `carrier_secret.<name>`) and are never returned by any endpoint (`POST /settings` echoes back only `StatusBody`, with secrets routed through `set_carrier_secret`). The carrier is the user's own paid account — disclosed plainly in `docs/telephony.md` as the one unavoidable non-local hop, consistent with the existing `docs/agentic-voice.md:12-15` and `competitive-analysis.md:962-965` "no fully-local path to the PSTN" honesty. The 100% of installs that never enable it are byte-for-byte unchanged.

- **Default-features-work-on-every-platform (CLAUDE.md strict rule, 2026-05-20)**: this is **not** a default feature, so it is legitimately allowed to be platform-gated/opt-in. It sits behind an explicit Settings toggle + env var (`OMNIVOICE_TELEPHONY`) + credential requirement (three gates), exactly the "Settings toggle, env var, or CLI flag" opt-in mechanism the rule prescribes for non-default behaviour. **And** there is no platform divergence even in the *enabled* path: pipecat + carrier WS are pure-Python over the network, the daily-cap boundary uses stdlib `time.localtime()`, and the single-call serialization uses stdlib `asyncio.Lock` — identical on macOS/Windows/Linux. No P0 "default doesn't work on a platform" risk because nothing here is default; no platform-only code path in the user-visible behaviour.

- **Backward-compatible project data (CLAUDE.md)**: the only schema change is the additive, alembic-versioned `0006_telephony.py` (two new empty tables, no ALTER, no backfill), with a tested idempotent upgrade and an `IF EXISTS` downgrade (Test #7). Existing `omnivoice_data/` (voices, projects, settings) keeps working with no manual migration; the upgrade path is lossless because the new tables start empty. Client-side: `TelephonyPanel` reads state from the backend on mount (no localStorage schema to migrate); any non-secret UI state it caches uses the lazy-with-default read pattern (`RemoteBackendPanel.jsx:19`) so a missing/older key shape degrades gracefully, never crashes.

- **Existing engine compatibility (CLAUDE.md)**: no engine code is touched. The single shared-code change, `embed_watermark`, is **parameter-additive and keyword-only** (`*, force: bool=False`) and preserves the existing `_check_available()` gate and error-passthrough, so every on-disk engine state and every existing caller (`generation.py:461-463`, `dub_generate.py:485,750`) behaves identically (Test #4 asserts this). No reinstall for users with IndexTTS/CosyVoice/etc.

- **CodeQL py/polynomial-redos (binding gate on PRs touching user-input regex)**: the E.164 normalizer is the only user-input-reachable regex this task adds. It is written ReDoS-clean per the house style already in the repo (`audiobook.py:33-42`, `ssml_lite.py:59`, `pronunciation.py:21-22`): strip separators with a literal character class first (`re.sub(r"[ \t().\-]", "", s)`), normalize a leading `00`/`011` to `+`, then validate the remainder against an **anchored, bounded, non-overlapping** pattern (`^\+\d{8,15}$` — E.164 caps the digit count, so no unbounded `+`/`*` over the same characters; no nested quantifiers; no `\s*`/`.+` adjacency). A justification comment `# CodeQL py/polynomial-redos: …` sits beside the compiled pattern, and Test #2 feeds an adversarial input asserting bounded-time refusal. The emergency/premium denylist is a literal `startswith` prefix-set test (no regex). Any carrier-token addition to `logging_filter.py` follows the same bounded shape as the existing `hf_[A-Za-z0-9]{30,}` rule (e.g. `AC[a-f0-9]{32}`, `SK[a-f0-9]{32}`, `KEY[A-Za-z0-9]{32,}`).

- **Localization hard rule (CLAUDE.md: no hardcoded non-English text outside `frontend/src/i18n/`; all UI via `t()`)**: every panel string (state labels, refusal-reason display, disclosure preview, legal copy) is a `t('telephony.*')` key. The load-bearing add is the English keys in `frontend/src/i18n/locales/en.json` (the only locale in the main bundle, `fallbackLng:'en'` per `i18n/index.ts:7,59,64`); the 20 lazy-loaded sibling locales fall back to `en` per-key if untranslated, which degrades gracefully and does not break the panel. The wire-level refusal `reason` enum is never localized (machine-readable). The server-side `_DISCLOSURE` table is plain ASCII English with `en` fallback on unknown locale (never empty, never raises). No hardcoded CJK anywhere; `test_no_hardcoded_cjk.py` passes with **no** new `_ALLOWED_FILES` entries.

- **Docs-sync hard rule (CLAUDE.md, 2026-06-11)**: `docs/telephony.md` (new) + the `docs/agentic-voice.md:12-15` deferral→shipped-opt-in flip + the `competitive-analysis.md` §R1 status flip + a README opt-in-capability mention all land in the **same PR series** as the behaviour. `docs/telephony.md` must additionally document: the host-local-timezone cap boundary, the emergency/premium denylist, that "record" means carrier-side + spoken notice (not a local WAV) in slice 1, the spike (a)/(b) results, that installing the extra requires a backend restart, and the honest jurisdiction/gray-zone language. Stale docs are bugs; the doc impact is known up front, not deferred.

- **Versioning (hard rule, 2026-06-11)**: main is latest-release+1-patch (currently **v0.3.6**). This feature does **not** bump minor/major and invents no RC/codename. It ships **continuous-to-main** across the PR slices; version files (`frontend/src-tauri/tauri.conf.json`, `frontend/src-tauri/Cargo.toml`, `pyproject.toml`) stay in lockstep at v0.3.6 and are not touched by this work. Scope is absorbed into the open v0.3.x line, never re-versioned or deferred.

- **Beta cadence (no RC, no ceremony, 2026-05-20)**: each slice is independently mergeable, CI-green before merge (merge-discipline memory), no soak/RC/phased release. Guardrails 1–5 must all exist in code before any real call is possible.

- **Regulatory (binding, not optional — `competitive-analysis.md:981-1002`)**: guardrails 1–5 are acceptance gates enforced in code, not docs. Tennessee ELVIS Act extends liability to us as tool provider (`:985-986`), so the gates live in the call path, not in copy. The **disclosure-before-connect invariant** (a call can never reach a human without a disclosed, synthesized preamble) is the load-bearing legal guarantee and is tested (#3). EU AI Act Art. 50 (from 2026-08-02) wants AI disclosure + machine-readable marking — the spoken disclosure satisfies disclosure unconditionally; the AudioSeal mark satisfies machine-readable marking *if* it survives the phone band (spike b decides, documented honestly either way).

## Dependencies

- **New optional extra** in `pyproject.toml:114-146` (`[project.optional-dependencies]`, alongside the existing `eval`/`ui`/`supertonic` extras): e.g. `telephony = ["pipecat-ai[silero,openai,telnyx]"]` (or `[...,twilio]`). **Not** added to `[project] dependencies`. Lazy-imported (`mcp_server.py:31-48` pattern). The existing extras pin exact versions (`supertonic==1.3.1` at `:144-146`); follow that convention with a verified pin. Verify `uv tree` resolves cleanly and adds **nothing** to the default install (Acceptance: default install byte-for-byte unchanged).
- `audioseal` — already wired (`backend/services/watermark.py`); used force-on for telephony. Its absence is a handled degraded path (`watermarked=0`), not an error.
- `cryptography`/Fernet — already present (`backend/services/settings_store.py:24-34`, `_fernet()`); reused for carrier secrets (generalize the currently single-key `set_hf_token` path; reuse its `InvalidToken`→None and SQLite-error→None fallbacks).
- `httpx` — already in deps; used for any carrier REST handshake.
- `phonenumbers` — **deliberately not added** (verified absent from `uv tree`). The E.164 normalizer is a strict, ReDoS-clean pure-Python regex (`^\+\d{8,15}$` over pre-stripped input — see §Destination validation + §Constraints/CodeQL), documented in `docs/telephony.md` as "format-checked, not carrier-validated." No new default dep, no platform-specific native dep.
- No new default runtime deps. Per CLAUDE.md "Installation" section, only Capability 4 (Supertonic) added a default-ish dep (and even that is an opt-in extra); this task adds an **optional extra only**.
- External (user-supplied, documented, not bundled): a Telnyx or Twilio account + a purchased number (the `*_from_number` credential field) + a media-streaming application configured to point at the user's backend WS URL.

## Risk

| Risk | Severity | Mitigation |
|---|---|---|
| **Watermark stripped by 8 kHz G.711 phone band** (untested — `competitive-analysis.md:968-970`) | High | Spike (b) test up front (`detect_watermark` at `watermark.py:145-219`, dict shape pinned in §API); three documented outcomes (survives / stripped / dep-absent). If stripped, lean on the spoken disclosure preamble (which alone satisfies Texas/FCC/Art.50(1) — `competitive-analysis.md:994-995`) and document the limitation honestly. `watermarked=0` on the call row when the mark isn't present. Do not claim machine-readable marking survives if it doesn't. |
| **A call reaches a human with no disclosure** (TTS fails on the preamble) | High | Disclosure-before-connect invariant: synthesize + verify non-empty disclosure audio *before* the carrier leg connects; on failure abort with `disclosure_synthesis_failed`, no call placed. Tested (#3). |
| **Tool-provider liability** (ELVIS Act, `competitive-analysis.md:985-986`) | High | Guardrails enforced in code + "I understand legal responsibility" gate + honest jurisdiction notice. The "my own voice, my own errand" case is a genuine gray zone (`:990-992`) — docs say so, don't imply safety. |
| **Robocall-infra misuse** | High | No bulk/batch/list-dial endpoint exists (tested-for-absence); daily cap; allowlist-only; single-call-at-a-time via non-blocking `_call_lock.locked()` (no parallelism, no queue); no auto-redial; emergency/premium denylist; immutable log. Architecturally incapable of campaigns (`competitive-analysis.md:999-1000`). |
| **CodeQL py/polynomial-redos on the E.164 normalizer** (user-input regex blocks the PR) | Med | Anchored/bounded/non-overlapping pattern (`^\+\d{8,15}$`) over pre-stripped input, mirroring `audiobook.py:33-42`/`ssml_lite.py:59`/`pronunciation.py:21-22`; literal `startswith` denylist (no regex); adversarial-input bounded-time test (#2); justification comment beside the compiled pattern. |
| **Localization drift / hardcoded text trips `test_no_hardcoded_cjk.py` or breaks a locale** | Med | All UI via `t()` keyed into `en.json` (the `fallbackLng:'en'` main-bundle locale); 20 sibling locales fall back to `en` per-key, so missing translations degrade gracefully and don't break the panel; server-side `_DISCLOSURE` table is ASCII English with `en` fallback (never empty/raise); refusal `reason` is a non-localized enum. No new `_ALLOWED_FILES` entry. |
| **Daily cap bypass via timezone/race/refusal-counting** | Med | Cap counts only carrier-reaching attempts (`status IN ('placing','ringing','in_progress','completed','failed')`) in the host-local day (stdlib `time.localtime()`+`mktime`, identical on all OSes), read inside the single-call critical section. Refused rows don't burn budget (so a misconfig can't lock the user out, and a refusal loop can't be used as a counter trick). Tested (#2). |
| **Stuck non-terminal row after crash blocks all future calls** | Med | Reconciliation sweep on router init / first `/status` runs `UPDATE … SET status='failed', refused_reason='interrupted', ended_at=now WHERE status IN ('placing','ringing','in_progress')` (UPDATE, immutable-preserving). Tested (#2). |
| **Local-first promise erosion** | Med | Opt-in by construction; feature absent without creds (`DISABLED`/`NO_CREDENTIALS`); carrier is user's own; default install unchanged (`uv tree` clean). Mirrors the already-accepted `docs/agentic-voice.md:12-15` honesty framing. |
| **TTFA exceeds 600 ms budget** → unusable calls | Med | Spike (a) benchmark before promising quality (`competitive-analysis.md:967-968`); CPU/no-GPU bench `xfail`s with the number recorded; document realistic engine/latency guidance; allow draft (low-step) synthesis for lower TTFA. |
| **Half-saved carrier credentials silently treated as ready** | Med | `PARTIAL_CREDENTIALS` is a distinct state; call refused 400 `no_credentials`; `/status.credential_fields_missing` tells the UI which `CARRIER_FIELDS` entry is blank (without logging it). |
| **Carrier credential leak in logs** | Med | Fernet-encrypted at rest (`settings_store._fernet()`, generalized `set/get/clear_carrier_secret` keyed `carrier_secret.<name>`); never returned by `/telephony/status` or `/settings`; `InvalidToken`/SQLite-error → treated absent (no ciphertext logged). **Note:** `core/logging_filter.py:25` only redacts `hf_` tokens — it does **not** catch carrier secrets. Either extend the filter with carrier-token patterns (Twilio `AC[a-f0-9]{32}`/`SK[a-f0-9]{32}`, Telnyx `KEY[A-Za-z0-9]{32,}`, each ReDoS-clean) or, preferably, never pass carrier secrets to any logger in the telephony adapter; never log carrier *error bodies* either (they can echo account hints). |
| **Cross-platform divergence in the enabled path** | Low | No OS-specific code in the user-visible behaviour: pipecat/carrier WS are pure-Python over the network, cap boundary is `time.localtime()`, single-call lock is `asyncio.Lock` — identical on macOS/Windows/Linux. The feature is opt-in, so it is not subject to the default-feature parity P0 rule regardless. |
| **Scope creep into inbound / Discord / LiveKit SIP / parallel calls / redial** | Low | Explicit non-goals; those are separate §R1 rungs (`competitive-analysis.md:957,1008-1011`) or deliberately excluded (parallelism, retry). |
| **Maintenance burden of carrier SDKs** | Low | Optional extra, lazy import, thin adapter; default install unaffected; absence is the default and fully tested. |

## PR slices

Each slice is independently mergeable, continuous-to-main, CI-green before merge (merge-discipline memory). Version files stay at v0.3.6 throughout (no minor bump, no RC — versioning hard rule). Guardrails 1–5 must all exist before any real call is possible (`competitive-analysis.md:1012-1015`), so the call-placing slice (4) is gated behind slices 2–3.

1. **PR 1 — Foundation + docs honesty (no behavior).** `backend/migrations/versions/0006_telephony.py` (`revision="0006_telephony"`, `down_revision="0005_unified_profiles"`, `CREATE TABLE IF NOT EXISTS` up / `DROP TABLE IF EXISTS` down, with the exact DDL above) + `_BASE_SCHEMA` tables + indexes (`core/db.py:38-160`); `backend/services/telephony/config.py` (the `TelephonyState` enum + 5-state resolver + `StatusDict`/`build_status()` + `CARRIER_FIELDS` using `prefs.resolve(...)` + `_truthy()` coercion + memoized `_telephony_available()`) + generalized `set_carrier_secret`/`get_carrier_secret`/`clear_carrier_secret` in `settings_store.py`; `embed_watermark(*, force=)` keyword-only param at `watermark.py:96` (bypass `is_enabled()` only, keep `_check_available()` + error-passthrough) + thread `functools.partial(embed_watermark, force=True)` through the GPU-pool caller at `generation.py:461-463`; `docs/telephony.md` (jurisdiction notice, opt-in explainer, 5-state behavior, carrier setup, host-local cap boundary, emergency/premium denylist, the honest gray-zone language); flip `docs/agentic-voice.md:12-15` + §R1 status. Tests: migration (idempotent + downgrade), gating-5-state (incl. `StatusBody` shape), watermark-force (present/absent/error + non-telephony callers unaffected).
2. **PR 2 — Guardrail enforcement layer (no carrier).** `backend/api/routers/telephony.py` (`APIRouter(prefix="/telephony", dependencies=[Depends(require_loopback)])`, Pydantic `SettingsBody`/`AllowlistAddBody`/`CallBody`, response shapes `StatusBody`/`AllowlistRow`/`PreflightOK`/`CallRow`/`CallLogPage`/`RefusalBody` exactly as pinned) with `/status`, `/settings`, `/allowlist*` (ReDoS-clean E.164 normalize, idempotent add 201/200, 404-on-missing-delete, emergency/premium denylist), `/preflight` (read-only, no row), `/calls` (refusal-precedence, refused-row persistence, 501 when carrier absent, 409 single-call lock, reconciliation sweep on init, per-reason HTTP codes); register in `main.py:742-773` block. `services/telephony/disclosure.py` builder (`_DISCLOSURE` ASCII string table, `disclosure_text(record, locale)`, `en` fallback). `emit("telephony", …)` progress via `event_bus.py:44` (extend `kind` docstring; payload shapes per §SSE/WS events). Tests: guardrails (allowlist, consent-lock + revoke-between-preflight-and-place, cap + timezone + refused-don't-count, blocked-destination, ReDoS-adversarial-input, no-batch, immutable log, reconciliation, single-call-lock, FK/profile-not-found, pagination), disclosure (incl. abort-on-synth-fail, locale fallback).
3. **PR 3 — Frontend Settings → Telephony panel.** `frontend/src/components/settings/TelephonyPanel.jsx` (+ `.test.jsx`) wired into `frontend/src/pages/Settings.jsx`; i18n strings in `frontend/src/i18n/locales/en.json` (all 5 state labels, all 8 refusal reasons, disclosure preview, legal copy — English keys are load-bearing; sibling locales fall back to `en`); 5-state rendering off `StatusBody`, `/status`-failure non-blocking state, empty allowlist/log empty-states, allowlist editor, read-only call log with `watermarked`/`recorded`/status badges, recording toggle gated behind two-party-consent warning, legal-responsibility checkbox; write-only carrier-credential form modeled on `ApiKeysPanel.jsx` posting `SettingsBody.credentials` with `credential_fields_missing` highlighting; live "call in progress" banner off the `telephony` WS events; any non-secret UI cache uses lazy-with-default localStorage reads. `bunx vitest run` + `test_no_hardcoded_cjk.py` green.
4. **PR 4 — Embedded pipecat carrier leg (behind extra).** `backend/services/telephony/pipeline.py` (lazy `pipecat` + carrier serializer, mirroring `mcp_server._ensure_mcp()` at `:31-48`; module-global `_call_lock = asyncio.Lock()`), wire `POST /telephony/calls` to actually place via a `FakeSerializer`-testable pipeline; full call-status state machine (`placing`→…→terminal) with every mid-call failure path mapped to a terminal status + the pinned `telephony` event payloads; first frame → `create_speech`-shaped synthesis (`openai_compat.py:251`, `SpeechRequest`) with `embed_watermark(force=True)`; per-call `watermark_degraded` tracking → `watermarked` flag; single-call lock with `finally` release; `examples/agentic/telephony_outbound.py`. Tests: watermark-on-every-frame (+ absent/degraded), disclosure-first-frame (+ synth-fail abort), fake-carrier end-to-end through every terminal status, concurrent-call rejection. Add the `telephony` extra to `pyproject.toml:114-146` (exact pin; `uv tree` clean).
5. **PR 5 — Prerequisite spikes as tests + docs.** `tests/test_watermark_phoneband.py` (spike b, three documented outcomes, asserts the `detect_watermark` dict shape), `tests/test_telephony_ttfa.py` (spike a, env-gated on `OMNIVOICE_TELEPHONY_BENCH`, CPU `xfail`); record both results in `docs/telephony.md`. README capability line. If `logging_filter.py` is extended for carrier tokens (ReDoS-clean `AC…`/`SK…`/`KEY…`), that lands here or in PR 1.

## Acceptance criteria

- [ ] With **no** carrier credentials and `telephony.enabled` unset (the default for 100% of installs): `state==DISABLED`; `GET /telephony/status` → the DISABLED minimal-form `StatusBody` (`{enabled:false, available:<dep-bool>, state:"DISABLED", …}`); every other `/telephony/*` → 404. `OMNIVOICE_TELEPHONY=0` also yields `DISABLED`. Default install is byte-for-byte unchanged (no new `[project] dependencies`; `uv tree` clean).
- [ ] The 5-state machine is exhaustive and tested: `DISABLED`, `DEPS_MISSING` (501 + restart hint), `NO_CREDENTIALS`, `PARTIAL_CREDENTIALS` (surfaces `credential_fields_missing`), `READY` — each with its defined HTTP behavior and the full `StatusBody` key set.
- [ ] A call from a profile with `verified_own_voice=0` (`core/db.py:52`) is **refused** 403 `reason:"not_verified"` and writes a refused call-log row (guardrail 2). A call from a since-deleted profile → 404 `reason:"profile_not_found"`, **no row** (FK respected).
- [ ] A call to a number **not** on the allowlist is refused 403 `reason:"not_allowlisted"`; an emergency/premium number **on** the allowlist is still refused 422 `reason:"blocked_destination"` (guardrail 4 + safety denylist). The E.164 normalizer (`^\+\d{8,15}$`) is ReDoS-clean (CodeQL py/polynomial-redos passes; adversarial-input bounded-time test green). Allowlist add returns 201 fresh / 200 duplicate; delete-missing → 404.
- [ ] The daily cap counts only carrier-reaching attempts (`status IN ('placing','ringing','in_progress','completed','failed')`) in the **host-local calendar day** (stdlib `time.localtime()`+`mktime`, same on all OSes); refused rows don't consume it; `cap=0` refuses everything; the `cap`-th+1 real attempt → 429 `reason:"cap_exceeded"` (guardrail 4). Refusal-reason precedence is single-valued and deterministic per the §precedence list.
- [ ] **No** batch/list-dial endpoint exists; `POST /telephony/calls` takes exactly one `destination`; `POST /telephony/calls/batch` → 404/405; a second concurrent call → 409 `reason:"call_in_progress"` (guardrail 4, single-call-at-a-time via `_call_lock.locked()`, all test-asserted). `GET /telephony/calls` is paginated (`limit` clamped [1,200]) and never an unbounded dump.
- [ ] The **first** synthesized audio frame of every call is the server-generated disclosure preamble (`disclosure_text(record, locale)`); agent prompt cannot suppress/reorder it; an unknown `locale` falls back to non-empty `en`; a disclosure-synthesis failure aborts the call before connecting (`disclosure_synthesis_failed`), so no human is ever reached without disclosure; preamble text stored in `telephony_calls.disclosure_text` (guardrail 1).
- [ ] **Every** synthesized frame on a call passes through `embed_watermark(..., force=True)` (the new keyword-only param at `watermark.py:96`) regardless of the user's `watermark.invisible` pref; `telephony_calls.watermarked==1` only when AudioSeal is present **and** every frame embedded; AudioSeal absent or any frame failing → `watermarked=0` + `/status.watermark_available:false`, call still completes (guardrail 3, honest provenance). Existing non-telephony callers are unaffected by the param (default `force=False`, positional signature unchanged).
- [ ] Recording is OFF by default (pref + per-call `record:false`); enabling it requires the two-party-consent acknowledgment and adds the recording notice to the spoken disclosure; `recorded=1` is only set on a placed call; docs state "record" = carrier-side + spoken notice, not a locally stored WAV in slice 1 (guardrail 5).
- [ ] Call-log rows are append-only: no app DELETE/PATCH/PUT path; `DELETE /telephony/calls/{id}` → 405; orphaned non-terminal rows are reconciled to `failed`/`interrupted` via UPDATE on init (never deleted). Every call path (success + every mid-call failure: carrier connect, no-answer/busy/rejected, disclosure-synth-fail, mid-call TTS error) ends in a terminal status with `ended_at` set (guardrail 5).
- [ ] Spike (b) `tests/test_watermark_phoneband.py` runs and its G.711 survival result (asserting the `detect_watermark` dict shape) is documented honestly in `docs/telephony.md` (survives / stripped / dep-absent); if the mark does not survive, docs state so and the disclosure preamble is named as the binding marker.
- [ ] Spike (a) TTFA benchmark exists (env-gated on `OMNIVOICE_TELEPHONY_BENCH`), `xfail`s gracefully on CPU/no-GPU, and a measured number appears in `docs/telephony.md`.
- [ ] `0005_unified_profiles → 0006_telephony` upgrade + downgrade tested clean and idempotent; fresh `_BASE_SCHEMA` already has both tables with the pinned columns/indexes; existing `omnivoice_data/` works without manual migration (additive-only, lossless upgrade).
- [ ] All UI + disclosure strings go through i18n (English keys in `en.json`; siblings fall back to `en`); refusal `reason` is a non-localized wire enum; `tests/test_no_hardcoded_cjk.py` passes with **no** new allowlist entries.
- [ ] `docs/telephony.md` (new), `docs/agentic-voice.md:12-15` deferral update, and §R1 status flip land in the same PR series (docs-sync rule); README mentions the opt-in capability; docs cover the cap timezone, denylist, record semantics, restart-after-extra-install, and spike results.
- [ ] Carrier secrets are Fernet-encrypted at rest (generalized `settings_store` helper keyed `carrier_secret.<name>`, with `InvalidToken`/SQLite-error → treated-absent fallbacks), never returned by `/telephony/status` or `/settings`, and not passed to any logger (since `logging_filter.py:25` only redacts `hf_` tokens — verify carrier secrets and carrier error bodies never reach the log pipeline, or extend the filter with a ReDoS-clean carrier-token pattern).
- [ ] CI fully green (backend pytest selection + `bunx vitest run`) before each merge; version files remain at v0.3.6 (no minor bump, no RC); feature ships continuous-to-main.
