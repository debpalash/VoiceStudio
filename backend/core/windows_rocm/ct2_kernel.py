"""Exercise CT2's own GPU kernels without models, downloads or speech inputs."""


def verify_ct2_kernel(ctranslate2):
    """Round-trip exactly representable values through native GPU FP16 casts.

    Device discovery and host/device copies can succeed when the wheel lacks
    kernels for this GPU. Converting on the GPU executes CT2's device code;
    copying the result back synchronizes it and permits checking the values.
    This establishes model-free kernel execution, not ASR model compatibility.
    """
    import numpy as np

    expected = np.array([1.25, -2.5, 0.0, 8.0], dtype=np.float32)
    gpu = ctranslate2.StorageView.from_array(expected).to_device(ctranslate2.Device.cuda)
    if gpu.device != "cuda":
        raise RuntimeError("CTranslate2 GPU kernel check fell back to CPU")
    converted = gpu.to(ctranslate2.DataType.float16).to(ctranslate2.DataType.float32)
    if converted.device != "cuda":
        raise RuntimeError("CTranslate2 GPU conversion fell back to CPU")
    actual = np.asarray(converted.to_device(ctranslate2.Device.cpu))
    if actual.shape != expected.shape or not np.array_equal(actual, expected):
        raise RuntimeError("CTranslate2 GPU kernel returned incorrect values")
    return actual.tolist()
