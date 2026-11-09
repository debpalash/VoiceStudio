#!/usr/bin/env python3
"""Compile the real portable encoder and verify streaming/provenance without models."""
import ctypes
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from verify_source import SOURCE, verify


class EncoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        verify()
        compiler = shutil.which('clang') or shutil.which('cc')
        if not compiler:
            raise unittest.SkipTest('A C compiler is required')
        cls.scratch = tempfile.TemporaryDirectory(prefix='vs-mp3-test-')
        cls.root = Path(cls.scratch.name)
        library = cls.root / 'libmp3.so'
        subprocess.run([compiler, '-shared', '-fPIC', '-O2', '-DHAVE_CONFIG_H',
                        '-Wno-shift-negative-value', '-Wno-absolute-value', '-Wno-tautological-pointer-compare',
                        '-I', str(SOURCE), '-I', str(SOURCE / 'include'),
                        str(SOURCE.parent / 'Mp3Encoder.c'), *map(str, sorted((SOURCE / 'libmp3lame').glob('*.c'))),
                        '-lm', '-o', str(library)], check=True)
        cls.library = ctypes.CDLL(str(library))
        cls.library.vs_mp3_create.argtypes = [ctypes.c_char_p, ctypes.c_int32, ctypes.c_uint64, ctypes.c_int32]
        cls.library.vs_mp3_create.restype = ctypes.c_void_p
        cls.library.vs_mp3_write.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int32]
        cls.library.vs_mp3_finish.argtypes = [ctypes.c_void_p]
        cls.library.vs_mp3_free.argtypes = [ctypes.c_void_p]

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "scratch"): cls.scratch.cleanup()

    def encode(self, synthetic):
        output = self.root / ('synthetic.mp3' if synthetic else 'human.mp3')
        pointer = self.library.vs_mp3_create(str(output).encode(), 24000, 48000, synthetic)
        self.assertTrue(pointer)
        try:
            for start in range(0, 48000, 8192):
                count = min(8192, 48000-start)
                samples = (ctypes.c_float * count)(*[math.sin((start+i)*2*math.pi*220/24000)*.3 for i in range(count)])
                self.assertEqual(0, self.library.vs_mp3_write(pointer, samples, count))
            self.assertEqual(0, self.library.vs_mp3_finish(pointer))
            self.assertEqual(-1, self.library.vs_mp3_finish(pointer))
        finally:
            self.library.vs_mp3_free(pointer)
        return output

    def test_real_mp3_has_ascii_id3_marker_and_gapless_frame_metadata(self):
        output = self.encode(1)
        data = output.read_bytes()
        self.assertEqual(b'ID3', data[:3])
        self.assertIn(b'VoiceStudio synthetic audio', data)
        self.assertIn(b'Info', data)
        self.assertIn(b'LAME', data)
        if shutil.which('ffmpeg'):
            pcm = subprocess.check_output(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(output),
                                           '-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', '1', '-ar', '24000', '-'])
            self.assertEqual(48000*4, len(pcm))
            samples = (ctypes.c_float * 48000).from_buffer_copy(pcm)
            self.assertGreater(max(samples), .1)

    def test_human_audio_never_gains_synthetic_provenance(self):
        data = self.encode(0).read_bytes()
        self.assertIn(b'VoiceStudio audio', data)
        self.assertNotIn(b'VoiceStudio synthetic audio', data)

    def test_incomplete_nonfinite_and_unbounded_input_cannot_finish(self):
        output = str(self.root / 'invalid.mp3').encode()
        self.assertFalse(self.library.vs_mp3_create(output, 16000, 10, 1))
        self.assertFalse(self.library.vs_mp3_create(output, 24000, 0, 1))
        self.assertFalse(self.library.vs_mp3_create(output, 24000, 345600001, 1))
        pointer = self.library.vs_mp3_create(output, 24000, 10000, 1)
        self.assertTrue(pointer)
        try:
            samples = (ctypes.c_float * 8193)()
            self.assertEqual(-1, self.library.vs_mp3_write(pointer, samples, 8193))
            self.assertEqual(0, self.library.vs_mp3_write(pointer, samples, 8192))
            self.assertEqual(-1, self.library.vs_mp3_finish(pointer))
            samples[0] = float('nan')
            self.assertEqual(-1, self.library.vs_mp3_write(pointer, samples, 1))
            self.assertEqual(-1, self.library.vs_mp3_finish(pointer))
        finally:
            self.library.vs_mp3_free(pointer)


if __name__ == '__main__':
    unittest.main()
