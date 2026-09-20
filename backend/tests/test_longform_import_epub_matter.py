"""EPUB import narrates the book, not its print furniture (#2208).

A publisher EPUB carries print page numbers as ``epub:type="pagebreak"``
spans — inline they glued onto prose ("happily as 2Zoe threw"), block-level
they became lone "120" / "iv" paragraphs. Its spine also starts with a
title page, dedication and teaser and ends with the copyright page; each
came through as a "Chapter N". And the chapter heading lived in the nav
table of contents ("Chapter One: A New Arrival") while the ``<h1>`` held only
the subtitle. The extractor now drops page-number markup, skips front and
back matter (``epub:type`` first, title fallback), and titles chapters from
the TOC.
"""

import io
import zipfile

import pytest

from services import longform_import as li

_CONTAINER = (
    '<?xml version="1.0"?>'
    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
    '<rootfiles><rootfile full-path="OPS/package.opf"'
    ' media-type="application/oebps-package+xml"/></rootfiles></container>'
)


def _doc(body: str, *, title: str = "", section_type: str | None = None) -> str:
    sec = f' epub:type="{section_type}"' if section_type else ""
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>{title}</title></head><body><section{sec}>{body}</section></body></html>"
    )


def _epub(docs: dict[str, str], *, nav: str | None = None, ncx: str | None = None, linear_no=()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", _CONTAINER)
        manifest, spine = [], []
        if nav is not None:
            z.writestr("OPS/TOC.xhtml", nav)
            manifest.append('<item id="toc" href="TOC.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        if ncx is not None:
            z.writestr("OPS/toc.ncx", ncx)
            manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        for i, (name, xhtml) in enumerate(docs.items()):
            z.writestr(f"OPS/{name}", xhtml)
            manifest.append(f'<item id="d{i}" href="{name}" media-type="application/xhtml+xml"/>')
            linear = ' linear="no"' if name in linear_no else ""
            spine.append(f'<itemref idref="d{i}"{linear}/>')
        z.writestr(
            "OPS/package.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>'
            f"<manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>",
        )
    return buf.getvalue()


_NAV = (
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
    '<nav epub:type="toc"><ol>'
    '<li><a href="title.xhtml">Title Page</a></li>'
    '<li><a href="ch1.xhtml">Chapter One: A New Arrival</a></li>'
    '<li><a href="ch2.xhtml#start">Chapter Two: Too Many Questions</a></li>'
    "</ol></nav></body></html>"
)


def _publisher_epub() -> bytes:
    return _epub(
        {
            "cover.xhtml": _doc('<img src="c.jpg"/>', section_type="cover"),
            "title.xhtml": _doc("<p>The Talkative Tiger</p><p>Amelia Cobb</p>", section_type="frontmatter titlepage"),
            "teaser.xhtml": _doc(
                '<span epub:type="pagebreak" id="pi">i</span><p>“What is it?” chattered Meep.</p>',
                section_type="frontmatter",
            ),
            "dedication.xhtml": _doc("<p>For John Arthur</p>", section_type="frontmatter dedication"),
            "ch1.xhtml": _doc(
                '<div><span class="pagebreak-rw" epub:type="pagebreak" id="p1">1</span>'
                '<p class="title-num-rw">Chapter One</p><h1>A New Arrival</h1></div>'
                "<p>Alex and Nina, whistled happily as "
                '<span epub:type="pagebreak" id="p2">2</span>Zoe threw more fish.</p>'
                '<p>She handed it to Meep.<span role="doc-pagebreak" id="p3">3</span></p>'
                '<p><a class="page-number" id="p4">4</a>Someone wasn’t excited though.</p>',
                title="A New Arrival",
                section_type="bodymatter chapter",
            ),
            "ch2.xhtml": _doc(
                '<span epub:type="pagebreak" id="p5">5</span>'
                '<h1 id="start">Too Many Questions</h1><p class="page-number">120</p><p>“Fish for breakfast!”</p><p role="doc-pagebreak">iv</p><p>I</p>',
                title="Too Many Questions",
                section_type="bodymatter chapter",
            ),
            "notes.xhtml": _doc("<p>Printer notes.</p>", title="Notes"),
            "copyright.xhtml": _doc(
                "<p>First published in the UK in 2021</p><p>ISBN: 978 1 78800 935 5</p>",
                section_type="backmatter copyright-page",
            ),
        },
        nav=_NAV,
        linear_no=("notes.xhtml",),
    )


def test_page_numbers_never_reach_the_narration():
    script = li.epub_to_chapter_script(_publisher_epub())
    assert "whistled happily as Zoe threw more fish." in script  # inline pagebreak span gone
    assert "She handed it to Meep.\n" in script or script.endswith("She handed it to Meep.")
    assert "Meep.3" not in script and "2Zoe" not in script and "4Someone" not in script
    lines = [ln.strip() for ln in script.split("\n")]
    assert "120" not in lines and "iv" not in lines and "i" not in lines
    assert "I" in lines  # an uppercase numeral (or the pronoun) is prose, not a folio


def test_front_and_back_matter_are_skipped_and_chapters_take_toc_titles():
    script = li.epub_to_chapter_script(_publisher_epub())
    heads = [ln for ln in script.split("\n") if ln.startswith("# ")]
    assert heads == ["# Chapter One: A New Arrival", "# Chapter Two: Too Many Questions"]
    for furniture in ("Talkative Tiger", "Amelia Cobb", "For John Arthur", "chattered Meep", "ISBN", "First published"):
        assert furniture not in script
    assert "Printer notes" not in script  # linear="no"
    assert "Chapter One\n" in script  # the printed chapter label is still narrated


def test_untagged_epub_falls_back_to_title_heuristics():
    """No epub:type anywhere (EPUB 2 era): the section title decides."""
    epub = _epub(
        {
            "front.xhtml": _doc("<h1>Copyright</h1><p>All rights reserved.</p>", title="Copyright"),
            "toc.xhtml": _doc("<h1>Contents</h1><p>Chapter 1</p>", title="Contents"),
            "c1.xhtml": _doc("<h1>Chapter 1</h1><p>Once upon a time.</p>", title="Chapter 1"),
            "c2.xhtml": _doc("<h2>The Return</h2><p>And then.</p>", title="The Return"),
        },
        ncx=(
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
            "<navPoint><navLabel><text>Chapter 1: The Departure</text></navLabel><content src=\"c1.xhtml\"/></navPoint>"
            "<navPoint><navLabel><text>Chapter 2: The Return</text></navLabel><content src=\"c2.xhtml#top\"/></navPoint>"
            "</navMap></ncx>"
        ),
    )
    script = li.epub_to_chapter_script(epub)
    heads = [ln for ln in script.split("\n") if ln.startswith("# ")]
    assert heads == ["# Chapter 1: The Departure", "# Chapter 2: The Return"]
    assert "All rights reserved" not in script and "Contents" not in script


def test_section_typed_as_body_matter_survives_an_ancillary_looking_title():
    epub = _epub(
        {"c1.xhtml": _doc("<h1>Acknowledgments</h1><p>A chapter really named that.</p>", section_type="bodymatter chapter")},
    )
    assert "A chapter really named that." in li.epub_to_chapter_script(epub)


def test_toc_parsing_accepts_single_quoted_attributes():
    nav = _NAV.replace('href="ch1.xhtml"', "href='ch1.xhtml'").replace('href="ch2.xhtml#start"', "href='ch2.xhtml#start'")
    script = li.epub_to_chapter_script(
        _epub(
            {
                "ch1.xhtml": _doc("<h1>A New Arrival</h1><p>One.</p>", section_type="bodymatter chapter"),
                "ch2.xhtml": _doc("<h1>Too Many Questions</h1><p>Two.</p>", section_type="bodymatter chapter"),
            },
            nav=nav,
        )
    )
    assert [ln for ln in script.split("\n") if ln.startswith("# ")] == [
        "# Chapter One: A New Arrival",
        "# Chapter Two: Too Many Questions",
    ]
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        "<navPoint><navLabel><text>Part One</text></navLabel><content src='ch1.xhtml'/></navPoint></navMap></ncx>"
    )
    script = li.epub_to_chapter_script(
        _epub({"ch1.xhtml": _doc("<h1>A New Arrival</h1><p>One.</p>", section_type="bodymatter chapter")}, ncx=ncx)
    )
    assert script.startswith("# Part One\n")


def test_oversized_nav_document_is_skipped_not_decompressed():
    """A nav/NCX member is user-supplied like any other: the zip-bomb limits apply before it is read."""
    huge_nav = _NAV.replace("</ol>", "<li>" + "x" * 5000 + "</li></ol>")
    docs = {"ch1.xhtml": _doc("<h1>A New Arrival</h1><p>One.</p>", section_type="bodymatter chapter")}
    script = li.epub_to_chapter_script(_epub(docs, nav=huge_nav), max_entry_bytes=4000)
    assert script.startswith("# A New Arrival\n")  # heading fallback: the nav was never read
    # A nav that fits counts toward the shared total budget, so a ceiling it
    # nearly fills leaves nothing for the chapters — reported, not silently empty.
    data = _epub(docs, nav=_NAV)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        overhead = sum(info.file_size for info in archive.infolist() if info.filename.endswith(('container.xml', '.opf')))
    with pytest.raises(ValueError, match="no readable chapters"):
        li.epub_to_chapter_script(data, max_total_bytes=overhead + len(_NAV) + 10)


def test_bare_numbers_are_prose_even_when_the_book_is_paginated():
    """Pagination elsewhere does not make a year or Roman numeral disposable."""
    body = "<h1>Orwell</h1><p>1984</p><p>IV</p><p>civil</p><p>iv</p>"
    plain = li.epub_to_chapter_script(_epub({"c.xhtml": _doc(body, section_type="bodymatter chapter")}))
    assert all(x in plain.split("\n") for x in ("1984", "IV", "civil", "iv"))
    paginated = li.epub_to_chapter_script(
        _epub({"c.xhtml": _doc('<span epub:type="pagebreak">7</span>' + body, section_type="bodymatter chapter")})
    )
    lines = paginated.split("\n")
    assert "IV" in lines and "civil" in lines
    assert "iv" in lines and "1984" in lines and "7" not in lines


def test_only_the_toc_nav_names_sections_and_it_beats_the_ncx():
    """A landmarks nav links the same chapter first under "Start of Content";
    an NCX listed before the nav in the manifest carries an older label. The
    ``epub:type="toc"`` nav wins both."""
    nav = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        '<nav epub:type="landmarks"><ol><li><a epub:type="bodymatter" href="ch1.xhtml">Start of Content</a></li></ol></nav>'
        '<nav epub:type="toc"><ol><li><a href="ch1.xhtml">Chapter One: A New Arrival</a></li></ol></nav>'
        "</body></html>"
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        '<navPoint><navLabel><text>Old NCX label</text></navLabel><content src="ch1.xhtml"/></navPoint></navMap></ncx>'
    )
    docs = {"ch1.xhtml": _doc("<h1>A New Arrival</h1><p>One.</p>", section_type="bodymatter chapter")}
    script = li.epub_to_chapter_script(_epub(docs, nav=nav, ncx=ncx))
    assert script.startswith("# Chapter One: A New Arrival\n")


def test_unreadable_member_is_reported_as_a_value_error(monkeypatch):
    """A CRC-broken or encrypted member must not leak a raw zipfile error."""
    epub = _publisher_epub()
    real_read = li.zipfile.ZipFile.read

    def broken_read(self, name, *args, **kwargs):
        if name.endswith("ch1.xhtml"):
            raise li.zipfile.BadZipFile("Bad CRC-32 for file 'OPS/ch1.xhtml'")
        return real_read(self, name, *args, **kwargs)

    monkeypatch.setattr(li.zipfile.ZipFile, "read", broken_read)
    with pytest.raises(ValueError, match="unreadable"):
        li.epub_to_chapter_script(epub)
    monkeypatch.setattr(li.zipfile.ZipFile, "read", real_read)
    assert "# Chapter One: A New Arrival" in li.epub_to_chapter_script(epub)


def test_oversized_container_or_opf_is_refused_before_decompression():
    """The size guard applies to container.xml and the OPF too, not only the spine."""
    docs = {"ch1.xhtml": _doc("<h1>A New Arrival</h1><p>One.</p>", section_type="bodymatter chapter")}
    epub = _epub(docs)
    # A limit smaller than the (tiny) container.xml: nothing may be decompressed.
    with pytest.raises(ValueError, match="container.xml.*size limit"):
        li.epub_to_chapter_script(epub, max_entry_bytes=64)
    # A limit that admits container.xml but not the OPF.
    with pytest.raises(ValueError, match="package.opf.*size limit"):
        li.epub_to_chapter_script(epub, max_entry_bytes=len(_CONTAINER) + 16)
    # A missing OPF is reported the same way (not a KeyError).
    import io as _io
    import zipfile as _zip

    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", _CONTAINER)
    with pytest.raises(ValueError, match="package.opf.*missing"):
        li.epub_to_chapter_script(buf.getvalue())


@pytest.mark.parametrize('prefix', ['epub', 'book'])
def test_toc_uses_namespace_uri_and_exact_attributes(prefix):
    nav = (
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:{prefix}="http://www.idpf.org/2007/ops" xmlns:data-{prefix}="urn:custom"><body>'
        f'<nav data-{prefix}:type="toc"><a href="c.xhtml">Fake TOC</a></nav>'
        f'<nav {prefix}:type="landmarks"><a href="c.xhtml">Start of Content</a></nav>'
        f'<nav {prefix}:type="toc"><a data-href="c.xhtml">Not a link</a>'
        '<a href="c.xhtml#start">Real <em>chapter</em></a></nav></body></html>'
    )
    script = li.epub_to_chapter_script(_epub({'c.xhtml': _doc('<h1>Short</h1><p>Body.</p>')}, nav=nav))
    assert script.startswith('# Real chapter\n')


def test_prefixed_ncx_uses_exact_content_source():
    ncx = (
        '<n:ncx xmlns:n="http://www.daisy.org/z3986/2005/ncx/"><n:navMap>'
        '<n:navPoint><n:navLabel><n:text>Real chapter</n:text></n:navLabel>'
        '<n:content data-src="wrong.xhtml" src="c.xhtml#start"/></n:navPoint></n:navMap></n:ncx>'
    )
    script = li.epub_to_chapter_script(_epub({'c.xhtml': _doc('<h1>Short</h1><p>Body.</p>')}, ncx=ncx))
    assert script.startswith('# Real chapter\n')


@pytest.mark.parametrize('prefix', ['epub', 'book'])
def test_main_element_semantics_and_pagebreak_namespace(prefix):
    front = _doc('<p>Not narration.</p>').replace('<section', '<main epub:type="frontmatter titlepage"').replace('</section>', '</main>')
    front = front.replace('xmlns:epub=', f'xmlns:{prefix}=').replace('epub:type=', f'{prefix}:type=')
    chapter = _doc('<p>Hello<span epub:type="pagebreak">12</span> world.</p>')
    chapter = chapter.replace('xmlns:epub=', f'xmlns:{prefix}=').replace('epub:type=', f'{prefix}:type=')
    script = li.epub_to_chapter_script(_epub({'front.xhtml': front, 'c.xhtml': chapter}))
    assert 'Not narration.' not in script
    assert 'Hello world.' in script


@pytest.mark.parametrize('marker', ['<span epub:type="pagebreak"><img src="p.png">12</span>', '<br role="doc-pagebreak">'])
def test_pagebreak_void_elements_do_not_swallow_following_prose(marker):
    script = li.epub_to_chapter_script(_epub({'c.xhtml': _doc(f'<h1>Chapter</h1><p>Before {marker}after.</p>')}))
    assert 'Before after.' in script


@pytest.mark.parametrize(
    'marker',
    ['<span epub:type="pagebreak"/>', '<div role="doc-pagebreak"/>'],
)
def test_self_closing_pagebreak_does_not_swallow_following_prose(marker):
    """HTMLParser balances start-end tags through handle_startendtag by default."""
    script = li.epub_to_chapter_script(
        _epub({'c.xhtml': _doc(f'<h1>Chapter</h1><p>Before{marker}After.</p>')})
    )
    assert 'BeforeAfter.' in script


def test_layout_page_break_class_is_not_a_page_number():
    script = li.epub_to_chapter_script(_epub({'c.xhtml': _doc('<h1>Chapter</h1><p class="page-break-before">Keep this paragraph.</p>')}))
    assert 'Keep this paragraph.' in script


def test_malformed_container_is_a_value_error():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('META-INF/container.xml', '<container>')
    with pytest.raises(ValueError, match='container.*XML'):
        li.epub_to_chapter_script(buf.getvalue())

def test_semantic_namespace_scope_is_restored_after_nested_override():
    body = (
        '<div xmlns:book="urn:custom"><p><span book:type="pagebreak">Keep</span></p></div>'
        '<p><span book:type="pagebreak">99</span>After.</p>'
    )
    doc = _doc(body).replace('xmlns:epub=', 'xmlns:book=')
    script = li.epub_to_chapter_script(_epub({'c.xhtml': doc}))
    assert 'Keep' in script and 'After.' in script and '99' not in script


def test_malformed_optional_navigation_preserves_heading_and_body():
    script = li.epub_to_chapter_script(_epub({'c.xhtml': _doc('<h1>Chapter</h1><p>Body.</p>')}, nav='<html>'))
    assert script == '# Chapter\n\nBody.'


def test_nav_without_nav_element_does_not_override_chapter_heading():
    script = li.epub_to_chapter_script(_epub({'c.xhtml': _doc('<h1>Chapter</h1><p>Body.</p>')}, nav='<html><body><a href="c.xhtml">Advertisement</a></body></html>'))
    assert script.startswith('# Chapter\n')


def test_required_members_consume_total_budget_before_decompression():
    from unittest.mock import patch
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('container.xml', '12345')
        archive.writestr('book.opf', '12345')
    with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive:
        budget = li._ReadBudget(10, 9)
        assert li._read_member(archive, 'container.xml', budget, required=True) == b'12345'
        assert budget.used == 5
        with patch.object(archive, 'read', wraps=archive.read) as read:
            with pytest.raises(ValueError, match='size limit'):
                li._read_member(archive, 'book.opf', budget, required=True)
            read.assert_not_called()
