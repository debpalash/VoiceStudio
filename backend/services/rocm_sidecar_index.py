"""Select exact ROCm wheels while respecting a user-supplied index."""
from core.torch_indexes import PYTORCH_ROCM_INDEX_URL, ROCM_INDEX_TAIL_FOR_TORCH


def rocm_torch_args(index: str, torch_version: str, audio_version: str, *, custom: bool):
    """Return bounded wheel pins, or None when their ROCm version is unknown."""
    tail = ROCM_INDEX_TAIL_FOR_TORCH.get(".".join(torch_version.split(".")[:2]))
    if tail is None:
        return None
    if not custom:
        index = PYTORCH_ROCM_INDEX_URL.rsplit("/", 1)[0] + "/" + tail
    return ["--no-sources", f"torch=={torch_version}+{tail}",
            f"torchaudio=={audio_version}+{tail}", "--extra-index-url", index,
            "--index-strategy", "unsafe-best-match"]


# Archive + sum of uncompressed ZIP entries, rounded up to GiB, from the
# Linux x86_64 CPython 3.11 wheels published at download.pytorch.org/whl/.
# Measured 2026-11-07: rocm6.3/torch 2.7.0 = 25.101 GiB,
# rocm6.4/torch 2.8.0 = 14.873 GiB, rocm7.2/torch 2.11.0 = 18.999 GiB.
# This conservative peak allows for both the download and an unpacked copy;
# custom wheels and cache files on other volumes can have different sizes.
_TORCH_WHEEL_PEAK_GIB = {"2.7.0": 26, "2.8.0": 15, "2.11.0": 19}


def rocm_minimum_install_bytes(required_bytes: int, preserved_bytes: int, version: str | None) -> int:
    """Estimate source/model space plus the selected Torch wheel's peak."""
    wheel_gib = _TORCH_WHEEL_PEAK_GIB.get(version, max(_TORCH_WHEEL_PEAK_GIB.values()))
    return max(0, required_bytes - preserved_bytes) + wheel_gib * 1024**3
