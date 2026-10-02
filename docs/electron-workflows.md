# Electron Workflows

Workflows run one saved pipeline over many inputs. A workflow is a canvas of
steps joined by connections; the list it runs over comes from the input step —
either scripts (one per `---` separator, or imported files) or uploaded audio
clips. Up to 50 items per run, each script at most 20,000 characters and each
clip at most 64 MiB. Drafts, the workflow library, duplication and deletion live
in browser storage; generated audio and uploaded media are held separately and
released when the last workflow referencing them is removed.

The executable steps are the input (**Start** or **Audio**), **Speak**,
**Convert**, **Transcribe**, **Translate**, **Normalize**, **Condition** and
**End**. **Agent** and **Call** remain local drafts: they can be placed on the
canvas, but a workflow containing one does not run. Steps are contract-checked
before anything executes — text reaches translation or speech, audio reaches
transcription or conversion, and generated speech reaches normalization — so an
incompatible draft is refused up front rather than part-way through a run.

A **Condition** routes each item down one of two branches. It compares the text
reaching it against the phrase you enter, using Contains, Is exactly, Starts
with or Ends with; capitals and surrounding blanks are ignored on both sides, so
a draft takes the same branch on every machine. The `yes` branch runs on a
match, the `no` branch otherwise. Both handles must be connected, only a
condition may fork, and a condition placed where audio rather than text arrives
is rejected. Branches may rejoin, but the paths meeting at a shared step must
carry the same kind of value — one branch speaking while the other passes text
straight through is refused, because the shared step would receive a different
kind of value depending on the item.

Each item walks the graph on its own, so a single run legitimately sends one
clip down the `yes` branch and the next down `no`, using a different voice for
each. Conditions are re-evaluated rather than recorded: the text they test is
itself checkpointed, so resuming an interrupted run re-reads the same value and
takes the same branch.

Execution is serial, which avoids competing model loads, and every completed
step is a durable checkpoint. Stopping a run keeps the work already finished;
starting again resumes from the last checkpoint instead of regenerating it, and
a failed item stops the run for explicit retry rather than being skipped. Saved
progress is tied to the executable settings — voices, languages, speeds,
normalization targets, condition phrases and the connections themselves — so
moving a branch invalidates a finished run, while moving a node on the canvas or
renaming a step does not.

Finished items expose their own playback and an explicit save; text output is
saved as `.txt` and audio as `.wav` through the native save dialog. Nothing is
downloaded automatically.

A branch that goes directly from a Condition to End keeps the incoming text as
its exportable result. Conditions have one phrase editor; the generic Instructions
field is hidden for those steps.

Native saves stage the complete replacement in the selected destination directory before replacing an existing export. A write or replacement failure leaves the previous file intact. Existing exports must pass a non-truncating write-authorization check before replacement. Existing POSIX permission bits and working symbolic links are preserved; a dangling symbolic link is left unchanged and reports a filesystem error. Atomic replacement requires write permission on the parent directory, even when the existing file is writable. It replaces the original file inode: existing ACLs, ownership, and relationships to other hard links are not preserved. This does not provide a power-loss recovery guarantee.
