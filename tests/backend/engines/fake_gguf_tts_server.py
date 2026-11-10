"""Stand-in for omnivoice.cpp's ``tts-server`` (040c8b344d8c+ API) in tests.

Speaks the endpoints the resident mode uses and records every call in
``$FAKE_TTS_STATE/events.jsonl``. Flag files in that directory steer it:

* ``exit_at_start``      exit before listening (a server that cannot start);
* ``crash_next_speech``  exit on the next speech request (removed first);
* ``crash_every_speech`` exit on every speech request;
* ``no_voice_registry``  answer 404 on /v1/audio/voices (pre-registry server);
* ``reject_speech``      answer 400 on speech;
* ``truncate_next_speech``  send the headers, half the WAV, then drop the
                        connection, once (removed first);
* ``slow_speech``        take 5 s to answer speech.
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = Path(os.environ["FAKE_TTS_STATE"])


def _arg(name: str) -> str:
    return sys.argv[sys.argv.index(name) + 1]


def _event(**row) -> None:
    with (STATE / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _flag(name: str) -> bool:
    return (STATE / name).exists()


def _wav(n: int = 2400) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(struct.pack("<" + "h" * n, *([1000] * n)))
    return buf.getvalue()


VOICES: dict[str, str] = {}
MODEL_ID = os.path.basename(_arg("--model"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")

    def do_GET(self):
        if self.path == "/v1/models":
            self._reply(200, json.dumps({"object": "list", "data": [{"id": MODEL_ID}]}).encode())
        elif self.path == "/v1/audio/voices" and not _flag("no_voice_registry"):
            self._reply(200, json.dumps({"voices": [{"name": n} for n in VOICES]}).encode())
        else:
            self._reply(404, b"{}")

    def do_POST(self):
        body = self._body()
        if self.path == "/v1/audio/voices" and not _flag("no_voice_registry"):
            VOICES[body["name"]] = body["ref_text"]
            _event(kind="voice", pid=os.getpid(), name=body["name"], ref_text=body["ref_text"],
                   wav_bytes=len(body["wav_b64"]))
            self._reply(200, json.dumps({"name": body["name"], "status": "registered"}).encode())
        elif self.path == "/v1/audio/speech":
            _event(kind="speech", pid=os.getpid(), body=body)
            if _flag("crash_next_speech"):
                (STATE / "crash_next_speech").unlink()
                os._exit(3)
            if _flag("crash_every_speech"):
                os._exit(3)
            if _flag("truncate_next_speech"):
                (STATE / "truncate_next_speech").unlink()
                wav = _wav()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav)))
                self.end_headers()
                self.wfile.write(wav[: len(wav) // 2])
                self.wfile.flush()
                self.close_connection = True
                self.connection.close()
                return
            if _flag("slow_speech"):
                time.sleep(5)
            if _flag("reject_speech") or (body.get("voice") and body["voice"] not in VOICES):
                self._reply(400, b'{"error":{"message":"rejected"}}')
                return
            self._reply(200, _wav(), "audio/wav")
        else:
            self._reply(404, b"{}")


def main() -> None:
    _event(kind="start", pid=os.getpid(), argv=sys.argv[1:])
    if _flag("exit_at_start"):
        sys.exit(1)
    server = ThreadingHTTPServer((_arg("--host"), int(_arg("--port"))), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
