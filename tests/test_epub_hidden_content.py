"""Only rendered EPUB content should reach the audiobook script."""
import io
import zipfile

import pytest



def _epub(body):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="chapter" href="chapter.xhtml"/></manifest><spine><itemref idref="chapter"/></spine></package>')
        archive.writestr("chapter.xhtml", "<html><body>" + body + "</body></html>")
    return output.getvalue()


@pytest.mark.parametrize("attribute", ["hidden", 'hidden="hidden"', 'hidden="false"', 'hidden="until-found"'])
def test_hidden_subtrees_do_not_supply_title_or_narration(attribute):
    from services.longform_import import epub_to_chapter_script
    script = epub_to_chapter_script(_epub(
        f'<div {attribute}><h1>Hidden heading</h1><p>Hidden <em>nested</em> words.</p></div>'
        '<h1>Visible chapter</h1><p>The visible words survive.</p>'
    ))
    assert script == "# Visible chapter\n\nThe visible words survive."


def test_hidden_inline_and_void_nodes_do_not_consume_following_text():
    from services.longform_import import epub_to_chapter_script
    script = epub_to_chapter_script(_epub(
        '<h1>Chapter One</h1><p>Hello <span hidden>secret <b>nested</b></span>world.'
        '<br hidden/> More words.</p><p>Next paragraph.</p>'
    ))
    assert script == "# Chapter One\n\nHello world. More words.\n\nNext paragraph."


def test_visible_attributes_and_pagebreak_suppression_are_unchanged():
    from services.longform_import import epub_to_chapter_script
    script = epub_to_chapter_script(_epub(
        '<h1>Chapter One</h1><p><span data-hidden="true">Visible</span> '
        '<span epub:type="pagebreak">12</span>words.</p>'
    ))
    assert script == "# Chapter One\n\nVisible words."


def test_hidden_structural_semantics_do_not_discard_visible_chapter():
    from services.longform_import import epub_to_chapter_script
    script = epub_to_chapter_script(_epub(
        '<div hidden epub:type="cover"><p>Hidden cover.</p></div>'
        '<h1>Visible chapter</h1><p>Visible chapter prose.</p>'
    ))
    assert script == "# Visible chapter\n\nVisible chapter prose."
