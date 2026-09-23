"""Phone-call integrations (Twilio Media Streams).

- ``audio``   — 8 kHz resampling and G.711 μ-law in numpy
- ``twilio``  — the Twilio provider adapter (signatures, TwiML, frames)
- ``session`` — provider-agnostic call loop, tokens, limits, call log
- ``config``  — persisted settings (auth token encrypted, default off)
- ``gateway`` — the separate loopback listener a public tunnel targets
- ``calls``   — call-agent sessions: placing calls, records, live events
- ``agent``   — the conversational responder (VAD → ASR → LLM → TTS)
"""
