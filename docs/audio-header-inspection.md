# Audio header inspection

`services.audio_io.audio_info` reports declared fixed-width RIFF/WAVE header
facts so truncated cached WAVs remain detectable. The RIFF scanner reads only
the 16-byte fixed `fmt ` prefix, seeks over optional format extensions and
unrelated chunks, and restores a caller-owned stream to its original position.
A file claiming a very large format extension cannot force a correspondingly
large header allocation. Short format prefixes remain unreadable; declared
frame counts retain their existing meaning rather than silently becoming the
number of bytes physically present in a truncated file.
