"""Model-free metadata admission regressions; execute only the extracted checker."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


class FakeMetadata(dict):
    def __init__(self, distribution):
        super().__init__(distribution.fields)
        self.distribution = distribution

    def get(self, key, default=None):
        if key == "Name":
            self.distribution.name_reads += 1
        return super().get(key, default)


class FakeDistribution:
    """Distribution.version may read metadata again, just like the stdlib."""
    def __init__(self, name, version):
        self.fields = {"Version": version}
        if name is not None:
            self.fields["Name"] = name
        self.metadata_reads = self.name_reads = self.version_reads = 0

    @property
    def metadata(self):
        self.metadata_reads += 1
        return FakeMetadata(self)

    @property
    def version(self):
        self.version_reads += 1
        return self.metadata.get("Version")


class MemoryPath:
    def __init__(self, path, files):
        self.path, self.files = path, files

    def __truediv__(self, suffix):
        return MemoryPath(self.path + "/" + suffix, self.files)

    def __str__(self):
        return self.path

    def read_text(self, *, encoding):
        assert encoding == "utf-8"
        return self.files[self.path]


class EnvironmentFixture:
    def __init__(self):
        self.packages = {"torch": "2.9.1+rocm7.2.1", "torchaudio": "2.9.1+rocm7.2.1",
                         "rocm-sdk-core": "7.2.1", "soundfile": "0.13.1"}
        self.hip = "7.2.53211"
        self.distributions = [FakeDistribution(name, version) for name, version in self.packages.items()]
        self.distributions.extend(FakeDistribution("unrelated-" + str(index), "1.0") for index in range(8))
        self.files = {"checkout/.venv/pyvenv.cfg": "version_info = 3.12.14\n",
                      "checkout/.venv/Lib/site-packages/torch/version.py":
                          "__version__ = '2.9.1+rocm7.2.1'\nhip = '7.2.53211'\n"}
        self.checkout = MemoryPath("checkout", self.files)
        self.enumerations = 0
        source = globals().get("SIDECAR_SOURCE_TEXT")
        if source is None:
            source = (Path(__file__).resolve().parents[1] / "backend/core/windows_rocm/sidecar.py").read_text("utf-8")
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "environment_matches")
        scope = {"ast": ast, "re": re, "Path": Path,
                 "metadata": SimpleNamespace(distributions=self.enumerate_distributions)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "extracted-environment-matches", "exec"), scope)
        self.checker = scope["environment_matches"]

    def enumerate_distributions(self, *, path):
        assert path == ["checkout/.venv/Lib/site-packages"]
        self.enumerations += 1
        return iter(self.distributions)

    def check(self):
        return self.checker(self.checkout, self.packages, self.hip)


class EnvironmentMatchesTests(unittest.TestCase):
    def test_names_are_read_once_per_distribution_per_call(self):
        fixture = EnvironmentFixture()
        self.assertTrue(fixture.check())
        self.assertEqual(fixture.enumerations, 1)
        self.assertEqual([row.name_reads for row in fixture.distributions], [1] * len(fixture.distributions))
        self.assertEqual(sum(row.version_reads for row in fixture.distributions), len(fixture.packages))
        self.assertEqual(sum(row.metadata_reads for row in fixture.distributions),
                         len(fixture.distributions) + len(fixture.packages))

    def test_expected_normalized_duplicates_are_rejected_in_both_orders(self):
        for name in ("TORCH", "rocm_sdk_core", "ROCM-SDK-CORE"):
            for first in (False, True):
                with self.subTest(name=name, first=first):
                    fixture = EnvironmentFixture()
                    version = fixture.packages[name.lower().replace("_", "-")]
                    duplicate = FakeDistribution(name, version)
                    fixture.distributions.insert(0 if first else len(fixture.distributions), duplicate)
                    self.assertFalse(fixture.check())

    def test_unrelated_normalized_duplicates_do_not_reject_valid_environment(self):
        fixture = EnvironmentFixture()
        fixture.distributions.extend([FakeDistribution("OTHER_PKG", "1"), FakeDistribution("other-pkg", "2")])
        self.assertTrue(fixture.check())

    def test_name_case_and_underscore_normalization_is_preserved(self):
        fixture = EnvironmentFixture()
        fixture.distributions[0].fields["Name"] = "TORCH"
        fixture.distributions[2].fields["Name"] = "ROCM_SDK_CORE"
        self.assertTrue(fixture.check())

    def test_name_normalization_is_not_broadened(self):
        for name in ("rocm.sdk.core", "rocm--sdk-core"):
            with self.subTest(name=name):
                fixture = EnvironmentFixture()
                fixture.distributions[2].fields["Name"] = name
                self.assertFalse(fixture.check())

    def test_missing_expected_distribution_is_rejected(self):
        fixture = EnvironmentFixture()
        fixture.distributions.pop(0)
        self.assertFalse(fixture.check())

    def test_unnamed_unrelated_distribution_is_ignored(self):
        fixture = EnvironmentFixture()
        fixture.distributions.append(FakeDistribution(None, "1"))
        self.assertTrue(fixture.check())

    def test_version_comparison_remains_exact(self):
        for index, version in ((0, "2.9.1"), (0, "2.9.1+cpu"), (0, "2.9.1+rocm7.2.1 "), (2, "7.2.1.0")):
            with self.subTest(index=index, version=version):
                fixture = EnvironmentFixture()
                fixture.distributions[index].fields["Version"] = version
                self.assertFalse(fixture.check())

    def test_torch_source_version_or_hip_tampering_is_rejected(self):
        for version, hip in (("2.9.1+cpu", "7.2.53211"), ("2.9.1+rocm7.2.1", "7.1.0"),
                             ("2.9.1+rocm7.2.1", None)):
            with self.subTest(version=version, hip=hip):
                fixture = EnvironmentFixture()
                fixture.files["checkout/.venv/Lib/site-packages/torch/version.py"] = (
                    f"__version__ = {version!r}\nhip = {hip!r}\n")
                self.assertFalse(fixture.check())

    def test_literal_annotated_torch_assignments_remain_supported(self):
        fixture = EnvironmentFixture()
        fixture.files["checkout/.venv/Lib/site-packages/torch/version.py"] = (
            "__version__: str = '2.9.1+rocm7.2.1'\nhip: str = '7.2.53211'\n")
        self.assertTrue(fixture.check())

    def test_nonliteral_torch_assignments_still_raise(self):
        fixture = EnvironmentFixture()
        fixture.files["checkout/.venv/Lib/site-packages/torch/version.py"] = "__version__ = unexpected()\n"
        with self.assertRaises(ValueError):
            fixture.check()

    def test_python_312_version_gate_is_unchanged(self):
        for version, expected in (("3.12", True), ("3.12.14", True), ("3.11.14", False),
                                  ("3.13", False), ("3.12.14.1", False)):
            with self.subTest(version=version):
                fixture = EnvironmentFixture()
                fixture.files["checkout/.venv/pyvenv.cfg"] = f"version = {version}\n"
                self.assertEqual(fixture.check(), expected)
                self.assertEqual(fixture.enumerations, int(expected))

    def test_second_call_detects_version_change_without_global_cache(self):
        fixture = EnvironmentFixture()
        self.assertTrue(fixture.check())
        fixture.distributions[0].fields["Version"] = "2.9.1+cpu"
        self.assertFalse(fixture.check())
        self.assertEqual(fixture.enumerations, 2)

    def test_second_call_detects_new_duplicate_without_global_cache(self):
        fixture = EnvironmentFixture()
        self.assertTrue(fixture.check())
        fixture.distributions.append(FakeDistribution("Torch", fixture.packages["torch"]))
        self.assertFalse(fixture.check())
        self.assertEqual(fixture.enumerations, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
