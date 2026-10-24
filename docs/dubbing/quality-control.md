# Dub quality scoring

The optional backend `POST /dub/qc/{job_id}` pass compares the selected dubbed
track's text with a second speech-to-text transcription. It annotates lines
with drift scores, verification flags and measured timing; it preserves the
generated and recognized text.

Canonically equivalent Unicode spellings score identically. For example, a
letter stored with a separate combining accent matches the same precomposed
letter returned by transcription. This comparison preserves real accent and
word differences and does not fold compatibility characters such as ligatures.

The pass requires an installed speech-to-text model. Advanced QC controls
remain outside the current Electron UI parity coverage.
