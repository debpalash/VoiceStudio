"""Exercise the source-archive boundary with Git's actual ZIP/tar exporters."""

import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCAM_TEST = "tests/test_scam_guard.py"
REQUIRED = (
    ".github/scripts/scam_guard.py",
    "tests/test_cla_workflow.py",
    "scripts/install.ps1",
    "scripts/install.sh",
    "electron/tests/packaging-contract.mjs",
    "electron/package.json",
    "pyproject.toml",
    "LICENSE",
)


@unittest.skipUnless((ROOT / ".git").exists() and shutil.which("git"), "requires a Git checkout")
class SourceArchiveTests(unittest.TestCase):
    def test_attack_examples_stay_in_checkout_but_not_source_archives(self):
        self.assertTrue((ROOT / SCAM_TEST).is_file(), "CI must retain scam-guard coverage")
        for archive_format in ("zip", "tar"):
            with self.subTest(archive_format=archive_format):
                archive = subprocess.run(
                    ["git", "archive", "--worktree-attributes", f"--format={archive_format}",
                     "HEAD", "--", SCAM_TEST, *REQUIRED],
                    cwd=ROOT, check=True, capture_output=True,
                ).stdout
                if archive_format == "zip":
                    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                        names = set(bundle.namelist())
                else:
                    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                        names = set(bundle.getnames())
                self.assertNotIn(SCAM_TEST, names)
                self.assertTrue(set(REQUIRED) <= names, f"Required source files missing: {set(REQUIRED) - names}")


if __name__ == "__main__":
    unittest.main()
