"""The CPU capability check must reject old OmniVoice binaries without the scoped API."""
import ctypes
import pathlib
import platform
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="iOS bridge uses Apple's native headers")


def test_cpu_capability_requires_the_scoped_backend_export(tmp_path):
    source = ROOT / "mobile/ios/VoiceStudioKit/Sources/VSNative"
    bridge = tmp_path / "bridge.dylib"
    subprocess.run(["xcrun", "clang", "-dynamiclib", "-std=c11", "-I", str(source / "include"), str(source / "VSNative.c"), "-o", str(bridge)], check=True, capture_output=True)
    libs = []
    for name, extra in [("old", ""), ("scoped", "void *ov_init_with_backend(void *p, const char *b) {return 0;}")]:
        stub = tmp_path / (name + ".c")
        stub.write_text("void *ov_init(void *p) {return 0;}\n" + extra)
        library = tmp_path / (name + ".dylib")
        subprocess.run(["xcrun", "clang", "-dynamiclib", str(stub), "-o", str(library)], check=True, capture_output=True)
        libs.append(str(library).encode())
    native = ctypes.CDLL(str(bridge))
    for function in [native.vs_runtime_available, native.vs_cpu_runtime_available]:
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        function.restype = ctypes.c_int
    assert native.vs_runtime_available(b"omnivoice.cpp", libs[0]) == 1
    assert native.vs_cpu_runtime_available(b"omnivoice.cpp", libs[0]) == 0
    assert native.vs_cpu_runtime_available(b"omnivoice.cpp", libs[1]) == 1
    assert native.vs_cpu_runtime_available(b"omnivoice.cpp", b"/missing-runtime.dylib") == 0
