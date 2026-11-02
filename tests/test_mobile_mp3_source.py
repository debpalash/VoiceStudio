"""The mobile MP3 encoder is source-built from the same reviewed pin on both phones."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mobile_mp3_upstream_sources_and_packaged_licence_are_pinned():
    path = ROOT / "mobile/native/mp3/verify_source.py"
    spec = importlib.util.spec_from_file_location("mobile_mp3_pin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify()
    original = (module.SOURCE / "COPYING").read_text(encoding="utf-8")
    for path in (
        ROOT / "mobile/android/app/src/main/assets/licenses/lame.txt",
        ROOT / "mobile/ios/VoiceStudio/Resources/NativeLicences/lame.txt",
    ):
        text = path.read_text(encoding="utf-8")
        assert original in text
        assert module.ARCHIVE_SHA256 in text
        assert "mobile/native/mp3/README.md" in text


def test_both_mobile_builds_compile_the_same_encoder_sources():
    package = (ROOT / "mobile/ios/VoiceStudioKit/Package.swift").read_text(encoding="utf-8")
    cmake = (ROOT / "mobile/native/android/CMakeLists.txt").read_text(encoding="utf-8")
    assert 'name: "VSLame"' in package and 'dependencies: ["VSLame"]' in package
    assert 'Sources/VSNative/Lame' in package
    assert 'ios/VoiceStudioKit/Sources/VSNative' in cmake
    assert 'Lame/libmp3lame/*.c' in cmake and 'Mp3Encoder.c' in cmake
    assert 'HAVE_CONFIG_H' in package and 'HAVE_CONFIG_H' in cmake


def test_ios_current_notices_override_cached_framework_copies(tmp_path):
    spec = importlib.util.spec_from_file_location("mobile_resource_notices", ROOT / "mobile/ios/scripts/prepare_resources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    current, cached, output = (tmp_path / name for name in ("current", "cached", "bundle"))
    current.mkdir(); cached.mkdir()
    (current / "lame.txt").write_text("current encoder notice", encoding="utf-8")
    (cached / "lame.txt").write_text("stale encoder notice", encoding="utf-8")
    (cached / "framework-only.txt").write_text("framework notice", encoding="utf-8")
    module.copy_native_notices(current, cached, output)
    assert (output / "lame.txt").read_text(encoding="utf-8") == "current encoder notice"
    assert (output / "framework-only.txt").read_text(encoding="utf-8") == "framework notice"
