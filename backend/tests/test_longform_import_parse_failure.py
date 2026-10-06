"""A mid-chapter HTML parse failure must not silently drop the chapter.

`_html_to_title_body` swallows parser exceptions. If it returned empty text
on failure, `epub_to_chapter_script`'s `if not body.strip(): continue` would
silently omit that chapter from the audiobook — the user only finds out when
the narration skips from chapter 1 to chapter 3. The contract instead: keep
the partial text extracted before the failure, and log a warning (#1161).
"""

import io
import zipfile

import pytest

from services import longform_import as li


@pytest.mark.parametrize("tag", ["p", "div", "li", "tr", "section", "blockquote", "dd", "h4"])
def test_block_edges_separate_adjacent_prose(tag):
    _, body = li._html_to_title_body(f"<body>Before<{tag}>Inside</{tag}>After</body>")
    assert body.splitlines() == ["Before", "Inside", "After"]


def test_inline_markup_keeps_word_fragments_joined():
    _, body = li._html_to_title_body("<body><p>un<em>break</em>able</p></body>")
    assert body == "unbreakable"


def test_heading_and_pagebreak_do_not_become_body_text():
    title, body = li._html_to_title_body('<body><h1>Chapter <em>One</em></h1><p>First<span role="doc-pagebreak">20</span> word.</p>After.</body>')
    assert title == "Chapter One"
    assert body.splitlines() == ["First word.", "After."]


def _make_epub(n_chapters: int = 3) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        manifest, spine = [], []
        for i in range(1, n_chapters + 1):
            z.writestr(
                f"OEBPS/ch{i}.xhtml",
                f"<html><head><title>Chapter {i}</title></head><body>"
                f"<h1>Chapter {i}</h1>"
                f"<p>Opening paragraph of chapter {i}.</p>"
                f"<p>Closing paragraph of chapter {i}.</p></body></html>",
            )
            manifest.append(f'<item id="c{i}" href="ch{i}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test Book</dc:title></metadata>'
            f"<manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>",
        )
    return buf.getvalue()


def test_clean_epub_yields_all_chapters():
    script = li.epub_to_chapter_script(_make_epub())
    assert script.count("# Chapter") == 3
    assert "Opening paragraph of chapter 1.\n\nClosing paragraph of chapter 1." in script


def test_mid_chapter_parse_failure_keeps_partial_text(monkeypatch, caplog):
    """Parser dies halfway through chapter 2 → chapter 2 stays, with the text
    extracted up to the failure point; the failure is logged, not silent."""
    real_feed = li._TextExtractor.feed

    def poisoned_feed(self, data):
        if "chapter 2" in data:
            half = data.index("Closing")
            real_feed(self, data[:half])
            raise ValueError("simulated parser blow-up mid-chapter")
        return real_feed(self, data)

    monkeypatch.setattr(li._TextExtractor, "feed", poisoned_feed)

    with caplog.at_level("WARNING"):
        script = li.epub_to_chapter_script(_make_epub())

    # All three chapters still present — nothing silently dropped.
    assert script.count("# Chapter") == 3
    assert "# Chapter 2" in script
    # The pre-failure text survived.
    assert "Opening paragraph of chapter 2." in script
    # And the failure left a trace for diagnosis.
    assert any("HTML parsing failed" in r.message for r in caplog.records)


def test_chapter_failing_before_any_text_is_skipped_not_fatal(monkeypatch):
    """If the parser dies before extracting anything, that chapter is empty and
    skipped (pre-existing behavior) — but the rest of the book still imports."""

    def dead_feed(self, data):
        if "chapter 2" in data:
            raise ValueError("boom before any text")
        return li.HTMLParser.feed(self, data)

    monkeypatch.setattr(li._TextExtractor, "feed", dead_feed)

    script = li.epub_to_chapter_script(_make_epub())
    assert "# Chapter 1" in script and "# Chapter 3" in script
    assert "chapter 2" not in script.lower()


@pytest.mark.parametrize("line_break", ["<br>", "<br/>", "<br />", "<BR/>"])
def test_void_line_break_is_not_a_paragraph_break(line_break):
    from services import longform_import

    _, body = longform_import._html_to_title_body(f"<body><p>Line one.{line_break}Line two.</p>After.</body>")

    assert body == "Line one.\nLine two.\nAfter."


@pytest.mark.parametrize("line_break", ["<br>", "<br/>"])
def test_epub_preserves_line_break_inside_paragraph(line_break):
    from services import longform_import

    # Repack the actual EPUB fixture with one multiline opening paragraph.
    packed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_make_epub(1))) as source, zipfile.ZipFile(packed, "w") as dest:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "OEBPS/ch1.xhtml":
                content = content.replace(b"Opening paragraph of chapter 1.",
                                          f"Line one.{line_break}Line two.".encode())
            dest.writestr(info, content)

    script = longform_import.epub_to_chapter_script(packed.getvalue())

    assert "Line one.\nLine two.\n\nClosing paragraph of chapter 1." in script
