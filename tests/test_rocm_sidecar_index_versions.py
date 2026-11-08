from core.torch_indexes import ROCM_INDEX_TAIL_FOR_TORCH


def test_rocm_sidecar_index_mappings_only_include_verified_torch_audio_pairs():
    assert "2.12" not in ROCM_INDEX_TAIL_FOR_TORCH
    assert "2.13" not in ROCM_INDEX_TAIL_FOR_TORCH
    assert {v: ROCM_INDEX_TAIL_FOR_TORCH[v] for v in ("2.7", "2.8", "2.11")} == {
        "2.7": "rocm6.3", "2.8": "rocm6.4", "2.11": "rocm7.2"
    }
