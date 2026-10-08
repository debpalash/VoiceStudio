# Reject incomplete float wav references

Persisted WAV validation rejects incomplete declared RIFF payloads before a decoder can silently shorten their frame count.

## Contract

Walk RIFF/RIFX chunk headers with fixed-size reads and check each declared payload against the file and container bounds before attempting PCM or SoundFile decoding.

The existing channel/rate/frame bounds and bounded decoder reads remain. This does not expand the validator to additional containers or validate arbitrary media formats. Full application/ML and native platform smoke tests await hosted CI.

## Regression coverage

The public helper regression is in `tests/test_voice_float_wav.py`.
Run it with `python -m pytest -q tests/test_voice_float_wav.py` from the repository root.
It uses local text, files or child processes; no model generation is required.
