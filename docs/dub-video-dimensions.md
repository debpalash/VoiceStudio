# Dub video dimensions

Smart Fit, Stretch Video, and subtitle burning encode H.264 with 4:2:0 color.
Odd-sized input frames are padded with a black row or column as needed to make
the output dimensions even. Existing pixels and the original framing are kept.
Even-sized video and exports that copy the video stream retain their dimensions.
