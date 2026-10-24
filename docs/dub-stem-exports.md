# Dub stem exports

`GET /dub/export-stems/{job_id}?lang=de` produces a ZIP containing the selected
vocal track and, when separation is available, `background_original.wav`.
For Smart Fit or Stretch Video exports, the background follows the same retiming
plan as the vocal track, so both stems align when imported at time zero in a DAW.
Tracks without a retiming plan retain the separated background unchanged.
Without background separation the ZIP contains only the vocal stem.

Timing follows the selected language track even if another language was generated
later with a different strategy. Older tracks without a saved strategy retain
the job-level timing fallback.
