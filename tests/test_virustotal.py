"""Offline tests for upload routing, credential handling and scan verdicts."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("virustotal_scan", ROOT / "scripts/virustotal.py")
vt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vt)


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


class VirusTotalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "public 'sample'.zip"
        self.path.write_bytes(b"public sample bytes\x00\xff")
        self.client = vt.Client("private-test-key")
        self.client.opener = Mock()

    def test_small_upload_streams_multipart_with_exact_length_and_basename(self):
        sent = []

        def upload(request, **kwargs):
            body = b"".join(request.data)
            self.assertEqual(int(request.get_header("Content-length")), len(body))
            self.assertIn(self.path.read_bytes(), body)
            self.assertIn(b'filename="public__sample_.zip"', body)
            self.assertNotIn(str(self.path.parent).encode(), body)
            self.assertEqual(request.get_header("X-apikey"), "private-test-key")
            sent.append(request.full_url)
            return response({"data": {"id": "analysis-1"}})

        self.client.opener.open.side_effect = upload
        self.assertEqual(self.client.upload(self.path), "analysis-1")
        self.assertEqual(sent, [f"{vt.API}/files"])

    def test_multipart_sanitizes_double_quotes_without_creating_an_invalid_filename(self):
        quoted_path = Mock(spec=Path, wraps=self.path)
        quoted_path.name = 'public "sample".zip'

        chunks, length = vt.multipart(quoted_path, "test-boundary")
        body = b"".join(chunks)

        self.assertEqual(length, len(body))
        self.assertIn(b'filename="public__sample_.zip"', body)
        self.assertNotIn(b'filename="public "sample".zip"', body)
        self.assertNotIn(str(self.path.parent).encode(), body)
        self.assertIn(self.path.read_bytes(), body)
        quoted_path.open.assert_called_once_with("rb")
        self.client.opener.open.assert_not_called()

    def test_large_upload_gets_one_use_url_then_uploads(self):
        upload_url = "https://www.virustotal.com/_ah/upload/one-use-token"
        self.client.opener.open.side_effect = [response({"data": upload_url}), response({"data": {"id": "large"}})]
        with patch.object(vt, "DIRECT_LIMIT", 1), patch.object(vt.time, "sleep"):
            self.assertEqual(self.client.upload(self.path), "large")
        requests = [call.args[0] for call in self.client.opener.open.call_args_list]
        self.assertEqual([(r.get_method(), r.full_url) for r in requests],
                         [("GET", f"{vt.API}/files/upload_url"), ("POST", upload_url)])

    def test_large_upload_may_use_the_big_file_host_virustotal_returns(self):
        """VirusTotal's /files/upload_url answers with bigfiles.virustotal.com for
        files over 32 MiB; refusing it made every full-repo scan fail."""
        upload_url = "https://bigfiles.virustotal.com/_ah/upload/one-use-token/?q=1"
        self.client.opener.open.side_effect = [response({"data": upload_url}), response({"data": {"id": "big"}})]
        with patch.object(vt, "DIRECT_LIMIT", 1), patch.object(vt.time, "sleep"):
            self.assertEqual(self.client.upload(self.path), "big")
        post = self.client.opener.open.call_args_list[1].args[0]
        self.assertEqual((post.get_method(), post.full_url), ("POST", upload_url))
        self.assertEqual(post.get_header("X-apikey"), "private-test-key")

    def test_big_file_host_is_for_the_file_upload_only(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method), self.assertRaises(vt.ScanError):
                self.client.request(method, "https://bigfiles.virustotal.com/api/v3/files/upload_url")
        self.client.opener.open.assert_not_called()

    def test_documented_http_upload_urls_are_upgraded_before_sending_credentials(self):
        # VirusTotal's own /files/upload_url example uses an HTTP-form URL.
        for authority in ("www.virustotal.com", "www.virustotal.com:80"):
            with self.subTest(authority=authority):
                self.client.last_request = None
                self.client.opener.open.reset_mock()
                self.client.opener.open.side_effect = [
                    response({"data": f"http://{authority}/_ah/upload/token/?query=preserved"}),
                    response({"data": {"id": "legacy-upload"}}),
                ]
                with patch.object(vt, "DIRECT_LIMIT", 1), patch.object(vt.time, "sleep"):
                    self.assertEqual(self.client.upload(self.path), "legacy-upload")
                request = self.client.opener.open.call_args.args[0]
                self.assertEqual(request.full_url, "https://www.virustotal.com/_ah/upload/token/?query=preserved")
                self.assertEqual(request.get_header("X-apikey"), "private-test-key")

    def test_empty_or_oversize_files_make_no_requests(self):
        for limit in (0, 1):
            with self.subTest(limit=limit), patch.object(vt, "MAX_SIZE", limit):
                with self.assertRaises(vt.ScanError):
                    self.client.upload(self.path)
        self.path.write_bytes(b"")
        with self.assertRaises(vt.ScanError):
            self.client.upload(self.path)
        self.client.opener.open.assert_not_called()

    def test_untrusted_upload_url_cannot_receive_file_or_key(self):
        for url in ("http://www.virustotal.com/upload", "https://evil.example/upload",
                    "https://www.virustotal.com.evil.example/upload",
                    "https://user@www.virustotal.com/upload", "https://www.virustotal.com:444/upload",
                    "http://www.virustotal.com:443/_ah/upload/token",
                    "http://user@www.virustotal.com/_ah/upload/token",
                    "http://www.virustotal.com/_ah/upload/token#fragment",
                    "http://www.virustotal.com.evil.example/_ah/upload/token",
                    "https://www.virustotal.com:invalid/_ah/upload/token",
                    "http://bigfiles.virustotal.com/_ah/upload/token",
                    "https://bigfiles.virustotal.com.evil.example/_ah/upload/token",
                    "https://evilbigfiles.virustotal.com/_ah/upload/token",
                    "https://static.virustotal.com/_ah/upload/token",
                    "https://virustotal.com/_ah/upload/token",
                    "https://user@bigfiles.virustotal.com/_ah/upload/token",
                    "https://bigfiles.virustotal.com:8443/_ah/upload/token",
                    "https://bigfiles.virustotal.com/_ah/upload/token#fragment"):
            with self.subTest(url=url), patch.object(vt, "DIRECT_LIMIT", 1):
                self.client.opener.open.reset_mock()
                self.client.opener.open.side_effect = None
                self.client.opener.open.return_value = response({"data": url})
                with patch.object(vt.time, "sleep"), self.assertRaises(vt.ScanError):
                    self.client.upload(self.path)
                self.assertEqual(self.client.opener.open.call_count, 1)

    def test_rejected_upload_url_diagnostic_omits_tokens_and_credentials(self):
        with self.assertRaises(vt.ScanError) as caught:
            vt.normalize_upload_url("https://private-test-key@other.example/_ah/upload/private-token/?secret=private-query")
        self.assertIn("other.example", str(caught.exception))
        for sensitive in ("private-test-key", "private-token", "private-query"):
            self.assertNotIn(sensitive, str(caught.exception))

    def test_generic_requests_still_refuse_http(self):
        with self.assertRaises(vt.ScanError):
            self.client.request("GET", "http://www.virustotal.com/api/v3/files/upload_url")
        self.client.opener.open.assert_not_called()

    def test_redirects_never_forward_credentials(self):
        self.assertIsNone(vt.NoRedirects().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_requests_respect_public_api_interval(self):
        self.client.opener.open.side_effect = [response({}), response({})]
        with patch.object(vt.time, "monotonic", side_effect=[100, 103, 116]), patch.object(vt.time, "sleep") as sleep:
            self.client.request("GET", f"{vt.API}/files/upload_url")
            self.client.request("GET", f"{vt.API}/analyses/test")
        sleep.assert_called_once_with(13)

    def test_api_errors_are_actionable_without_echoing_credentials_or_body(self):
        for code in (401, 403, 429, 500):
            with self.subTest(code=code):
                self.client.last_request = None
                self.client.opener.open.side_effect = HTTPError(
                    "https://www.virustotal.com/private-test-key", code, "private-test-key", {},
                    io.BytesIO(b"private-test-key"))
                with self.assertRaises(vt.ScanError) as caught:
                    self.client.request("GET", f"{vt.API}/analyses/test")
                self.assertIn(str(code), str(caught.exception))
                self.assertNotIn("private-test-key", str(caught.exception))
        self.client.last_request = None
        self.client.opener.open.side_effect = URLError("private-test-key")
        with self.assertRaises(vt.ScanError) as caught:
            self.client.request("GET", f"{vt.API}/analyses/test")
        self.assertNotIn("private-test-key", str(caught.exception))

    def test_polling_waits_for_completed_analysis(self):
        completed = {"status": "completed", "stats": {"undetected": 1}, "results": {}}
        self.client.request = Mock(side_effect=[{"data": {"attributes": {"status": "queued"}}},
                                                {"data": {"attributes": completed}}])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.client.wait("id/with=characters", 600), completed)
        self.assertTrue(self.client.request.call_args.args[1].endswith("id%2Fwith%3Dcharacters"))

    def test_timeout_is_not_a_clean_verdict(self):
        with patch.object(vt.time, "monotonic", side_effect=[0, 601]):
            with self.assertRaisesRegex(vt.ScanError, "pending"):
                self.client.wait("analysis", 600)

    def test_poll_deadline_prevents_waiting_for_another_quota_slot(self):
        self.client.last_request = 100
        with patch.object(vt.time, "monotonic", return_value=101), patch.object(vt.time, "sleep") as sleep:
            with self.assertRaisesRegex(vt.ScanError, "pending"):
                self.client.request("GET", f"{vt.API}/analyses/test", deadline=110)
        sleep.assert_not_called()
        self.client.opener.open.assert_not_called()

    def test_flagged_engines_fail_but_unsupported_only_is_inconclusive(self):
        for category in ("malicious", "suspicious"):
            with self.subTest(category=category), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(vt.summarize({"status": "completed", "stats": {category: 1},
                    "results": {"ExampleEngine": {"category": category, "result": "example-label"}}}), 1)
                self.assertIn("ExampleEngine: example-label", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(vt.ScanError):
                vt.summarize({"status": "completed", "stats": {"type-unsupported": 58}})
            self.assertEqual(vt.summarize({"status": "completed", "stats": {"undetected": 58}}), 0)
            with self.assertRaises(vt.ScanError):
                vt.summarize({"status": "in-progress", "stats": {"undetected": 1}})

    def test_missing_key_fails_before_archive_or_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(vt.sys.stdin, "isatty", return_value=False), \
                patch.object(vt, "Client") as client, patch.object(vt, "source_archive") as archive, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(vt.main([]), 2)
        client.assert_not_called()
        archive.assert_not_called()

    def test_file_override_and_no_wait_print_link_without_key(self):
        client = Mock()
        client.upload.return_value = "analysis-1"
        with patch.dict(os.environ, {"VIRUSTOTAL_API_KEY": "private-test-key", "VIRUSTOTAL_FILE": str(self.path)}), \
                patch.object(vt, "Client", return_value=client), patch.object(vt, "source_archive") as archive, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(vt.main(["--no-wait"]), 0)
        client.upload.assert_called_once_with(self.path)
        client.wait.assert_not_called()
        archive.assert_not_called()
        self.assertIn("https://www.virustotal.com/gui/file/", output.getvalue())
        self.assertIn("pending", output.getvalue())
        self.assertNotIn("private-test-key", output.getvalue())

    @unittest.skipUnless((ROOT / ".git").exists(), "requires a Git checkout")
    def test_default_archive_uses_committed_tree_instead_of_local_edits(self):
        import zipfile

        archive_path = Path(self.temporary.name) / "source.zip"
        with patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.eol",
            "GIT_CONFIG_VALUE_1": "lf",
        }):
            vt.source_archive(archive_path)
        committed_attributes = subprocess.check_output(["git", "show", "HEAD:.gitattributes"], cwd=ROOT)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.read("VoiceStudio-main/.gitattributes"), committed_attributes)
            self.assertNotIn("VoiceStudio-main/.env", archive.namelist())

    def test_make_passes_file_override_without_needing_a_live_api(self):
        import shutil

        if not shutil.which("make"):
            self.skipTest("Make is unavailable")
        missing_file = Path(self.temporary.name) / "missing public file.zip"
        result = subprocess.run(
            ["make", "virustotal", f"FILE={missing_file}", f"PYTHON={vt.sys.executable}"],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "VIRUSTOTAL_API_KEY": "private-test-key"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Upload path must be an existing file", result.stderr)
        self.assertNotIn("private-test-key", result.stdout + result.stderr)

    def test_plain_make_keeps_the_existing_release_default(self):
        import shutil

        if not shutil.which("make"):
            self.skipTest("Make is unavailable")
        result = subprocess.run(["make", "-n"], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertIn("uv run python scripts/release.py", result.stdout)
        self.assertNotIn("virustotal.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
