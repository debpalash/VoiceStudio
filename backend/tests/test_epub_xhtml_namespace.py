"""XHTML namespace aliases must not change a book's narrated paragraphs."""

import importlib
import io
import re
import zipfile

import pytest

@pytest.fixture
def importer():
    # Resolve after collection: other suites may replace services modules.
    return importlib.import_module("services.longform_import")


@pytest.mark.parametrize("prefix", ["x", "html"])
def test_prefixed_xhtml_matches_default_namespace(prefix, importer):
    plain = (
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Book metadata</title><style>not spoken</style></head>'
        '<body><h1>Chapter <em>One</em></h1>'
        '<script>not spoken</script><p>First paragraph.</p>'
        '<p>Second<br/>line.</p></body></html>'
    )
    prefixed = re.sub(r"(<\/?)([a-z][a-z0-9]*)", rf"\1{prefix}:\2", plain)
    prefixed = prefixed.replace('xmlns=', f'xmlns:{prefix}=')
    expected = ("Chapter One", "First paragraph.\n\nSecond\nline.")
    assert importer._html_to_title_body(plain) == expected
    assert importer._html_to_title_body(prefixed) == expected


def test_xhtml_namespace_alias_is_scoped_and_does_not_strip_foreign_prefix(importer):
    document = (
        '<x:html xmlns:x="http://www.w3.org/1999/xhtml">'
        '<x:body><x:h1>Chapter</x:h1><x:p>Before '
        '<x:span xmlns:x="urn:publisher"><x:script>spoken</x:script></x:span>'
        ' after.</x:p><x:p>Next paragraph.</x:p></x:body></x:html>'
    )
    assert importer._html_to_title_body(document) == (
        "Chapter", "Before spoken after.\n\nNext paragraph."
    )


def test_prefixed_xhtml_epub_keeps_heading_and_paragraphs(importer):
    document = (
        '<x:html xmlns:x="http://www.w3.org/1999/xhtml">'
        '<x:head><x:title>Metadata</x:title></x:head><x:body>'
        '<x:h1>The Arrival</x:h1><x:p>One.</x:p><x:p>Two.</x:p>'
        '</x:body></x:html>'
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("META-INF/container.xml", (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="book.opf"/></rootfiles></container>'
        ))
        archive.writestr("book.opf", (
            '<package xmlns="http://www.idpf.org/2007/opf">'
            '<manifest><item id="chapter" href="chapter.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>'
        ))
        archive.writestr("chapter.xhtml", document)
    assert importer.epub_to_chapter_script(stream.getvalue()) == '# The Arrival\n\nOne.\n\nTwo.'


def test_self_closing_xhtml_break_keeps_the_foreign_parent_binding(importer):
    # The inner XHTML <x:br/> is void; closing it must not pop the outer
    # foreign <x:br>, or the following foreign <x:script> reads as XHTML and
    # its words are dropped.
    document = (
        '<x:html xmlns:x="http://www.w3.org/1999/xhtml"><x:body><x:h1>Chapter</x:h1><x:p>'
        '<x:br xmlns:x="urn:publisher"><x:br xmlns:x="http://www.w3.org/1999/xhtml"/>'
        '<x:script>spoken</x:script></x:br></x:p></x:body></x:html>'
    )
    assert importer._html_to_title_body(document) == ("Chapter", "spoken")
