"""Select exact ROCm wheels while respecting a user-supplied index."""
from urllib.parse import urlsplit

from core.torch_indexes import PYTORCH_ROCM_INDEX_URL, ROCM_INDEX_TAIL_FOR_TORCH


def rocm_torch_args(index: str, torch_version: str, audio_version: str, *, custom: bool):
    """Return bounded wheel pins, or None when their ROCm version is unknown."""
    tail = ROCM_INDEX_TAIL_FOR_TORCH.get(".".join(torch_version.split(".")[:2]))
    if tail is None:
        return None
    if custom:
        path_tail = urlsplit(index).path.rstrip("/").rsplit("/", 1)[-1]
        tag = path_tail if path_tail.startswith("rocm") else tail
    else:
        index = PYTORCH_ROCM_INDEX_URL.rsplit("/", 1)[0] + "/" + tail
        tag = tail
    return ["--no-sources", f"torch=={torch_version}+{tag}",
            f"torchaudio=={audio_version}+{tag}", "--extra-index-url", index,
            "--index-strategy", "unsafe-best-match"]


def rocm_minimum_install_bytes(required_bytes: int, preserved_bytes: int) -> int:
    """Reserve the additional space needed by a large ROCm Torch installation."""
    return max(0, required_bytes - preserved_bytes) + 21 * 1024**3
