"""URL imports only fetch public destinations (core.url_safety).

Covers the up-front 400s on both import routes, option-like input never
reaching yt-dlp, the connect-time guard (which is what stops redirects and
discovered URLs), the opt-in, and the restricted upload extensions.
"""
import http.server
import os
import socket
import subprocess
import sys
import threading

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core import url_safety
from core.url_safety import ALLOW_PRIVATE_ENV, UnsafeURLError, check_public_url, is_public_address

PUBLIC_IP = "93.184.215.14"


def _answers(*ips):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
            for ip in ips
        ]

    return fake_getaddrinfo


@pytest.fixture(autouse=True)
def _no_opt_in(monkeypatch):
    monkeypatch.delenv(ALLOW_PRIVATE_ENV, raising=False)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "::",
        "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
        "2002:c0a8:0101::1", "64:ff9b::a9fe:a9fe", "fe80::1%eth0", "not-an-ip",
    ],
)
def test_non_public_addresses_are_refused(ip):
    assert not is_public_address(ip)


@pytest.mark.parametrize("ip", [PUBLIC_IP, "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip):
    assert is_public_address(ip)


@pytest.mark.parametrize(
    "url",
    ["--config-locations=/tmp/x.conf", "-o/tmp/x", "file:///etc/passwd", "ytsearch:x", "ftp://h/x", "http://", ""],
)
def test_only_http_urls_are_accepted(url):
    with pytest.raises(UnsafeURLError, match="http"):
        check_public_url(url)


def test_private_answers_are_refused_and_public_allowed(monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers("192.168.1.10"))
    with pytest.raises(UnsafeURLError, match=ALLOW_PRIVATE_ENV):
        check_public_url("http://media.lan:9000/x")
    # One private answer among public ones is enough to refuse.
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers(PUBLIC_IP, "127.0.0.1"))
    with pytest.raises(UnsafeURLError):
        check_public_url("https://mixed.example/v")
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers(PUBLIC_IP))
    assert check_public_url(" https://www.youtube.com/watch?v=x ") == "https://www.youtube.com/watch?v=x"


def test_fake_ip_answers_stay_refused_and_the_message_says_what_to_do(monkeypatch):
    """Clash, Surge and sing-box in TUN/fake-IP mode answer every lookup with
    198.18.0.0/15. The range stays refused; the message names the cause, the
    fix, and where a desktop user sets the variable."""
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers("198.18.0.7"))
    with pytest.raises(UnsafeURLError) as refused:
        check_public_url("https://www.youtube.com/watch?v=x")
    detail = str(refused.value)
    assert f"{ALLOW_PRIVATE_ENV}=1" in detail
    assert "198.18." in detail and "fake-IP" in detail and "real-IP DNS" in detail
    assert "~/.config/omnivoice/env" in detail
    assert "%USERPROFILE%\\.config\\omnivoice\\env" in detail


def test_opt_in_allows_private_destinations(monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers("192.168.1.10"))
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "1")
    assert check_public_url("http://media.lan:9000/x") == "http://media.lan:9000/x"


def test_resolved_media_urls_are_checked(monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers("169.254.169.254"))
    with pytest.raises(UnsafeURLError):
        url_safety.check_resolved_media_urls({"requested_formats": [{"url": "http://meta/x"}]})
    with pytest.raises(UnsafeURLError):
        url_safety.check_resolved_media_urls({"url": "rtmp://public.example/live"})


# ── Routes ───────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 1))


@pytest.fixture
def spawned(monkeypatch):
    import api.routers.gallery as gallery

    calls = []

    class _Proc:
        returncode = 1

        async def communicate(self):
            return b"", b"stub failure"

    async def fake_spawn(*argv, **_kwargs):
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(gallery, "spawn_subprocess", fake_spawn)
    return calls


@pytest.mark.parametrize(
    "video_url",
    ["--config-locations=/tmp/evil.conf", "--exec=touch /tmp/x", "-a/tmp/list", "ytsearch:x"],
)
def test_option_like_gallery_url_never_reaches_ytdlp(spawned, video_url):
    response = _client().post(
        "/gallery/download", params={"video_url": video_url, "character_name": "x"}
    )
    assert response.status_code == 400
    assert spawned == []


@pytest.mark.parametrize("ip", ["10.0.0.7", "169.254.169.254", "::ffff:127.0.0.1"])
def test_private_gallery_destination_is_refused(spawned, monkeypatch, ip):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers(ip))
    response = _client().post(
        "/gallery/download", params={"video_url": "http://target:9000/x", "character_name": "x"}
    )
    assert response.status_code == 400
    assert ALLOW_PRIVATE_ENV in response.json()["detail"]
    assert spawned == []


def test_public_gallery_url_runs_guarded_ytdlp_with_separator(spawned, monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers(PUBLIC_IP))
    url = "https://www.youtube.com/watch?v=abc"
    response = _client().post("/gallery/download", params={"video_url": url, "character_name": "x"})
    assert response.status_code == 500  # the stubbed run fails
    (argv,) = spawned
    assert argv[-2:] == ["--", url]
    assert "core.url_safety" in " ".join(argv[:4])


@pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.0.5", "169.254.169.254"])
def test_private_dub_ingest_destination_is_refused(monkeypatch, ip):
    from core.tasks import task_manager

    added = []

    async def fake_add_task(*args, **kwargs):
        added.append(args)

    monkeypatch.setattr(task_manager, "add_task", fake_add_task)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers(ip))
    response = _client().post("/dub/ingest-url", json={"url": "http://box:9000/x"})
    assert response.status_code == 400
    assert ALLOW_PRIVATE_ENV in response.json()["detail"]
    assert added == []

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _answers(PUBLIC_IP))
    assert _client().post("/dub/ingest-url", json={"url": "https://v.example/x"}).status_code == 202
    assert len(added) == 1


@pytest.mark.parametrize("name", ["evil.conf", "cfg.txt", "run.sh", "noext"])
def test_uploads_refuse_non_media_extensions(name):
    client = _client()
    gallery = client.post(
        "/gallery/upload",
        data={"name": "x"},
        files={"audio": (name, b"--exec touch /tmp/x\n", "application/octet-stream")},
    )
    assert gallery.status_code == 415
    dub = client.post(
        "/dub/upload",
        files={"video": (name, b"--exec touch /tmp/x\n", "application/octet-stream")},
    )
    assert dub.status_code == 415


@pytest.mark.parametrize("name", ["evil.conf", "cfg.txt", "run.sh", "x.wav:stream", "clip.m\\..\\x"])
def test_profile_batch_and_preview_uploads_share_the_media_policy(name):
    client = _client()
    payload = b"--exec touch /tmp/x\n"
    profile = client.post(
        "/profiles",
        data={"name": "x"},
        files={"ref_audio": (name, payload, "application/octet-stream")},
    )
    assert profile.status_code == 415
    batch = client.post(
        "/batch/enqueue", files={"video": (name, payload, "application/octet-stream")}
    )
    assert batch.status_code == 415
    preview = client.post(
        "/preview/upload", files={"video": (name, payload, "application/octet-stream")}
    )
    assert preview.status_code == 415


@pytest.mark.parametrize(
    "name", ["book.m4b", "a.mka", "a.ac3", "a.mp2", "a.asf", "a.f4v", "a.mxf", "A.M4B"]
)
def test_real_media_extensions_are_accepted(name):
    from core.media_types import MEDIA_EXTS, media_extension, media_upload_suffix

    assert media_extension(name, MEDIA_EXTS, ".wav") == os.path.splitext(name)[1].lower()
    assert media_upload_suffix(name) == os.path.splitext(name)[1].lower()


def test_media_extensions_are_plain_and_exclude_config_or_script_types():
    import re

    from core.media_types import AUDIO_EXTS, MEDIA_EXTS, VIDEO_EXTS, media_upload_suffix

    assert MEDIA_EXTS == AUDIO_EXTS | VIDEO_EXTS
    assert all(re.fullmatch(r"\.[a-z0-9]{1,8}", ext) for ext in MEDIA_EXTS)
    for ext in (".conf", ".cfg", ".ini", ".env", ".txt", ".json", ".yaml", ".toml",
                ".sh", ".bat", ".cmd", ".ps1", ".py", ".js", ".html", ".svg", ".ps",
                ".exe", ".dll", ".so", ".lnk", ".pth"):
        assert ext not in MEDIA_EXTS, ext
    assert media_upload_suffix("recording") == ""
    assert media_upload_suffix(None, ".mp4") == ".mp4"
    assert media_upload_suffix("evil.conf") is None


def test_ws_tts_emotion_clip_must_live_in_app_folders(tmp_path, monkeypatch):
    from api.routers import tts_stream
    from core import config

    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "calm.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(config, "VOICES_DIR", str(voices))
    monkeypatch.setattr(config, "OUTPUTS_DIR", str(tmp_path / "outputs"))
    assert tts_stream.resolve_emotion_clip("calm.wav") == str((voices / "calm.wav").resolve())
    assert tts_stream.resolve_emotion_clip(str(voices / "calm.wav")).endswith("calm.wav")
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"RIFF")
    for value in (str(outside), "../secret.wav", "/etc/passwd", "", None):
        with pytest.raises(ValueError):
            tts_stream.resolve_emotion_clip(value)


# ── Connect-time guard (redirects, discovered URLs, the real yt-dlp) ─────


class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list

    def do_GET(self):  # noqa: N802 - stdlib hook name
        self.hits.append(self.path)
        location = getattr(self.server, "redirect_to", None)
        if location:
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><title>x</title></html>")

    def log_message(self, *args):
        pass


def _serve(host="127.0.0.1", redirect_to=None):
    hits = []
    handler = type("H", (_Recorder,), {"hits": hits})
    try:
        server = http.server.ThreadingHTTPServer((host, 0), handler)
    except OSError:
        pytest.skip(f"cannot bind {host}")
    server.redirect_to = redirect_to
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, hits


def test_guard_is_scoped_to_the_guarded_thread():
    with url_safety.guard_outbound_connections():
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_in_process_ytdlp_cannot_follow_a_redirect_to_a_private_host(monkeypatch):
    import yt_dlp

    target, target_hits = _serve("127.0.0.2")
    entry, entry_hits = _serve(redirect_to=f"http://127.0.0.2:{target.server_address[1]}/secret")
    # Treat the entry server as "public" so only the redirect target is private.
    monkeypatch.setattr(url_safety, "is_public_address", lambda ip: ip == "127.0.0.1")
    monkeypatch.setattr(url_safety, "_proxy_hosts", lambda: frozenset())
    try:
        with url_safety.guard_outbound_connections(), yt_dlp.YoutubeDL(
            {"quiet": True, "no_warnings": True, "retries": 0, "extractor_retries": 0, "proxy": ""}
        ) as ydl:
            with pytest.raises(yt_dlp.utils.DownloadError):
                ydl.extract_info(f"http://127.0.0.1:{entry.server_address[1]}/start", download=False)
    finally:
        entry.shutdown()
        target.shutdown()
    assert entry_hits == ["/start"]
    assert target_hits == []


def test_guarded_ytdlp_subprocess_never_connects_to_a_private_host(tmp_path):
    from services.media_tools import guarded_ytdlp_invocation

    server, hits = _serve()
    argv, env = guarded_ytdlp_invocation()
    env = dict(env or os.environ)
    env.pop(ALLOW_PRIVATE_ENV, None)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    try:
        result = subprocess.run(
            [*argv, "--simulate", "--retries", "0", "--", f"http://127.0.0.1:{server.server_address[1]}/x"],
            capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path,
        )
    finally:
        server.shutdown()
    assert argv[0] == sys.executable
    assert result.returncode != 0
    assert "private network address" in result.stderr
    assert hits == []


def test_batch_refuses_a_non_media_upload_before_the_speech_model_check(monkeypatch):
    """CI has no speech-to-text model; the answer must not depend on that."""
    import services.asr_backend as asr

    monkeypatch.setattr(
        asr,
        "asr_model_missing_error",
        lambda *a, **k: {"code": "asr_model_missing", "message": "missing"},
    )
    response = _client().post(
        "/batch/enqueue",
        files={"video": ("evil.conf", b"--exec touch /tmp/x\n", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_policy_check_sees_private_answers_inside_the_connect_guard(monkeypatch):
    """Inside the guard, ``socket.getaddrinfo`` hides private answers; the policy
    check must still see them and give the clear refusal, not pass the URL on."""
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", url_safety.socket.getaddrinfo)
    for answers in (("192.168.1.10",), (PUBLIC_IP, "192.168.1.10")):
        monkeypatch.setattr(url_safety, "_real_getaddrinfo", _answers(*answers))
        with url_safety.guard_outbound_connections():
            with pytest.raises(UnsafeURLError, match=ALLOW_PRIVATE_ENV):
                check_public_url("http://media.lan:9000/x")
