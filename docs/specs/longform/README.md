# Longform references

For current behavior, start with these guides:

- [Stories and audiobooks](../../electron-longform.md): scripts, imports, rendering,
  mastering, exports, cue sheets and recovery.
- [Projects](../../electron-projects.md): named projects, library and deletion.
- [Gallery](../../electron-gallery.md) and [persona format](../../persona-format.md):
  voice selection, handoffs and portable voice files.
- [Calls](../../calls.md): workflows and opt-in telephony.

The old generated task checklists targeted the removed `frontend/src` tree and
have been retired. This does not declare every proposed feature complete.
Four designs are retained for their distinct routing, rendering, storage and
safety rationale; their snapshots are not current implementation instructions:

| Design reference | Scope |
| --- | --- |
| [GPU compatibility](21-gpu-compat-matrix.md) | Device probing and explicit engine routing |
| [Two-pass ACX mastering](28-two-pass-acx-mastering.md) | Loudness and chapter packaging |
| [Project storage](31-unified-longform-project-store.md) | Persistence, migration and recovery considerations |
| [Phone calls](32-phone-calls.md) | Explicit consent and telephony safety boundaries |
