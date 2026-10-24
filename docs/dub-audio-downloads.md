# Dub audio downloads

The WAV (`/dub/download-audio/{job_id}`), MP3 (`/dub/download-mp3/{job_id}`),
and stems (`/dub/export-stems/{job_id}`) endpoints accept an optional `lang`.
When omitted, the first generated track is selected. An explicit language
without a generated track returns HTTP 404; it never substitutes another language.
Generate the requested track before downloading it.
