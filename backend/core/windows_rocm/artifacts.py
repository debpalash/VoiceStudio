"""Download into temporary storage; extract only a verified named wheel."""
import hashlib
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_archive(url, destination, size):
    deadline = time.monotonic() + 1800
    written = 0
    with urllib.request.urlopen(url, timeout=60) as source, Path(destination).open("wb") as target:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > size or time.monotonic() > deadline:
                raise RuntimeError("Windows ROCm archive download exceeded size/time bound")
            target.write(chunk)
    if written != size:
        raise RuntimeError("Windows ROCm archive download was truncated")


def checked_wheel(archive_path, destination, config):
    archive_path = Path(archive_path)
    if sha256(archive_path) != config["archive_sha256"]:
        raise RuntimeError("Windows ROCm archive failed SHA-256 verification")

    member = config["wheel_member"]
    member_path = PurePosixPath(member)
    if member_path.is_absolute() or ".." in member_path.parts or "\\" in member:
        raise RuntimeError("Windows ROCm wheel member has an unsafe path")
    output = Path(destination) / member_path.name

    with zipfile.ZipFile(archive_path) as archive:
        matches = [entry for entry in archive.infolist() if entry.filename == member]
        if len(matches) != 1 or matches[0].is_dir():
            raise RuntimeError("Windows ROCm archive must contain exactly one matching wheel file")
        entry = matches[0]
        if entry.file_size > 200 * 1024 * 1024:
            raise RuntimeError("Windows ROCm wheel exceeds the extraction size bound")
        try:
            with archive.open(entry) as source, output.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
        except BaseException:
            output.unlink(missing_ok=True)
            raise

    if sha256(output) != config["wheel_sha256"]:
        output.unlink(missing_ok=True)
        raise RuntimeError("Windows ROCm wheel failed SHA-256 verification")
    return output
