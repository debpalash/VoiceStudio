"""CLI/module bridge for terminals, editor extensions, and agent hooks.

The desktop app must be running for native dictation control. Batch
transcription can also target a standalone or remote VoiceStudio backend.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

DEFAULT_CONTROL_URL = "http://127.0.0.1:3902"
DEFAULT_ENGINE_URL = "http://127.0.0.1:3900"


def _default_engine_url() -> str:
    """VOICESTUDIO_URL, else the backend's OMNIVOICE_PORT on loopback."""
    explicit = os.environ.get("VOICESTUDIO_URL", "").strip()
    if explicit:
        return explicit
    try:
        port = int(os.environ.get("OMNIVOICE_PORT", ""))
    except ValueError:
        return DEFAULT_ENGINE_URL
    return f"http://127.0.0.1:{port}" if 0 < port <= 65535 else DEFAULT_ENGINE_URL


class SpeechClientError(RuntimeError):
    pass


class _RejectCredentialRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        raise SpeechClientError("VoiceStudio refused a credentialed redirect")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _decode_error(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    try:
        detail = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        detail = body.strip()
    return f"HTTP {exc.code}: {detail or exc.reason}"


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _open(req: request.Request, timeout: float = 300.0) -> tuple[bytes, str]:
    target = urlsplit(req.full_url)
    scheme = target.scheme.lower()
    if scheme not in {"http", "https"}:
        raise SpeechClientError("VoiceStudio URLs must use http:// or https://")
    credentialed = bool(req.get_header("Authorization"))
    if credentialed and scheme != "https" and not _is_loopback_host(target.hostname):
        raise SpeechClientError("Remote VoiceStudio credentials require https://")
    try:
        opener = (
            request.build_opener(_RejectCredentialRedirect())
            if credentialed
            else request.build_opener()
        )
        with opener.open(req, timeout=timeout) as response:  # noqa: S310
            return response.read(), response.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        raise SpeechClientError(_decode_error(exc)) from exc
    except error.URLError as exc:
        raise SpeechClientError(f"VoiceStudio is unavailable: {exc.reason}") from exc


def _json_request(method: str, url: str, payload: Any | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    body, _ = _open(request.Request(url, data=data, headers=headers, method=method), timeout=10.0)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SpeechClientError("VoiceStudio returned invalid JSON") from exc


def _encode_multipart(
    *,
    filename: str,
    audio: bytes,
    fields: dict[str, str],
    boundary: str | None = None,
) -> tuple[bytes, str]:
    boundary = boundary or f"voicestudio-{secrets.token_hex(16)}"
    marker = boundary.encode("ascii")
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                b"--" + marker + b"\r\n",
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    safe_filename = Path(filename).name.replace('"', "") or "audio.wav"
    content_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
    if Path(safe_filename).suffix.lower() in {".wav", ".wave"}:
        content_type = "audio/wav"
    parts.extend(
        [
            b"--" + marker + b"\r\n",
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{safe_filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            audio,
            b"\r\n--" + marker + b"--\r\n",
        ]
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _control(args: argparse.Namespace, action: str) -> int:
    method = "GET" if action in {"status", "capabilities"} else "POST"
    path = {
        "status": "/v1/status",
        "capabilities": "/v1/capabilities",
        "start": "/v1/dictation/start",
        "stop": "/v1/dictation/stop",
        "toggle": "/v1/dictation/toggle",
    }[action]
    result = _json_request(method, _join_url(args.control_url, path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _read_audio(path: str, stdin_filename: str) -> tuple[bytes, str]:
    if path == "-":
        return sys.stdin.buffer.read(), stdin_filename
    audio_path = Path(path)
    try:
        return audio_path.read_bytes(), audio_path.name
    except OSError as exc:
        display_name = path.replace("\\", "/").rsplit("/", 1)[-1] or "audio input"
        reason = exc.strerror or type(exc).__name__
        raise SpeechClientError(f"could not read '{display_name}': {reason}") from exc


def _response_text(body: bytes, content_type: str) -> str:
    decoded = body.decode("utf-8", errors="replace")
    if "json" not in content_type.lower():
        return decoded
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return decoded
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return payload["text"]
    return decoded


def _transcribe(args: argparse.Namespace) -> int:
    audio, filename = _read_audio(args.audio, args.stdin_filename)
    fields = {
        "model": args.model,
        "response_format": args.response_format,
    }
    if args.language:
        fields["language"] = args.language
    body, content_type = _encode_multipart(filename=filename, audio=audio, fields=fields)
    headers = {"Content-Type": content_type, "Accept": "application/json, text/plain"}
    api_key = os.environ.get("OMNIVOICE_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    output_session_id = None
    if args.insert:
        session = _json_request(
            "POST", _join_url(args.control_url, "/v1/output/sessions")
        )
        output_session_id = session["session_id"]

    session_needs_cleanup = output_session_id is not None
    try:
        response_body, response_type = _open(
            request.Request(
                _join_url(args.engine_url, "/v1/audio/transcriptions"),
                data=body,
                headers=headers,
                method="POST",
            )
        )
        if output_session_id is not None:
            _json_request(
                "POST",
                _join_url(
                    args.control_url,
                    f"/v1/output/sessions/{output_session_id}/insert",
                ),
                {"text": _response_text(response_body, response_type)},
            )
            session_needs_cleanup = False
    finally:
        if session_needs_cleanup:
            try:
                _json_request(
                    "DELETE",
                    _join_url(args.control_url, f"/v1/output/sessions/{output_session_id}"),
                )
            except Exception:  # noqa: BLE001
                # Best-effort cleanup must not replace the original failure or
                # KeyboardInterrupt that brought control into this finally.
                pass

    sys.stdout.buffer.write(response_body)
    if response_body and not response_body.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voicestudio-speech",
        description="Control and consume VoiceStudio's local speech platform.",
    )
    parser.add_argument(
        "--control-url",
        default=os.environ.get("VOICESTUDIO_SPEECH_URL", DEFAULT_CONTROL_URL),
    )
    parser.add_argument(
        "--engine-url",
        default=_default_engine_url(),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "capabilities", "start", "stop", "toggle"):
        subparsers.add_parser(command)

    transcribe = subparsers.add_parser("transcribe")
    transcribe.add_argument("audio", help="audio file, or - for stdin")
    transcribe.add_argument("--stdin-filename", default="audio.wav")
    transcribe.add_argument("--model", default="whisper-1")
    transcribe.add_argument("--language")
    transcribe.add_argument(
        "--format",
        dest="response_format",
        choices=("json", "text", "verbose_json", "srt", "vtt"),
        default="text",
    )
    transcribe.add_argument(
        "--insert",
        action="store_true",
        help="insert the result into the app focused when this command starts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "transcribe":
            return _transcribe(args)
        return _control(args, args.command)
    except (SpeechClientError, KeyError) as exc:
        print(f"voicestudio-speech: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
