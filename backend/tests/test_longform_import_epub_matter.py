"""EPUB semantics, navigation, and reading-order regression coverage."""

import io
import zipfile

import pytest


@pytest.fixture(autouse=True)
def _runtime_import_module():
    global li
    from services import longform_import
    li = longform_import

_OPF_NS = "http://www.idpf.org/2007/opf"
_OPS_NS = "http://www.idpf.org/2007/ops"


def _epub(docs: dict[str, str], *, nav=None, ncx=None, linear_no=(), guide=None) -> bytes:
    """Assemble a minimal EPUB in memory: one OPF under OPS/ plus the given parts."""
    manifest = []
    spine = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OPS/book.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        for position, (href, xhtml) in enumerate(docs.items()):
            item_id = f"part{position}"
            zf.writestr(f"OPS/{href}", xhtml)
            manifest.append(
                f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>'
            )
            skip = href in linear_no or position in linear_no
            spine.append(
                f'<itemref idref="{item_id}"' + (' linear="no"' if skip else "") + "/>"
            )
        if nav is not None:
            if not nav.lstrip().startswith("<html"):
                nav = f'<html xmlns:epub="{_OPS_NS}"><body>' + nav
            zf.writestr("OPS/TOC.xhtml", nav)
            manifest.append(
                '<item id="navdoc" href="TOC.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            )
        spine_attrs = ""
        if ncx is not None:
            zf.writestr("OPS/toc.ncx", ncx)
            manifest.append('<item id="ncxdoc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
            spine_attrs = ' toc="ncxdoc"'
        opf = (
            f'<?xml version="1.0"?><package xmlns="{_OPF_NS}" version="3.0">'
            f"<manifest>{''.join(manifest)}</manifest>"
            f"<spine{spine_attrs}>{''.join(spine)}</spine>"
            f"{guide or ''}</package>"
        )
        zf.writestr("OPS/book.opf", opf)
    return buf.getvalue()


def _doc(body: str) -> str:
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><body><section>'
        f"{body}</section></body></html>"
    )

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


# ── unmarked front matter and stray back matter ────────────────────────────

_WORDS = " ".join(["word"] * 450)  # comfortably over the 400-word furniture ceiling


def _plain_nav(entries: list[tuple[str, str]], landmarks: str = "") -> str:
    items = "".join(f'<li><a href="{h}">{label}</a></li>' for h, label in entries)
    return (
        f'<nav epub:type="toc"><ol>{items}</ol></nav>{landmarks}</body></html>'
    )


def _landmarks(target: str) -> str:
    return (
        '<nav epub:type="landmarks"><ol>'
        f'<li><a epub:type="bodymatter" href="{target}">Start</a></li></ol></nav>'
    )


def _heads(script: str) -> list[str]:
    return [ln for ln in script.split("\n") if ln.startswith("# ")]


def _guide(target: str, kind: str = "text") -> str:
    return f'<guide><reference type="{kind}" href="{target}" title="Start"/></guide>'


def _book(extra_docs: dict[str, str], *, nav=None, guide=None) -> bytes:
    docs = {
        "teaser.xhtml": _doc("<p>A teaser excerpt of a few words.</p>"),
        "c1.xhtml": _doc("<h1>One</h1><p>First chapter text.</p>"),
        "c2.xhtml": _doc("<h1>Two</h1><p>Second chapter text.</p>"),
        **extra_docs,
    }
    return _epub(docs, nav=nav, guide=guide)


_TWO_CHAPTER_NAV = _plain_nav([("c1.xhtml", "One"), ("c2.xhtml", "Two")])


def test_guide_text_reference_declares_the_start_and_drops_the_teaser():
    script = li.epub_to_chapter_script(_book({}, nav=None, guide=_guide("c1.xhtml")))
    assert "teaser excerpt" not in script
    assert _heads(script) == ["# One", "# Two"]


def test_guide_reference_type_is_case_insensitive_and_other_types_ignored():
    upper = li.epub_to_chapter_script(_book({}, guide=_guide("c1.xhtml", "TEXT")))
    assert "teaser excerpt" not in upper
    other = li.epub_to_chapter_script(_book({}, guide=_guide("c1.xhtml", "cover")))
    assert "teaser excerpt" in other  # nothing declares a start, nothing is dropped


def test_landmarks_bodymatter_declares_the_start():
    nav = _plain_nav([("c1.xhtml", "One"), ("c2.xhtml", "Two")], _landmarks("c1.xhtml"))
    # TOC alone would also work here; point landmarks at c2 to prove it is the source.
    nav2 = _plain_nav([("c1.xhtml", "One"), ("c2.xhtml", "Two")], _landmarks("c2.xhtml"))
    assert "teaser excerpt" not in li.epub_to_chapter_script(_book({}, nav=nav))
    script = li.epub_to_chapter_script(_book({}, nav=nav2))
    assert "teaser excerpt" not in script
    assert _heads(script) == ["# One", "# Two"]  # headed chapters before the start survive


def test_without_a_declared_start_the_toc_decides():
    script = li.epub_to_chapter_script(_book({}, nav=_TWO_CHAPTER_NAV))
    assert "teaser excerpt" not in script
    assert _heads(script) == ["# One", "# Two"]


def test_long_unlisted_prologue_is_kept_when_no_start_is_declared():
    docs = {"prologue.xhtml": _doc(f"<p>{_WORDS}</p>")}
    epub = _epub({**docs, "c1.xhtml": _doc("<h1>One</h1><p>Text.</p>")},
                 nav=_plain_nav([("c1.xhtml", "One")]))
    assert _WORDS in li.epub_to_chapter_script(epub)




def test_unlisted_pages_after_the_start_and_books_without_navigation_are_untouched():
    nav = _plain_nav([("c1.xhtml", "One")])
    epub = _epub({"c1.xhtml": _doc("<h1>One</h1><p>Text.</p>"),
                  "after.xhtml": _doc("<p>Continuation of one.</p>")}, nav=nav)
    assert "Continuation of one." in li.epub_to_chapter_script(epub)
    bare = _epub({"a.xhtml": _doc("<p>Alpha page.</p>"), "b.xhtml": _doc("<h1>B</h1><p>Beta.</p>")})
    script = li.epub_to_chapter_script(bare)
    assert "Alpha page." in script and "Beta." in script


def _back_matter_book(middle: dict[str, str], listed_back: bool = True) -> bytes:
    entries = [("c1.xhtml", "One"), ("c2.xhtml", "Two")]
    if listed_back:
        entries.append(("praise.xhtml", "Praise for Another Book"))
    docs = {
        "c1.xhtml": _doc("<h1>One</h1><p>First chapter text.</p>"),
        "c2.xhtml": _doc("<h1>Two</h1><p>Second chapter text.</p>"),
        "praise.xhtml": _doc("<p>Rave reviews.</p>"),
        **middle,
    }
    return _epub(docs, nav=_plain_nav(entries))








def test_unlisted_ancillary_page_inside_back_matter_keeps_it_open():
    epub = _back_matter_book(
        {"colophon.xhtml": _doc("<h1>Footnotes</h1><p>Note text.</p>"),
         "ad.xhtml": _doc("<p>Buy more books.</p>")},
    )
    script = li.epub_to_chapter_script(epub)
    assert "Note text." not in script and "Buy more books." not in script


def test_substantive_unlisted_prologue_survives_a_late_declared_start():
    headed = _epub(
        {
            "pro.xhtml": _doc("<h1>Prologue</h1><p>Short but headed.</p>"),
            "long.xhtml": _doc(f"<p>{_WORDS}</p>"),
            "c1.xhtml": _doc("<h1>One</h1><p>Text.</p>"),
        },
        nav=_plain_nav([("c1.xhtml", "One")]),
        guide=_guide("c1.xhtml"),
    )
    script = li.epub_to_chapter_script(headed)
    assert "Short but headed." in script and _WORDS in script


def test_publisher_style_headings_are_ancillary_when_short_and_unlisted():
    for heading in ("Novels by Some Author", "Published by Some House"):
        epub = _epub(
            {
                "pub.xhtml": _doc(f"<h1>{heading}</h1><p>Catalogue.</p>"),
                "c1.xhtml": _doc("<h1>One</h1><p>Text.</p>"),
            },
            nav=_plain_nav([("c1.xhtml", "One")]),
        )
        assert "Catalogue." not in li.epub_to_chapter_script(epub)


def test_ambiguous_short_heading_alone_does_not_delete_a_chapter():
    epub = _epub(
        {
            "short.xhtml": _doc("<h1>Prologue</h1><p>Brief opening.</p>"),
            "c1.xhtml": _doc("<h1>One</h1><p>Text.</p>"),
        },
        nav=_plain_nav([("c1.xhtml", "One")]),
    )
    assert "Brief opening." in li.epub_to_chapter_script(epub)


def test_publisher_like_headings_are_kept_when_listed_or_substantive():
    listed = _epub(
        {"pub.xhtml": _doc("<h1>Published by Me</h1><p>My own story.</p>"),
         "c1.xhtml": _doc("<h1>One</h1><p>Text.</p>")},
        nav=_plain_nav([("pub.xhtml", "Published by Me"), ("c1.xhtml", "One")]),
    )
    assert "My own story." in li.epub_to_chapter_script(listed)
    long = _epub(
        {"pub.xhtml": _doc(f"<h1>Novels by Me</h1><p>{_WORDS}</p>"),
         "c1.xhtml": _doc("<h1>One</h1><p>Text.</p>")},
        nav=_plain_nav([("c1.xhtml", "One")]),
    )
    assert _WORDS in li.epub_to_chapter_script(long)


@pytest.mark.parametrize("title", ["Other Works by Jane", "Books by Jane", "About the Publisher",
                                   "Footnotes", "Endnote", "Titles by Jane"])
def test_new_ancillary_titles_are_recognised(title):
    assert li._is_ancillary(set(), title)
    assert not li._is_ancillary(set(), "Booksellers")


def test_toc_titles_reports_the_landmarks_start():
    epub = _epub({"c1.xhtml": _doc("<h1>One</h1><p>Text.</p>")},
                 nav=_plain_nav([("c1.xhtml", "One")], _landmarks("c1.xhtml")))
    with zipfile.ZipFile(io.BytesIO(epub)) as zf:
        budget = li._ReadBudget(10**6, 10**7)
        titles, start = li._toc_titles(zf, "OPS", ["TOC.xhtml"], set(zf.namelist()), budget)
    assert titles == {"OPS/c1.xhtml": "One"} and start == "OPS/c1.xhtml"
