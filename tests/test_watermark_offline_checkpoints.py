"""Model-free coverage of the AudioSeal cache/load boundary; no app imports.

Only the relevant production functions are compiled from their AST. File
presence is simulated in memory: these tests never create checkpoint bytes.
"""
from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch


GENERATOR = "audioseal_wm_16bits"
DETECTOR = "audioseal_detector_16bits"
URL = "https://huggingface.co/facebook/audioseal/resolve/main/"
GENERATOR_FILE = "0f195d476dd87ca1bd7b09e6"
DETECTOR_FILE = "94c8df0b1d5ea8e45af4c884"


def _source_text():
    return (Path(__file__).resolve().parents[1] / "backend/services/watermark.py").read_text(
        encoding="utf-8"
    )


def _load_boundary(source):
    names = {
        "_GeneratorLoadDeferred", "_get_generator", "_get_detector",
        "_audioseal_checkpoint_path", "_require_audioseal_checkpoint_if_offline",
        "_generator_checkpoint_cached", "prefetch_generator", "embed_watermark",
    }
    nodes = [node for node in ast.parse(source).body if getattr(node, "name", "") in names]
    tree = ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    ), *nodes], type_ignores=[])
    module = types.ModuleType("watermark_boundary")
    module.__dict__.update(
        Path=Path, os=os, time=time, logger=logging.getLogger("watermark_boundary_test"),
        _generator=None, _detector=None, _generator_failed_at=None,
        _last_used=0.0, _prefetched_unused=False, _GENERATOR_RETRY_SECONDS=600.0,
        _generator_lock=threading.Lock(), _detector_lock=threading.Lock(),
        is_enabled=lambda: True, _check_available=lambda: True, will_mark=lambda: True,
        torch=types.SimpleNamespace(no_grad=lambda: lambda function: function),
    )
    exec(compile(ast.fix_missing_locations(tree), "<watermark-boundary>", "exec"), module.__dict__)
    return module


class AudioSealOfflineCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.wm = _load_boundary(_source_text())
        self.root = Path("unit-audioseal-cache")
        self.present = set()
        self.card = {
            GENERATOR: {"checkpoint": URL + "generator_base.pth", "nbits": 16},
            DETECTOR: {"checkpoint": URL + "detector_base.pth", "nbits": 16},
        }
        self.config_loader = Mock(side_effect=self.card.get)
        self.generator = types.SimpleNamespace(eval=Mock())
        self.detector = types.SimpleNamespace(eval=Mock())
        self.generator_loader = Mock(return_value=self.generator)
        self.detector_loader = Mock(return_value=self.detector)
        package = types.ModuleType("audioseal")
        package.AudioSeal = types.SimpleNamespace(
            load_generator=self.generator_loader, load_detector=self.detector_loader,
        )
        loader = types.ModuleType("audioseal.loader")
        loader.load_local_model_config = self.config_loader
        self.enterContext(patch.dict(sys.modules, {"audioseal": package, "audioseal.loader": loader}))
        self.enterContext(patch.dict(os.environ, {"AUDIOSEAL_CACHE_DIR": str(self.root)}, clear=True))
        present = self.present
        self.enterContext(patch.object(Path, "is_file", lambda candidate: candidate in present))

    def test_offline_generator_miss_never_reaches_url_constructor(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        with self.assertRaises(FileNotFoundError):
            self.wm._get_generator()
        self.generator_loader.assert_not_called()
        self.assertIsNotNone(self.wm._generator_failed_at)

    def test_offline_detector_miss_never_reaches_url_constructor(self):
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        with self.assertRaises(FileNotFoundError):
            self.wm._get_detector()
        self.detector_loader.assert_not_called()

    def test_prefetch_recognizes_installed_https_cache_filename(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        self.present.add(self.root / "audioseal" / GENERATOR_FILE)
        self.wm.prefetch_generator()
        self.generator_loader.assert_called_once_with(GENERATOR)
        self.assertTrue(self.wm._prefetched_unused)

    def test_both_offline_flags_and_hf_true_values(self):
        for flag in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            for value in ("1", "ON", "yes", "True"):
                with self.subTest(flag=flag, value=value), patch.dict(os.environ, {flag: value}):
                    with self.assertRaises(FileNotFoundError):
                        self.wm._get_detector()
        self.detector_loader.assert_not_called()

    def test_cached_models_keep_named_cards_cpu_defaults_and_eval(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        self.present.update(self.root / "audioseal" / name for name in (GENERATOR_FILE, DETECTOR_FILE))
        self.assertIs(self.wm._get_generator(), self.generator)
        self.assertIs(self.wm._get_detector(), self.detector)
        self.generator_loader.assert_called_once_with(GENERATOR)
        self.detector_loader.assert_called_once_with(DETECTOR)
        self.generator.eval.assert_called_once_with()
        self.detector.eval.assert_called_once_with()
        self.assertIsNone(self.wm._generator_failed_at)

    def test_online_load_does_not_require_card_or_cache(self):
        os.environ.update(HF_HUB_OFFLINE="0", TRANSFORMERS_OFFLINE="false")
        self.config_loader.side_effect = AssertionError("online constructor must remain unchanged")
        self.assertIs(self.wm._get_generator(), self.generator)
        self.assertIs(self.wm._get_detector(), self.detector)
        self.generator_loader.assert_called_once_with(GENERATOR)
        self.detector_loader.assert_called_once_with(DETECTOR)

    def test_generator_miss_preserves_cooldown_and_retry(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        with self.assertRaises(FileNotFoundError):
            self.wm._get_generator()
        with self.assertRaises(self.wm._GeneratorLoadDeferred):
            self.wm._get_generator()
        self.present.add(self.root / "audioseal" / GENERATOR_FILE)
        self.wm._generator_failed_at -= self.wm._GENERATOR_RETRY_SECONDS + 1
        self.assertIs(self.wm._get_generator(), self.generator)
        self.generator_loader.assert_called_once_with(GENERATOR)

    def test_offline_embed_miss_passes_original_audio_through(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        audio = object()
        with self.assertLogs("watermark_boundary_test", level="WARNING"):
            self.assertIs(self.wm.embed_watermark(audio, 24000), audio)
        self.generator_loader.assert_not_called()

    def test_preload_opt_in_cannot_override_offline_miss(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        with self.assertLogs("watermark_boundary_test", level="WARNING"):
            self.wm.prefetch_generator(allow_download=True)
        self.generator_loader.assert_not_called()

    def test_empty_default_prefetch_does_not_start_loader_or_cooldown(self):
        self.wm.prefetch_generator()
        self.generator_loader.assert_not_called()
        self.assertIsNone(self.wm._generator_failed_at)

    def test_online_preload_opt_in_keeps_constructor_behavior(self):
        self.config_loader.side_effect = AssertionError("explicit online preload need not inspect cache")
        self.wm.prefetch_generator(allow_download=True)
        self.generator_loader.assert_called_once_with(GENERATOR)

    def test_xdg_root_and_audioseal_override_precedence(self):
        os.environ["XDG_CACHE_HOME"] = "unit-xdg"
        self.assertEqual(self.wm._audioseal_checkpoint_path(GENERATOR), self.root / "audioseal" / GENERATOR_FILE)
        del os.environ["AUDIOSEAL_CACHE_DIR"]
        self.assertEqual(self.wm._audioseal_checkpoint_path(DETECTOR), Path("unit-xdg/audioseal") / DETECTOR_FILE)

    def test_empty_overrides_fall_back_to_home_without_expanding_explicit_tilde(self):
        os.environ.update(AUDIOSEAL_CACHE_DIR="", XDG_CACHE_HOME="")
        with patch.object(Path, "home", return_value=Path("unit-home")):
            self.assertEqual(self.wm._audioseal_checkpoint_path(GENERATOR), Path("unit-home/.cache/audioseal") / GENERATOR_FILE)
        os.environ["AUDIOSEAL_CACHE_DIR"] = "~/literal"
        self.assertEqual(self.wm._audioseal_checkpoint_path(GENERATOR), Path("~/literal/audioseal") / GENERATOR_FILE)

    def test_resolver_reads_installed_card_instead_of_hardcoded_url(self):
        self.card[GENERATOR]["checkpoint"] = URL + "detector_base.pth"
        self.assertEqual(self.wm._audioseal_checkpoint_path(GENERATOR), self.root / "audioseal" / DETECTOR_FILE)

    def test_local_checkpoint_keeps_existing_path(self):
        checkpoint = Path("unit-local-existing-checkpoint")
        self.present.add(checkpoint)
        self.card[GENERATOR]["checkpoint"] = str(checkpoint)
        self.assertEqual(self.wm._audioseal_checkpoint_path(GENERATOR), checkpoint)

    def test_known_hf_uri_uses_local_directory_filename(self):
        for repo in ("facebook/audioseal", "meta-adtp/audioseal"):
            self.card[GENERATOR]["checkpoint"] = repo + "/generator_base.pth"
            self.assertEqual(self.wm._audioseal_checkpoint_path(GENERATOR), self.root / "audioseal/generator_base.pth")

    def test_missing_card_fails_closed_offline(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        self.card.clear()
        with self.assertRaises(ValueError):
            self.wm._get_detector()
        self.detector_loader.assert_not_called()

    def test_warm_models_do_not_need_disk_after_cache_removal(self):
        self.wm._generator = self.generator
        self.wm._detector = self.detector
        os.environ["HF_HUB_OFFLINE"] = "1"
        self.config_loader.side_effect = AssertionError("warm models must not load again")
        self.assertIs(self.wm._get_generator(), self.generator)
        self.assertIs(self.wm._get_detector(), self.detector)
        self.generator_loader.assert_not_called()
        self.detector_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
