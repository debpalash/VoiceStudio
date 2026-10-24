# MP3 export storage

Preserving background audio during MP3 download requires an intermediate WAV.
The WAV and the in-progress MP3 live in a private temporary export directory,
removed when conversion completes, fails, or is cancelled. Only a successfully
encoded MP3 is published in the job exports directory. Source audio and the
prepared background cache remain available for subsequent exports.
