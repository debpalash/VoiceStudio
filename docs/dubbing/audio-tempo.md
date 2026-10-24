# Pitch-preserving audio fitting

Dubbing uses local FFmpeg `atempo` stages to fit generated speech into a slot.
Each stage stays between 0.5 and 2.0. The combined tempo ratio must be finite
and positive; zero, negative and non-finite values are rejected before building
the filter. Empty generated audio is rejected before launching FFmpeg, allowing
the caller to report the failed render instead of hanging the event loop.
