"""Immutable Hugging Face revisions for VoiceStudio's curated repositories.

Branch names are mutable supply-chain inputs. Every repo the product offers is
resolved here to a reviewed commit SHA; download, preflight, and repair paths
must call :func:`revision_for` instead of following ``main``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")

CURATED_REVISIONS: dict[str, str] = {
    "facebook/nllb-200-distilled-600M": "f8d333a098d19b4fd9a8b18f94170487ad3f821d",
    "k2-fsa/OmniVoice": "c5fdb5ccb189668d56333f77ba2629f4cd7535f4",
    "audio-cpp/audio.cpp-gguf": "dc6fecccc2b0c6bdda0a8b2f38fa61394fee0b9c",
    "Systran/faster-whisper-large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    "mlx-community/whisper-large-v3-mlx": "49e6aa286ad60c14352c404340ded53710378a11",
    "mlx-community/whisper-large-v3-turbo": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
    "openai/whisper-large-v3": "06f233fe06e710322aca913c1bc4249a0d71fce1",
    "mlx-community/whisper-tiny-mlx": "6caf9c55601caafbe6508a8b0d216bdf4783c4e8",
    "deepdml/faster-whisper-large-v3-turbo-ct2": "4df90f75321148c3a29a9e2351b7ddf8f5b115a8",
    "Systran/faster-distil-whisper-large-v3": "c3058b475261292e64a0412df1d2681c06260fab",
    "Systran/faster-whisper-medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
    "Systran/faster-whisper-small": "536b0662742c02347bc0e980a01041f333bce120",
    "Systran/faster-whisper-base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "nvidia/parakeet-tdt-0.6b-v3": "541d1f99c6b0c3cd0b11a95167540bb8edefd82b",
    "nvidia/parakeet-tdt-0.6b-v2": "ae9ad07059c7c739ffaf932226a8fe64ae2620b0",
    "mlx-community/parakeet-tdt-0.6b-v3": "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15",
    "UsefulSensors/moonshine-base": "7a73d8d55ac0ba2ef3ae761593f6784b51f96dcf",
    "UsefulSensors/moonshine-tiny": "390624ed33d594443aa4aa221f5b9f283b545b5a",
    "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8": "2bda32ec70b097a55adaa07d9a7173915b43cc78",
    "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8": "1ab9323565ddb038682214b292f588070a538ce2",
    "csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20": "98590b7ed6443e77b714204da2757d75e1a642f4",
    "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en": "8e40c43232a1c5c66c82111efc5820d3accca11b",
    "csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17": "d42f2d9f7ca24806fb667456a18a9f1b60f70d16",
    "csukuangfj/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23": "204ad334e2e683fd295359930cc16fc0432a23ac",
    "csukuangfj/sherpa-onnx-whisper-tiny": "65176e2deb88badc814a94058666cadccc29b61c",
    "pyannote/speaker-diarization-3.1": "84fd25912480287da0247647c3d2b4853cb3ee5d",
    "pyannote/segmentation-3.0": "e66f3d3b9eb0873085418a7b813d3b369bf160bb",
    "pyannote/wespeaker-voxceleb-resnet34-LM": "837717ddb9ff5507820346191109dc79c958d614",
    "OpenMOSS-Team/MOSS-TTS-Nano-100M": "44502f80dbf9743528fa921cc544d662c685ebec",
    "KittenML/kitten-tts-mini-0.8": "c02725660cea441db4c383af69f1f26f5cd00947",
    "openbmb/VoxCPM2": "bffb3df5a29440629464e5e839f4d214c8714c3d",
    "FunAudioLLM/Fun-CosyVoice3-0.5B-2512": "29e01c4e8d000f4bcd70751be16fa94bf3d85a18",
    "lj1995/GPT-SoVITS": "336b2ec4e8d4ac74740798dd40af44e74659ecaf",
    "mlx-community/Kokoro-82M-bf16": "a71e4d38b236d968966a2002c4c895dbd12b1c3c",
    "mlx-community/csm-1b-8bit": "fcf0cc857eade3615a60f30722cf5197d4f88406",
    "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit": "5c390979e4b93af5f2932f90742ca99c7dd04687",
    "mlx-community/Dia-1.6B": "de4fa8c178ca5cc4e9d884b55b03fcfaa0995162",
    "mlx-community/Llama-OuteTTS-1.0-1B-4bit": "3ac2cff406f7de16a3216c60d0108571a916acc0",
    "mlx-community/Chatterbox-TTS-4bit": "a3c8ded2d711d6395410d645b3a97c79fd563a13",
    "mlx-community/MeloTTS-English-v3-MLX": "837d15fd72bc35a15033234ce5ea242367ca1960",
    "OpenMOSS-Team/MOSS-TTS-v1.5": "cdd3b911b1585e3f2dbc7775ef10f9926f58850a",
    "eustlb/higgs-audio-v2-tokenizer": "528e871c2a26c4f0f7773b9754e2e1acae20899d",
}


def revision_for(repo_id: str) -> str:
    """Return the immutable revision for a curated repo, or raise."""
    try:
        return CURATED_REVISIONS[repo_id]
    except KeyError as exc:
        raise ValueError(f"No reviewed revision is pinned for {repo_id!r}") from exc


def _repo_dir(repo_id: str, cache_dir: str) -> Path:
    return Path(cache_dir) / ("models--" + repo_id.replace("/", "--"))


def remember_revision(repo_id: str, revision: str, cache_dir: str) -> None:
    """Persist the exact installed revision for later in-place repair."""
    if not _SHA.fullmatch(revision):
        raise ValueError("Hugging Face revision must be a 40-character commit SHA")
    repo_dir = _repo_dir(repo_id, cache_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    marker = repo_dir / "voicestudio-revision"
    temporary = marker.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(revision + "\n", encoding="ascii")
    os.replace(temporary, marker)


def installed_revision(repo_id: str, cache_dir: str) -> str:
    """Return VoiceStudio's recorded revision, falling back to the curated pin."""
    # Authenticate the repository before consulting attacker-writable cache
    # metadata.  A syntactically valid marker must never authorize repair of a
    # repository outside VoiceStudio's reviewed catalog.
    curated_revision = revision_for(repo_id)
    repo_dir = _repo_dir(repo_id, cache_dir)
    # New installs write the first marker. ``refs/main`` preserves the commit
    # resolved by older VoiceStudio/huggingface_hub installs, so upgrades repair
    # the bytes the user actually installed rather than silently changing them.
    for marker in (repo_dir / "voicestudio-revision", repo_dir / "refs" / "main"):
        try:
            revision = marker.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if _SHA.fullmatch(revision):
            return revision
    return curated_revision
