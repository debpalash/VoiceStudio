"""Pinned native notices survive a Windows-style Git checkout byte for byte."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pinned_native_notices_survive_autocrlf_checkout(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "core.autocrlf", "true")
    git("config", "core.safecrlf", "false")
    shutil.copy2(ROOT / ".gitattributes", tmp_path / ".gitattributes")
    notices = []
    for directory, manifest, prefix in [
        ("mobile/ios/VoiceStudio/Resources/NativeLicences", "sources.json", ""),
        ("mobile/android/app/src/main/assets/licenses", "native-sources.json", "native-"),
    ]:
        relative = Path(directory)
        shutil.copytree(ROOT / relative, tmp_path / relative)
        for entry in json.loads((ROOT / relative / manifest).read_text(encoding="utf-8")):
            notices.append((relative / (prefix + entry["name"] + ".txt"), entry["notice_sha256"]))
    git("add", ".")
    for path, _ in notices:
        (tmp_path / path).unlink()
    git("checkout", "--", *(path.as_posix() for path, _ in notices))
    for path, expected in notices:
        assert hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() == expected, str(path)
