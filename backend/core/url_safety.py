"""Destination policy for URLs the backend fetches on a caller's behalf.

URL imports (video ingest, voice-gallery clips) hand a caller-supplied URL to
yt-dlp. Without a destination policy that turns the backend into a proxy into
the user's own machine and network: loopback services, the LAN, router admin
pages and cloud metadata endpoints. Every URL import therefore goes through
three layers, all defined here so the policy cannot drift between callers:

1. :func:`check_public_url` — up-front validation for a clear 400: http(s)
   only, and every DNS answer for the host must be a public address.
2. :func:`guard_outbound_connections` — connect-time enforcement. While it is
   active, ``socket.getaddrinfo`` drops non-public answers for the current
   thread (or the whole process, in the yt-dlp subprocess). This covers what
   up-front validation cannot: HTTP redirects, URLs discovered inside the
   fetched page or manifest, and DNS answers that change between the check
   and the connection.
3. :func:`harden_ytdlp_options` — yt-dlp must do every fetch itself, in
   Python, where layer 2 applies. External downloaders (ffmpeg, aria2c, curl,
   rtmpdump) resolve and connect in their own process, so they are switched
   off, refused at run time, and live streams (which yt-dlp always hands to
   ffmpeg) are rejected. :func:`check_resolved_media_urls` adds a clear early
   error for media URLs that point at private hosts, and downloads that turn
   out to be a playlist or manifest rather than media are refused, because
   ffmpeg would follow the URLs inside it when the file is processed. Clips
   are cut locally from the downloaded file, never from a URL.

Private destinations are allowed only when the user opts in with
``OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS=1`` (for example to import from a media
server on their own network). Configured HTTP(S)/SOCKS proxies remain
reachable: with a proxy the proxy resolves the destination, so only layer 1
and 3 apply there.

Stdlib-only on purpose: the guarded yt-dlp subprocess imports this module
before anything else.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
import threading
import urllib.request
from collections.abc import Iterator
from urllib.parse import urlsplit

from core.user_env import DESKTOP_ENV_FILE_HINT

ALLOW_PRIVATE_ENV = "OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Carrier-grade NAT (RFC 6598) and the NAT64 well-known prefix (RFC 6052).
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

PRIVATE_DESTINATION_DETAIL = (
    "This URL points to a private network address (this computer, the local "
    "network, or a link-local service), which URL imports don't fetch. Use a "
    "public link or download the file and add it directly. To import from a "
    f"server on your own network, set {ALLOW_PRIVATE_ENV}=1 "
    f"({DESKTOP_ENV_FILE_HINT}) and restart VoiceStudio. A VPN or proxy app "
    "in TUN or fake-IP mode (such as Clash, Surge or sing-box, which answer "
    "with 198.18.x.x addresses) can cause this for public links too: turn on "
    "that mode's real-IP DNS option, or set the same variable."
)
LIVE_SOURCE_DETAIL = (
    "This link is a live stream or an upcoming premiere, which URL imports "
    "can't download. Import it after the broadcast has ended and the "
    "recording is available."
)
UNSUPPORTED_DOWNLOAD_DETAIL = (
    "This source needs an external downloader (such as ffmpeg) to fetch it, "
    "which URL imports don't use because it bypasses the private-network "
    "check. Download the file another way and add it directly."
)
NOT_MEDIA_DETAIL = (
    "This link returned a playlist or web page instead of an audio or video "
    "file, so it was not imported. Use a direct link to the video page or "
    "download the file and add it directly."
)
SCHEME_DETAIL = (
    "URL must start with http:// or https://. Paste a full link "
    "(e.g. https://youtube.com/watch?v=…) or add a local file instead."
)


class UnsafeURLError(ValueError):
    """A URL import was refused by the destination policy."""


def private_url_imports_allowed() -> bool:
    """Whether the user opted in to URL imports from private addresses."""
    return os.environ.get(ALLOW_PRIVATE_ENV, "").strip().lower() in _TRUTHY


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 transition form actually reaches, if any."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def is_public_address(value: str) -> bool:
    """True only for globally routable unicast addresses.

    Refuses loopback, RFC 1918/ULA private ranges, link-local (including the
    169.254.169.254 metadata service), CGNAT, multicast, unspecified,
    reserved and documentation ranges, and every IPv6 form that embeds one of
    those IPv4 addresses (IPv4-mapped, 6to4, Teredo, NAT64).
    """
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            address = embedded
    if (
        address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
    ):
        return False
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        return False
    return address.is_global


def _split_http_url(url: str):
    try:
        parsed = urlsplit(str(url).strip())
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise UnsafeURLError(SCHEME_DETAIL) from exc
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise UnsafeURLError(SCHEME_DETAIL)
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.hostname, port


def check_public_url(url: str) -> str:
    """Validate a caller-supplied import URL; return it stripped.

    Raises :class:`UnsafeURLError` (message suitable for a 400) when the
    scheme is not http(s) or any DNS answer is a non-public address. A host
    that does not resolve here is let through: DNS may only work through a
    configured proxy, and the connect-time guard still applies to any direct
    connection. Blocking — call from a worker thread in async code.
    """
    url = str(url or "").strip()
    host, port = _split_http_url(url)
    if private_url_imports_allowed():
        return url
    # Inside the connect guard, socket.getaddrinfo hides private answers (and
    # raises when only private ones exist); this policy check needs them all.
    resolve = _real_getaddrinfo if _guard_active() else socket.getaddrinfo
    try:
        answers = resolve(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return url
    if any(not is_public_address(answer[4][0]) for answer in answers):
        raise UnsafeURLError(PRIVATE_DESTINATION_DETAIL)
    return url


# Protocols yt-dlp downloads natively, in Python, under the socket guard.
# Anything else (rtmp via rtmpdump, rtsp/mms via ffmpeg, websocket via ffmpeg
# stdin) needs a helper process that connects on its own.
_NATIVE_PROTOCOLS = frozenset({
    "http", "https", "m3u8", "m3u8_native",
    "http_dash_segments", "http_dash_segments_generator", "ism", "f4m",
})
_LIVE_STATUSES = frozenset({"is_live", "is_upcoming"})


def check_ytdlp_source(info: dict) -> None:
    """Refuse a resolved yt-dlp download the guarded path can't fetch safely.

    Live streams are always handed to ffmpeg by yt-dlp; non-native protocols
    need an external program. Both connect outside the socket guard.
    """
    if info.get("is_live") or info.get("live_status") in _LIVE_STATUSES:
        raise UnsafeURLError(LIVE_SOURCE_DETAIL)
    formats = info.get("requested_formats") or [info]
    for fmt in formats:
        for protocol in str(fmt.get("protocol") or "").split("+"):
            if protocol and protocol not in _NATIVE_PROTOCOLS:
                raise UnsafeURLError(UNSUPPORTED_DOWNLOAD_DETAIL)
    check_resolved_media_urls(info)


def check_resolved_media_urls(info: dict) -> None:
    """Validate every media URL yt-dlp resolved for one download.

    Run before download so a media URL on a private host fails early with a
    clear message; the socket guard still covers every connection.
    """
    if private_url_imports_allowed():
        return
    formats = info.get("requested_formats") or [info]
    for fmt in formats:
        for key in ("url", "manifest_url", "fragment_base_url"):
            value = fmt.get(key)
            if value:
                check_public_url(value)


# Text that ffmpeg would treat as a playlist or manifest (HLS, concat, DASH and
# other XML, SDP) and follow the URLs inside. Real audio/video containers are
# binary and never start with these.
_MANIFEST_PREFIXES = (b"#EXTM3U", b"ffconcat", b"<", b"v=0")


def is_manifest_head(head: bytes) -> bool:
    """Whether the first bytes of a file are a playlist/manifest, not media."""
    return head.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(_MANIFEST_PREFIXES)


def is_manifest_file(path: str) -> bool:
    """Whether ``path`` holds a playlist/manifest that ffmpeg would follow.

    Shared by URL imports and local uploads: a file named ``clip.mp4`` whose
    content is an HLS playlist or concat list must never reach ffmpeg as
    media.
    """
    with open(path, "rb") as fh:
        return is_manifest_head(fh.read(512))


def check_downloaded_media(path: str) -> None:
    """Refuse a downloaded "media" file that is really a playlist/manifest."""
    if is_manifest_file(path):
        raise UnsafeURLError(NOT_MEDIA_DETAIL)


# ── Connect-time guard ──────────────────────────────────────────────────

_real_getaddrinfo = socket.getaddrinfo
_install_lock = threading.Lock()
_installed = threading.Event()
_process_wide = False
_thread_state = threading.local()


def _proxy_hosts() -> frozenset[str]:
    """Hostnames of the system/environment proxies yt-dlp would use."""
    hosts = set()
    try:
        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 - a broken proxy config must not break imports
        proxies = {}
    for name, value in proxies.items():
        if name == "no" or not value:
            continue
        try:
            host = urlsplit(value if "://" in value else f"http://{value}").hostname
        except ValueError:
            continue
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def _guard_active() -> bool:
    return _process_wide or getattr(_thread_state, "depth", 0) > 0


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    answers = _real_getaddrinfo(host, port, *args, **kwargs)
    if not _guard_active() or private_url_imports_allowed():
        return answers
    name = host.decode("idna") if isinstance(host, bytes) else str(host or "")
    proxies = getattr(_thread_state, "proxy_hosts", None)
    if proxies is None:
        proxies = _proxy_hosts()
    if name.strip("[]").lower() in proxies:
        return answers
    public = [answer for answer in answers if is_public_address(answer[4][0])]
    if not public:
        raise socket.gaierror(
            socket.EAI_NONAME,
            f"refusing to connect to a private network address ({ALLOW_PRIVATE_ENV}=1 allows it)",
        )
    return public


def _install() -> None:
    with _install_lock:
        if not _installed.is_set():
            socket.getaddrinfo = _guarded_getaddrinfo
            _installed.set()


@contextlib.contextmanager
def guard_outbound_connections() -> Iterator[None]:
    """Refuse non-public destinations for connections made by this thread."""
    _install()
    depth = getattr(_thread_state, "depth", 0)
    if depth == 0:
        _thread_state.proxy_hosts = _proxy_hosts()
    _thread_state.depth = depth + 1
    try:
        yield
    finally:
        _thread_state.depth = depth
        if depth == 0:
            _thread_state.proxy_hosts = None


def guard_process_connections() -> None:
    """Refuse non-public destinations for every connection in this process.

    For the dedicated yt-dlp subprocess only — never call it in the backend.
    """
    global _process_wide
    _install()
    _process_wide = True


# ── yt-dlp hardening ────────────────────────────────────────────────────

# Every fetch stays inside yt-dlp's own Python downloaders, where the socket
# guard applies. One fragment worker keeps HLS/DASH fragment fetches on the
# guarded thread (the in-process guard is per-thread).
YTDLP_NATIVE_OPTIONS = {
    "external_downloader": {"default": "native"},
    "hls_prefer_native": True,
    "concurrent_fragment_downloads": 1,
}

_downloader_guard_installed = threading.Event()


def _refuse_external_downloads() -> bool:
    return _guard_active() and not private_url_imports_allowed()


def _install_ytdlp_downloader_guard() -> None:
    """Refuse yt-dlp's external downloaders while the connect guard is active.

    The options above keep yt-dlp from choosing one, but its native HLS
    downloader still delegates to ffmpeg on its own for some streams (AES-128
    without pycryptodomex, unsupported playlist features). Reporting ffmpeg as
    unavailable makes it decrypt natively instead; anything that still reaches
    an external downloader fails rather than connecting unguarded.
    """
    from yt_dlp.downloader.external import ExternalFD, FFmpegFD
    from yt_dlp.utils import DownloadError

    with _install_lock:
        if _downloader_guard_installed.is_set():
            return
        real_download = ExternalFD.real_download
        ffmpeg_available = FFmpegFD.available.__func__

        def guarded_real_download(self, filename, info_dict):
            if _refuse_external_downloads():
                raise DownloadError(UNSUPPORTED_DOWNLOAD_DETAIL)
            return real_download(self, filename, info_dict)

        def guarded_available(cls, path=None):
            if _refuse_external_downloads():
                return False
            return ffmpeg_available(cls, path)

        ExternalFD.real_download = guarded_real_download
        FFmpegFD.available = classmethod(guarded_available)
        _downloader_guard_installed.set()


def _media_file_hook(status: dict) -> None:
    if status.get("status") != "finished" or not status.get("filename"):
        return
    try:
        check_downloaded_media(status["filename"])
    except UnsafeURLError as exc:
        from yt_dlp.utils import DownloadError

        with contextlib.suppress(OSError):
            os.remove(status["filename"])
        raise DownloadError(str(exc)) from exc


def harden_ytdlp_options(options: dict, *, media: bool = True) -> dict:
    """yt-dlp options for a caller-supplied URL, run under the connect guard.

    ``media=False`` is for subtitle-only passes, whose files are text by
    design and never reach ffmpeg. Pair with
    :func:`ytdlp_url_guard_postprocessor` (``when="before_dl"``).
    """
    _install_ytdlp_downloader_guard()
    hardened = {**options, **YTDLP_NATIVE_OPTIONS}
    if media:
        hardened["progress_hooks"] = [_media_file_hook, *(options.get("progress_hooks") or [])]
    return hardened


def ytdlp_url_guard_postprocessor():
    """A ``before_dl`` yt-dlp post-processor enforcing the source policy."""
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import DownloadError

    class _MediaURLGuard(PostProcessor):
        def run(self, info):
            try:
                check_ytdlp_source(info)
            except UnsafeURLError as exc:
                raise DownloadError(str(exc)) from exc
            return [], info

    return _MediaURLGuard()


def run_guarded_ytdlp(argv: list[str]) -> int:
    """yt-dlp CLI entry point with the destination policy enforced.

    Used by ``python -c`` in the gallery subprocess (see
    ``services.media_tools.guarded_ytdlp_invocation``).
    """
    guard_process_connections()
    import yt_dlp

    parsed = yt_dlp.parse_options(argv)
    with yt_dlp.YoutubeDL(harden_ytdlp_options(parsed.ydl_opts)) as ydl:
        ydl.add_post_processor(ytdlp_url_guard_postprocessor(), when="before_dl")
        return ydl.download(parsed.urls)
