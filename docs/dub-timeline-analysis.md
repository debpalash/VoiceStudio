# Dub timeline energy

The timeline waveform and snap-to-onset markers analyze the strongest channel
at each sample. Opposite-polarity stereo and recordings with one silent channel
therefore retain their audible energy instead of disappearing in a mono mix.
This affects the editor visualization and markers, not the played/exported audio.
Cached analyses from earlier versions are recomputed once when next opened.
