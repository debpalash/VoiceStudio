# Planned speech generation and fixed SRT windows

POST /dub/plan/{job_id} resolves the same inputs as generation and counts new,
reusable and silent segments without synthesis, publication or recovery-file
mutation. Generation refuses, before any synthesis, a media cue of 0.05 s or
less that holds words (it cannot be spoken and would otherwise fail only after
the whole render). Under fixed windows, overlapping cues and cues past the end
of the source are refused instead. Each refusal names the cue numbers to fix.
Explicit regeneration takes precedence over reuse.

Completed natural takes are retained privately with integrity checks. Both
private checkpoint keys and upstream crash-resume require immutable model proof,
resolved voice content, synthesis settings and provenance policy. An active
interrupted-render journal takes precedence; a corrupt entry cannot be masked
by a second private copy. Existing explicit partial-render behavior is retained.
Read-only planning verifies recovery payloads without discarding invalid files.

The generation stream emits immutable completed-take audition URLs and saved /
reused counts. Auditioning a completed take never invokes TTS. Published tracks
and fingerprints remain atomic on error, cancellation or changed source cues.

The optional narration strategy accepts original caption cues separately from
speech blocks linked by source_ids. Every linked block must retain its original
SRT start/end envelope and blocks must not overlap. Narration audio, and
SRT-only audio once overflow rescue is opted in, is placed within those exact
windows, using pitch-preserving compression when needed. Later blocks never slide to compensate for an overlong phrase. Natural
cached takes retain every word; acceleration above 1.5x needs listening review.
There is no automatic text rewrite or promise of natural speech at any rate.

For ordinary Concise/Smart Fit, overflow rescue is opt-in: auto_fit_overflow
(default false) fits the entire take with pitch-preserving compression instead
of failing. Left off, an overlong take keeps the blocking timing error, and
Concise reports it as soon as that take is rendered rather than at the end of
a long job. Per-language speech manifests drive preview,
QC and clip export while subtitle exports retain the separate captions.
