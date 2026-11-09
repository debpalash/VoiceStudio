"""Validate the separately pinned CT2 artifact before any package mutation."""
import re
from pathlib import PurePosixPath

_SOURCES = frozenset({
    "https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Windows.zip",
    "https://github.com/dutch2005/VoiceStudio/releases/download/ct2-rocm-4.8.4-9a7a074/rocm-python-wheels-Windows.zip",
})


def validate_ct2_recipe(config):
    """Reject unapproved sources, unsafe members and incomplete integrity pins."""
    if config.get("archive_url") not in _SOURCES:
        raise RuntimeError("Windows ROCm CT2 recipe has an unapproved source")
    if type(config.get("archive_size")) is not int or not 0 < config["archive_size"] <= 512 * 1024**2:
        raise RuntimeError("Windows ROCm CT2 recipe has an invalid archive size")
    for field in ("archive_sha256", "wheel_sha256", "binary_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(config.get(field, ""))):
            raise RuntimeError("Windows ROCm CT2 recipe has an invalid sha256 pin")
    member = config.get("wheel_member", "")
    path = PurePosixPath(member)
    expected = f"ctranslate2-{config['version']}-cp312-cp312-win_amd64.whl"
    if path.is_absolute() or ".." in path.parts or "\\" in member or path.name != expected:
        raise RuntimeError("Windows ROCm CT2 recipe has an unsafe wheel member")
