# Optional background separation

POST /dub/upload accepts separate_background (default true for existing clients).
A voice-only client sends false to skip Demucs and the extra stereo extraction.
The original voice is replaced; music/ambience cannot be preserved separately.
Leave the option on to retain a separated background bed.

Preparation records skipped separately from failed separation and emits
 demucs_done with skipped=true and has_bg=false. Cache identities separate the
two modes so voice-only preparation cannot reuse stems, or vice versa. Choosing
separation retains the existing best-effort fallback and warning on failure.
