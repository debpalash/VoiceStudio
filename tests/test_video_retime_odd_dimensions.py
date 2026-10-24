"""Real screen captures can have odd dimensions; H.264 4:2:0 cannot."""
import json
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("width,height", [(17, 19), (18, 20), (17, 20), (18, 19)])
def test_retime_of_odd_sized_capture_is_encodable_without_cropping(tmp_path, width, height):
    from services.video_retime import build_chunk_filter_graph, VIDEO_ENC_ARGS
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe are needed for the encoder oracle")
    source, output = tmp_path / "screen.mkv", tmp_path / "dub.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={width}x{height}:rate=10:duration=0.5", "-c:v", "ffv1", str(source)], check=True)
    graph, label = build_chunk_filter_graph([(0.0, 0.5, 1.0)], "[0:v]")
    encoded = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(source),
                              "-filter_complex", graph, "-map", label,
                              *VIDEO_ENC_ARGS, str(output)], capture_output=True)
    assert encoded.returncode == 0, encoded.stderr.decode(errors="replace")
    info = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height", "-of", "json", str(output)],
                          capture_output=True, check=True)
    dimensions = json.loads(info.stdout)["streams"][0]
    assert dimensions == {"width": 18, "height": 20}
