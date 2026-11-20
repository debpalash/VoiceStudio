"""``_expand`` must not re-substitute inside text it already produced.

The engines root is user-choosable (``OMNIVOICE_ENGINES_DIR``), so a path can
contain the literal ``{checkout}``. A chain of ``str.replace`` calls substituted
that token again inside the path it had just inserted, corrupting the ``uv``
argv and the ``python -c`` probe with a failure that looks like a broken engine
rather than a placeholder bug.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from services.sidecar_install import _expand  # noqa: E402


def test_a_checkout_path_containing_the_token_survives_expansion():
    checkout = pathlib.Path("/tmp/{checkout}/engines/indextts2")
    rendered = _expand("x = {checkout_repr}", checkout)

    # The repr of the real path, not the repr with the token substituted inside it.
    assert repr(str(checkout)) in rendered
    assert rendered.count("{checkout}") == 1, rendered


def test_each_placeholder_resolves_to_its_own_value():
    checkout = pathlib.Path("/engines/cosyvoice")
    rendered = _expand("uri={checkout_uri} repr={checkout_repr} raw={checkout}", checkout)

    assert f"uri={checkout.resolve().as_uri()}" in rendered
    assert f"repr={repr(str(checkout))}" in rendered
    assert rendered.endswith(f"raw={checkout}")
    assert "{" not in rendered.replace("{checkout}", "")


def test_text_without_placeholders_is_unchanged():
    checkout = pathlib.Path("/engines/x")
    assert _expand("uv pip install -e .", checkout) == "uv pip install -e ."


def test_an_unknown_brace_token_is_left_alone():
    checkout = pathlib.Path("/engines/x")
    assert _expand("--flag {not_a_placeholder}", checkout) == "--flag {not_a_placeholder}"
