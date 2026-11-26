# Subtitle inputs

POST /dub/validate-srt checks a multipart file without creating a job or running
ASR. A client can validate subtitles before copying a large media file. Invalid
or empty subtitles are rejected and text uploads are limited to 2 MB.

POST /dub/from-srt accepts a multipart file and an explicit source_lang. It
persists an audio-only subtitle job with stable cue IDs, original text and a
duration equal to the final cue end, without extracting media or running ASR.
Both endpoints require administrator authorization in server mode, matching
subtitle replacement. Existing media and import endpoints remain available.

These are backend primitives. The guided desktop workflow that selects media
and subtitles together is a separate change; this PR does not expose new UI.
