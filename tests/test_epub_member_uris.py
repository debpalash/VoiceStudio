"""EPUB URI references must resolve the actual ZIP filenames without text loss."""
import io
import zipfile

import pytest


@pytest.mark.parametrize('filename,href', [
    ('chapter one.xhtml', 'chapter%20one.xhtml'),
    ('chapitre-é.xhtml', 'chapitre-%C3%A9.xhtml'),
    ('100%25.xhtml', '100%2525.xhtml'),
    ('chapter+one.xhtml', 'chapter+one.xhtml'),
])
def test_spine_and_navigation_resolve_uri_paths(filename, href):
    from services.longform_import import epub_to_chapter_script

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('META-INF/container.xml',
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr('OPS/book.opf',
            '<package xmlns="http://www.idpf.org/2007/opf">'
            '<manifest><item id="c" href="text/' + href + '"/>'
            '<item id="nav" href="nav.xhtml" properties="nav"/></manifest>'
            '<spine><itemref idref="c"/></spine></package>')
        archive.writestr('OPS/nav.xhtml',
            '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
            '<nav epub:type="toc"><a href="text/' + href + '#begin">Publisher title</a></nav>'
            '<nav epub:type="landmarks"><a epub:type="bodymatter" href="text/' + href + '#begin">Start</a></nav>'
            '</body></html>')
        archive.writestr('OPS/text/' + filename, '<html><body><p>All the chapter words survive.</p></body></html>')
    assert epub_to_chapter_script(buffer.getvalue()) == '# Publisher title\n\nAll the chapter words survive.'


def test_epub_ruby_annotations_are_not_read_as_duplicate_words():
    from services.longform_import import _html_to_title_body

    xhtml = (
        '<h1><ruby>東京<rp>(</rp><rt>とうきょう</rt><rp>)</rp></ruby>の旅</h1>'
        '<p>今日は<ruby>晴<rt>は</rt></ruby>れです。</p>'
    )
    assert _html_to_title_body(xhtml) == ("東京の旅", "今日は晴れです。")


def test_epub_unclosed_ruby_annotation_does_not_swallow_the_next_base():
    from services.longform_import import _html_to_title_body

    xhtml = '<p><ruby>東<rt>とう<rb>京</rb><rt>きょう</rt></ruby>と漢<ruby>字<rt>じ</ruby>です</p>'
    assert _html_to_title_body(xhtml) == ("", "東京と漢字です")
