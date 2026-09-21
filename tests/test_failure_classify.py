"""ASR-robustness failure classification (#551 / #549).

The dub/transcribe "no segments" toast is only actionable if `classify()` names
the failure class so `build_failure()` can attach a hint. These assert the two
new taxonomy classes added for the ASR-robustness fix map to a non-empty hint.
"""
from core import failure


def test_classify_compute_type_unsupported():
    # The exact CTranslate2 message on a GPU without efficient fp16 (#551).
    reason = (
        "Requested float16 compute type, but the target device or backend do "
        "not support efficient float16 computation"
    )
    assert failure.classify(reason) == "COMPUTE_TYPE_UNSUPPORTED"
    evt = failure.build_failure(reason, stage="transcribe", include_diagnostic=False)
    assert evt["docs_topic"] == "COMPUTE_TYPE_UNSUPPORTED"
    assert evt["hint"], "compute-type failure must carry an actionable hint"


def test_classify_transformers_import():
    # The transformers ASR-pipeline import failure (#549).
    assert failure.classify("Could not import module 'AutoFeatureExtractor'") == (
        "TRANSFORMERS_IMPORT"
    )
    # Substring match on the bare class name too (case-insensitive).
    assert failure.classify("AutoFeatureExtractor failed to load") == "TRANSFORMERS_IMPORT"
    evt = failure.build_failure(
        "Could not import module 'AutoFeatureExtractor'",
        stage="transcribe",
        include_diagnostic=False,
    )
    assert evt["hint"], "transformers-import failure must carry an actionable hint"


def test_classify_corrupted_transformers_file():
    # A missing transformers module file (interrupted uv sync / AV / partial
    # update) surfaces as FileNotFoundError, not ImportError — it must still
    # classify as TRANSFORMERS_IMPORT so the user gets "reinstall", not "restart".
    posix = (
        "[Errno 2] No such file or directory: "
        "'/Users/u/Library/Application Support/com.x/project/.venv/lib/python3.11/"
        "site-packages/transformers/models/qwen3/modeling_qwen3.py'"
    )
    win = (
        "[Errno 2] No such file or directory: "
        r"'C:\Users\u\AppData\Local\com.x\project\.venv\Lib\site-packages\transformers"
        r"\models\qwen3\modeling_qwen3.py'"
    )
    assert failure.classify(posix) == "TRANSFORMERS_IMPORT"
    assert failure.classify(win) == "TRANSFORMERS_IMPORT"
    f = failure.build_failure(FileNotFoundError(posix), stage="model-load", include_diagnostic=False)
    assert "reinstall" in f["hint"].lower()
    # A missing file from an UNRELATED package must NOT be mislabelled as transformers.
    assert failure.classify("[Errno 2] No such file or directory: '/x/site-packages/numpy/core/foo.py'") == ""


def test_classify_os_invalid_argument_einval():
    # #763: a per-chunk temp-WAV write failing with EINVAL surfaced as the
    # dead-end "produced no segments. [Errno 22] Invalid argument" toast. It must
    # now classify so build_failure attaches a temp-dir/disk/AV hint. This is the
    # exact string the streaming dub path aggregates and feeds build_failure.
    reason = "[Errno 22] Invalid argument"
    assert failure.classify(reason) == "OS_INVALID_ARGUMENT"
    evt = failure.build_failure(reason, stage="transcribe", include_diagnostic=False)
    assert evt["docs_topic"] == "OS_INVALID_ARGUMENT"
    assert evt["hint"], "an EINVAL transcribe failure must carry an actionable hint"
    assert "temp" in evt["hint"].lower()
    # The errno-22 rule must NOT swallow the errno-2 transformers class (its
    # markers still win) or fire on an unrelated errno.
    tf = (
        "[Errno 2] No such file or directory: "
        "'/x/site-packages/transformers/models/qwen3/modeling_qwen3.py'"
    )
    assert failure.classify(tf) == "TRANSFORMERS_IMPORT"
    assert failure.classify("[Errno 13] Permission denied") == ""


def test_classify_video_download_classes():
    # #554: a non-downloadable link shape → actionable "paste a direct video URL".
    assert failure.classify("Unsupported URL: https://www.douyin.com/discover") == (
        "UNSUPPORTED_VIDEO_URL"
    )
    # #536: a transient mid-download drop → "just retry".
    assert failure.classify("Unable to download video: [Errno 32] Broken pipe") == (
        "VIDEO_DOWNLOAD_NETWORK"
    )
    assert failure.classify("Connection reset by peer") == "VIDEO_DOWNLOAD_NETWORK"
    for cls, reason in (
        ("UNSUPPORTED_VIDEO_URL", "Unsupported URL: x"),
        ("VIDEO_DOWNLOAD_NETWORK", "Unable to download video: Broken pipe"),
    ):
        evt = failure.build_failure(reason, stage="download", include_diagnostic=False)
        assert evt["docs_topic"] == cls
        assert evt["hint"], f"{cls} must carry an actionable hint"


def test_classify_broken_venv_encodings():
    # The relocated/corrupted-venv stdlib-bootstrap failure → BROKEN_VENV (the
    # Rust self-heal rebuilds it; this names the class for the toast).
    assert failure.classify("ModuleNotFoundError: No module named 'encodings'") == (
        "BROKEN_VENV"
    )
    # ...but an app-level import of an 'encodings'-prefixed package must NOT.
    assert failure.classify("No module named 'encodings_helper'") == ""


def test_classify_broken_venv_missing_own_package():
    # #564: the interpreter starts but the backend can't import its own
    # 'omnivoice' package (editable install missing) → BROKEN_VENV so the toast
    # points at the self-heal / Clean & Retry instead of a bare import error.
    assert failure.classify("ModuleNotFoundError: No module named 'omnivoice'") == (
        "BROKEN_VENV"
    )
    # ...but a legitimately-named 'omnivoice_*' helper package must NOT match
    # (the trailing quote in the matcher is the guard).
    assert failure.classify("No module named 'omnivoice_helper'") == ""


def test_classify_socks_proxy_support_missing():
    # #959: the exact httpx message at client CONSTRUCTION under a socks5://
    # proxy env without socksio — it surfaced as a bare 500 from /generate
    # (huggingface_hub's get_session() builds the client inside model load).
    reason = (
        "Using SOCKS proxy, but the 'socksio' package is not installed. "
        "Make sure to install httpx using `pip install httpx[socks]`."
    )
    assert failure.classify(reason) == "SOCKS_PROXY_SUPPORT_MISSING"
    evt = failure.build_failure(
        ImportError(reason), stage="model-load", include_diagnostic=False
    )
    assert evt["docs_topic"] == "SOCKS_PROXY_SUPPORT_MISSING"
    assert evt["hint"], "the SOCKS-proxy class must carry an actionable hint"
    assert "ALL_PROXY" in evt["hint"]
    # append_hint is the raw-string surface (main.py's global 500 handler,
    # the model-install SSE) — the detail keeps the real error AND gains the
    # hint, and stays a pass-through for unknown reasons.
    out = failure.append_hint(reason)
    assert out.startswith(reason) and "ALL_PROXY" in out
    assert failure.append_hint("some unrelated failure") == "some unrelated failure"
    # A generic proxy connectivity error must NOT be mislabelled.
    assert failure.classify("ProxyError: connection refused by 10.0.0.1:8080") == ""


def test_classify_ssl_handshake_failure():
    # #976: the exact error a Windows user behind a corporate/antivirus
    # TLS-inspecting proxy sees on every model install — the TCP connection
    # succeeds, but the handshake fails because the OS trusts the proxy's
    # re-signed CA and Python's bundled certifi list doesn't. A different
    # failure mode from #984's TCP-level "host unreachable" fix.
    reason = (
        "Install failed: Got: ConnectError: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] "
        "ssl/tls alert handshake failure (_ssl.c:1016)"
    )
    assert failure.classify(reason) == "SSL_HANDSHAKE_FAILURE"
    evt = failure.build_failure(reason, stage="install", include_diagnostic=False)
    assert evt["docs_topic"] == "SSL_HANDSHAKE_FAILURE"
    assert evt["hint"], "the SSL-handshake class must carry an actionable hint"
    # A CERTIFICATE_VERIFY_FAILED-style message (the other common corporate-MITM
    # shape) must classify the same way.
    cert_reason = (
        "requests.exceptions.SSLError: HTTPSConnectionPool(host='huggingface.co', "
        "port=443): Max retries exceeded with url: / (Caused by SSLError("
        "SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate "
        "verify failed: unable to get local issuer certificate')))"
    )
    assert failure.classify(cert_reason) == "SSL_HANDSHAKE_FAILURE"
    # append_hint is the raw-string surface (setup/download.py's install SSE) —
    # the detail keeps the real error AND gains the hint.
    out = failure.append_hint(reason)
    assert out.startswith(reason) and "truststore" in out
    # A plain, unrelated connection error must NOT be mislabelled as SSL.
    assert failure.classify("ConnectionError: connection refused") == ""


def test_classify_generic_still_empty():
    # A genuinely unknown reason must still classify to "" (no false hint).
    assert failure.classify("some totally unrelated failure") == ""


def test_transformers_import_hint_names_the_package_that_actually_breaks():
    """#1376: the hint told users to reinstall torch+torchaudio+transformers —
    omitting torchvision, the package whose ABI mismatch produces this exact
    lazy-import wording (#1357's `torchvision::nms`). Following the advice to
    the letter left the broken package untouched.

    It must also point at the pinned constraint file: an unpinned reinstall of
    the trio can itself resolve a drifted pair, which is the bug the pins
    exist to prevent.
    """
    hint = failure._HINTS["TRANSFORMERS_IMPORT"]
    assert "torchvision" in hint


def test_transformers_import_hint_versions_match_the_constraint_file():
    """The hint carries LITERAL pins — desktop installs don't ship deploy/, so
    a `--constraint deploy/torch-constraints.txt` command fails with "file not
    found" for exactly the users most likely to need it (greptile on #1377).
    Literals drift, so this locks them to the constraint file: bump the pins,
    and this fails until every advice surface says the new versions."""
    import os
    import re

    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "deploy", "torch-constraints.txt")) as fh:
        pins = dict(
            re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", ln.split("#")[0].strip()).groups()
            for ln in fh
            if re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", ln.split("#")[0].strip())
        )

    surfaces = {
        "core/failure.py hint": failure._HINTS["TRANSFORMERS_IMPORT"],
    }
    with open(os.path.join(root, "docs", "install", "troubleshooting.md")) as fh:
        surfaces["troubleshooting.md"] = fh.read()

    # The COMMAND, not isolated substrings (CodeRabbit): a surface could carry
    # the right pin in a comment while its actual reinstall line says something
    # else. Normalizing collapses the asr_backend source's string-literal line
    # breaks so the assertion sees what the user sees.
    command = (
        f"uv pip install --python .venv --reinstall torch=={pins['torch']} "
        f"torchaudio=={pins['torchaudio']} torchvision=={pins['torchvision']} "
        f"transformers"
    )

    def _normalize(text):
        return re.sub(r"[\s\"\\]+", " ", text)

    for name, text in surfaces.items():
        assert command in _normalize(text), (
            f"{name} does not carry the exact pinned reinstall command "
            f"({command!r}) — either a pin drifted from "
            f"deploy/torch-constraints.txt or the command was reworded; "
            f"following stale advice would recreate the mismatch it fixes"
        )
