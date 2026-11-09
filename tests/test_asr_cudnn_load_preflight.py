"""Direct ASR loads must avoid CUDA when its cuDNN runtime cannot load (#2904)."""
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


class CudnnLoadPreflightTests(unittest.TestCase):
    def setUp(self):
        self.ab = importlib.import_module('services.asr_backend')
        self.device_caps = importlib.import_module('core.device_caps')

    def test_direct_faster_whisper_load_uses_cpu_only_when_cudnn_is_missing(self):
        ab, device_caps = self.ab, self.device_caps
        for cudnn_ok, expected in ((False, 'cpu'), (True, 'cuda')):
            with self.subTest(cudnn_ok=cudnn_ok):
                calls = []

                def load(source, *, device, compute_type):
                    calls.append((source, device, compute_type))
                    return object()

                with patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=load)}), \
                     patch.dict(os.environ, {'ASR_COMPUTE_TYPE': ''}), \
                     patch.object(device_caps, 'detect_host_caps', return_value=SimpleNamespace(family='cuda')), \
                     patch.object(ab, '_cuda_reported_available', return_value=True), \
                     patch.object(ab, '_rocm_torch', return_value=False), \
                     patch.object(ab, '_ctranslate2_cudnn_ok', return_value=(cudnn_ok, 'test')), \
                     patch.object(ab, '_ctranslate2_execstack_ok', return_value=(True, 'ready')), \
                     patch.object(ab, '_local_model_source', return_value='/local/installed-whisper'):
                    backend = ab.FasterWhisperBackend(model_name='installed-whisper')
                    backend._ensure_model()
                    self.assertEqual(calls, [('/local/installed-whisper', expected,
                                             'int8' if expected == 'cpu' else 'float16')])
                    self.assertIsNotNone(backend._model)
                    self.assertEqual(ab.WhisperXBackend._pick_device(),
                                     (expected, 'int8' if expected == 'cpu' else 'float16'))

    def test_cpu_override_never_probes_cuda_libraries(self):
        ab, device_caps = self.ab, self.device_caps
        with patch.object(device_caps, 'detect_host_caps', return_value=SimpleNamespace(family='cpu')), \
             patch.object(ab, '_ctranslate2_cudnn_ok') as probe:
            self.assertFalse(ab._ctranslate2_cuda_ok())
            probe.assert_not_called()

    def test_rocm_never_probes_nvidia_libraries(self):
        ab, device_caps = self.ab, self.device_caps
        with patch.object(device_caps, 'detect_host_caps', return_value=SimpleNamespace(family='cuda')), \
             patch.object(ab, '_cuda_reported_available', return_value=True), \
             patch.object(ab, '_rocm_torch', return_value=True), \
             patch.object(ab, '_ctranslate2_cudnn_ok') as probe:
            self.assertFalse(ab._ctranslate2_cuda_ok())
            probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
