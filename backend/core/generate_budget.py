"""Pure length-scaling arithmetic for the per-job generate compute budget.

Stdlib only, so ``services.model_manager`` (the real budget), the torch-free
``worker.deadlines`` fallback and the standalone ``mcp_server`` client timeout
all share ONE definition instead of each re-deriving it. They drifted apart
before (#1190/#1202), and a drifted copy means a tool or remote worker giving
up before the backend does.

Two scaling classes:

* **Accelerated / explicit** — the long-standing rule: the configured floor plus
  1 s per 40 characters past a 1200-character free allowance.
* **CPU, default budget** (#2609) — a CPU render is routinely 10-50x slower than
  realtime-on-GPU, so a flat 600 s floor plus a negligible length term
  abandoned healthy renders of an ordinary paragraph (a 6-core Ryzen on
  OmniVoice hit the 600 s wall with the worker still computing). The default
  CPU budget now grows with the input at ``CPU_SECONDS_PER_CHAR``, bounded by
  ``CPU_AUTO_CAP_S`` so a genuinely wedged engine is still caught in finite
  time. An EXPLICIT user setting never takes this path — it stays authoritative.
"""
from __future__ import annotations

#: Free character allowance before the legacy length bonus starts.
FREE_CHARS = 1200
#: Legacy length bonus: one extra second per this many characters.
CHARS_PER_SECOND = 40.0

#: Default CPU budget growth, seconds of compute allowed per input character.
CPU_SECONDS_PER_CHAR = 4.0
#: Hard ceiling for the AUTOMATIC CPU budget (2 h). Bounds a wedged job; a user
#: who needs longer sets the CPU budget explicitly in Settings.
CPU_AUTO_CAP_S = 7200.0


def length_bonus_s(chars: int) -> float:
    """Legacy bonus seconds for ``chars`` characters of input."""
    return max(0, int(chars) - FREE_CHARS) / CHARS_PER_SECOND


def cpu_auto_budget_s(floor: float, chars: int) -> float:
    """Default CPU execution budget for ``chars`` characters of input.

    Precedence: the automatic ceiling wins. Below it the result is never under
    the legacy ``floor + length bonus`` (so it is only ever more generous than
    before); the ceiling ``max(CPU_AUTO_CAP_S, floor)`` is applied to the FINAL
    value, so even a 500k-character single-shot job is bounded and a wedged
    engine is caught in finite time. Only automatic budgets are capped — an
    explicit user setting never reaches this function.
    """
    n = max(0, int(chars))
    legacy = floor + length_bonus_s(n)
    scaled = CPU_SECONDS_PER_CHAR * n
    return min(max(legacy, scaled), max(CPU_AUTO_CAP_S, floor))


def automatic_cpu_ceiling_s(floor: float) -> float:
    """The most the AUTOMATIC CPU budget can ever grant, whatever the text."""
    return max(CPU_AUTO_CAP_S, float(floor))


#: Clients (the MCP tools, the desktop UI backstop) see the text as the USER
#: typed it, but the backend budgets it after number normalization,
#: pronunciation rules and inline overrides. Expansion is unbounded in
#: principle (measured: a six-digit number grows ~11x, and pronunciation rows
#: are arbitrary), so a client can NOT derive the automatic CPU budget from the
#: raw length. It uses the ceiling instead (see ``client_execution_budget_s``).
#: This factor sizes the legacy length bonus (explicit budgets, GPU hosts): it
#: must stay above what ``normalize_for_tts`` can do to ordinary text — the
#: worst case measured is a six-digit number at ~11.5x — and
#: ``test_cpu_generate_budget_scaling_2609`` fails if the normalizer ever
#: exceeds it. Pronunciation rules are user-defined and not bounded by it.
TEXT_EXPANSION_FACTOR = 16


def client_execution_budget_s(
    floor: float, chars: int, *, cpu_auto_possible: bool = True,
) -> float:
    """Execution budget a client must wait for ``chars`` raw characters — never
    shorter than what ``model_manager.generate_timeout_s`` can grant.

    THE shared function for client waits (``mcp_server``; the TypeScript
    ``generateAbortMs`` mirrors it and is held equal by tests). ``floor`` is the
    largest execution base the backend could apply.

    When the default CPU budget can apply (``cpu_auto_possible``; False once the
    user sets the CPU budget explicitly) the answer is the automatic CEILING
    regardless of text length: the backend's grant depends on a length the
    client cannot see, and a wait that is too short aborts a render that is
    still inside its budget. Waiting longer than needed is harmless — the
    backend answers as soon as it finishes. Otherwise only the legacy length
    bonus applies, sized with ``TEXT_EXPANSION_FACTOR`` headroom.
    """
    n = max(0, int(chars)) * TEXT_EXPANSION_FACTOR
    legacy = floor + length_bonus_s(n)
    if cpu_auto_possible:
        return max(legacy, automatic_cpu_ceiling_s(floor))
    return legacy
