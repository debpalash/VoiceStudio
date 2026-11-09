#!/usr/bin/env python3
"""Manually upload a public file (default: committed HEAD source ZIP) to VirusTotal."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import uuid

ROOT = Path(__file__).resolve().parents[1]
API = "https://www.virustotal.com/api/v3"
API_HOSTS = ("www.virustotal.com",)
# Files over DIRECT_LIMIT go to a one-use URL VirusTotal hands out, and that URL is
# on its big-file host. Only the file POST may use it; API calls stay on www.
UPLOAD_HOSTS = ("www.virustotal.com", "bigfiles.virustotal.com")
DIRECT_LIMIT = 32 * 1024 * 1024
MAX_SIZE = 650 * 1024 * 1024
REQUEST_INTERVAL = 16  # Public API: at most four requests per minute.


class ScanError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the API credential or uploaded bytes to a redirect.
        return None


def validate_url(url: str, *, upload: bool = False) -> str:
    try:
        parsed = urlsplit(url)
        allowed = (parsed.scheme == "https"
                   and parsed.hostname in (UPLOAD_HOSTS if upload else API_HOSTS)
                   and parsed.port in (None, 443) and not parsed.username
                   and not parsed.password and not parsed.fragment)
    except ValueError:
        raise ScanError("VirusTotal returned a malformed upload URL; refusing to send the file/key.") from None
    if not allowed:
        # Report only the origin, never the one-use upload token/path/query.
        scheme = re.sub(r"[^A-Za-z0-9.-]", "?", parsed.scheme or "missing")[:16]
        host = re.sub(r"[^A-Za-z0-9.:-]", "?", parsed.hostname or "missing")[:255]
        origin = f"{scheme}://{host}"
        raise ScanError(f"VirusTotal returned an unexpected upload origin ({origin}); refusing to send the file/key.")
    return url


def normalize_upload_url(url: str) -> str:
    # The official /files/upload_url example uses http://www.virustotal.com/
    # _ah/upload/… . Upgrade that known legacy form locally, before any request.
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "http" and parsed.hostname == "www.virustotal.com"
                and parsed.port in (None, 80) and not parsed.username
                and not parsed.password and parsed.path.startswith("/_ah/upload/")):
            url = parsed._replace(scheme="https", netloc="www.virustotal.com").geturl()
    except ValueError:
        raise ScanError("VirusTotal returned a malformed upload URL; refusing to send the file/key.") from None
    return validate_url(url, upload=True)


def multipart(path: Path, boundary: str):
    # Send a basename only; never expose the local directory in the multipart header.
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", path.name)
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def chunks():
        yield head
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        yield tail

    return chunks(), len(head) + path.stat().st_size + len(tail)


class Client:
    def __init__(self, key: str):
        self.key = key
        self.opener = build_opener(NoRedirects())
        self.last_request = None

    def request(self, method: str, url: str, *, file: Path | None = None,
                deadline: float | None = None) -> dict:
        validate_url(url, upload=file is not None)
        delay = 0
        if self.last_request is not None:
            delay = REQUEST_INTERVAL - (time.monotonic() - self.last_request)
        if deadline is not None and time.monotonic() + max(delay, 0) >= deadline:
            raise ScanError("Analysis is still pending. Open the report link for results; no clean verdict was returned.")
        if delay > 0:
            time.sleep(delay)
        headers = {"x-apikey": self.key, "Accept": "application/json"}
        data = None
        if file is not None:
            boundary = uuid.uuid4().hex
            data, length = multipart(file, boundary)
            headers.update({"Content-Type": f"multipart/form-data; boundary={boundary}",
                            "Content-Length": str(length)})
        self.last_request = time.monotonic()
        request = Request(url, data=data, headers=headers, method=method)
        timeout = 120 if deadline is None else min(120, max(0.001, deadline - time.monotonic()))
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 429:
                raise ScanError("VirusTotal quota reached (HTTP 429). Retry later; other clients share this key's quota.") from None
            if status in (401, 403):
                raise ScanError(f"VirusTotal authorization failed (HTTP {status}); check VIRUSTOTAL_API_KEY and API access.") from None
            raise ScanError(f"VirusTotal request failed (HTTP {status}).") from None
        except (URLError, TimeoutError, OSError):
            raise ScanError("VirusTotal connection failed or timed out. Check the report before retrying an upload.") from None
        except (ValueError, TypeError):
            raise ScanError("VirusTotal returned invalid JSON.") from None

    def upload(self, path: Path) -> str:
        size = path.stat().st_size
        if not 0 < size <= MAX_SIZE:
            raise ScanError("Upload must be nonempty and at most 650 MiB.")
        url = f"{API}/files"
        if size > DIRECT_LIMIT:
            url = self.request("GET", f"{API}/files/upload_url")["data"]
            if not isinstance(url, str):
                raise ScanError("VirusTotal returned an invalid upload URL.")
            url = normalize_upload_url(url)
        result = self.request("POST", url, file=path)
        analysis_id = result.get("data", {}).get("id")
        if not isinstance(analysis_id, str) or not analysis_id:
            raise ScanError("VirusTotal did not return an analysis ID.")
        return analysis_id

    def wait(self, analysis_id: str, timeout: int) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.request("GET", f"{API}/analyses/{quote(analysis_id, safe='')}", deadline=deadline)
            attributes = response.get("data", {}).get("attributes", {})
            status = attributes.get("status")
            if status == "completed":
                return attributes
            if status not in ("queued", "in-progress"):
                raise ScanError("VirusTotal returned an unknown analysis status.")
            print(f"Analysis {status}; waiting…", flush=True)
        raise ScanError("Analysis is still pending. Open the report link for results; no clean verdict was returned.")


def summarize(attributes: dict) -> int:
    if attributes.get("status") != "completed":
        raise ScanError("Analysis is incomplete; no clean verdict was returned.")
    stats = attributes.get("stats", {})
    if not isinstance(stats, dict) or any(type(value) is not int or value < 0 for value in stats.values()):
        raise ScanError("VirusTotal returned invalid scan statistics.")
    print("Engine results:", json.dumps(stats, sort_keys=True))
    for engine, result in sorted(attributes.get("results", {}).items()):
        if result.get("category") in ("malicious", "suspicious"):
            print(f"  {engine}: {result.get('result') or result['category']}")
    if stats.get("malicious", 0) or stats.get("suspicious", 0):
        return 1
    if not stats.get("harmless", 0) and not stats.get("undetected", 0):
        raise ScanError("No engine returned an evaluable result; no clean verdict was returned.")
    print("No engines flagged this file in the completed analysis.")
    return 0


def source_archive(destination: Path) -> None:
    # Match GitHub's committed tree and export-ignore rules. Local changes are
    # deliberately excluded; use an explicit file to scan a prepared candidate.
    subprocess.run(["git", "archive", "--format=zip", "--prefix=VoiceStudio-main/",
                    "-o", str(destination), "HEAD"], cwd=ROOT, check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", default=os.environ.get("VIRUSTOTAL_FILE") or None,
                        help="Public file to upload; default: source ZIP of committed HEAD")
    parser.add_argument("--no-wait", action="store_true", help="Print the report link after upload without polling")
    parser.add_argument("--timeout", type=int, default=600, help="Analysis wait budget in seconds (default: 600)")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    key = os.environ.get("VIRUSTOTAL_API_KEY", "").strip()
    if not key and sys.stdin.isatty():
        try:
            key = getpass.getpass("VirusTotal API key (hidden, not saved): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("No API key entered; nothing uploaded.", file=sys.stderr)
            return 2
    if not key:
        print("Set VIRUSTOTAL_API_KEY or run interactively to enter it at the hidden prompt.", file=sys.stderr)
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="voicestudio-virustotal-") as temporary:
            path = Path(args.file).expanduser().resolve() if args.file else Path(temporary) / "VoiceStudio-main.zip"
            if args.file is None:
                source_archive(path)
                print("Source: committed HEAD (local changes excluded).")
            if not path.is_file():
                raise ScanError("Upload path must be an existing file.")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            print(f"File: {path.name} ({path.stat().st_size} bytes)\nSHA-256: {digest}", flush=True)
            print("Uploading to VirusTotal's shared dataset…", flush=True)
            client = Client(key)
            analysis_id = client.upload(path)
            print(f"Report: https://www.virustotal.com/gui/file/{digest}\nAnalysis ID: {analysis_id}", flush=True)
            if args.no_wait:
                print("Upload accepted; analysis verdict is pending.")
                return 0
            return summarize(client.wait(analysis_id, args.timeout))
    except (ScanError, OSError, subprocess.CalledProcessError, KeyError, TypeError) as error:
        # Do not print server bodies, request headers, or credential-bearing URLs.
        message = str(error) if isinstance(error, ScanError) else "Could not prepare the file or process the VirusTotal response."
        print(f"Error: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; an accepted upload may still be processing at the report link.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
