# Electron batch dubbing

Open Batch dubbing from the cloning sidebar or command search. Add video files,
choose one or more target languages, optionally select a saved voice, and choose
whether to preserve background audio. Add to Queue submits through the existing
backend. Successfully submitted files leave the upload list; failed files remain.
The backend enforces ASR readiness and owns translation, generation and mixing.
The Electron setup sidebar accepts file picking or drag-and-drop, groups media,
languages and voice/audio choices into stable cards, and exposes the same Add Videos
action from the empty job view.

Active, Completed and Failed views poll backend jobs. Progress shows backend stages
and percentages. Active jobs can be cancelled; finished records require an inline
confirmation before deletion. Completed language outputs use the native save dialog.
Reloading the renderer reads existing jobs and never re-enqueues them. Backend
process restart recovery is not established by this behavior: the batch queue is
in memory. Native watch folders detect settled files through a capability-scoped
directory handle and stream multipart uploads directly to the selected local or
HTTPS remote backend. Remote uploads receive the scoped session from Electron main;
the renderer never handles it.

Verification: node electron/tests/batch-smoke.mjs uses mocked jobs to check file
selection, enqueue, progress, renderer reload, cancellation, export availability
and confirmed deletion. The enqueue unit regression covers partial failure and
language/voice fields. The native helper smoke verifies confined streaming,
authorization replacement, rename detection and revocation. No test yet establishes
real model-backed batch completion or a separate remote-machine transfer.

Retry and deletion reserve the same job while admission or file cleanup is in progress. Concurrent actions return 409 instead of queuing a second attempt or removing its upload. Active jobs must be cancelled and finish stopping before their records and files can be deleted; settled terminal jobs retain normal deletion.
