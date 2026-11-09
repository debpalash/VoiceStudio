"""Boundary cases for safe EPUB extraction and archive reads."""

import io
import zipfile
from unittest.mock import patch

import pytest

from services import longform_import as li


def _archive(container: str, opf: str | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as book:
        book.writestr("META-INF/container.xml", container)
        if opf is not None:
            book.writestr("book.opf", opf)
    return stream.getvalue()


def test_page_markers_are_removed_without_joining_surrounding_words():
    markup = (
        '<body><p>Alpha<span class="pagenumber">12</span>Beta '
        '<span class="page-num-rw"/>Gamma.</p>'
        '<div role="doc-pagebreak">iv</div><p>In 2026, IV remained prose.</p>'
        '<p class="page-break-after">Layout text remains.</p></body>'
    )
    _title, body = li._html_to_title_body(markup)
    assert "Alpha Beta Gamma." in body
    assert "12" not in body and "\niv\n" not in f"\n{body}\n"
    assert "2026" in body and "IV remained" in body and "Layout text remains." in body


def test_semantic_prefix_resolution_is_scoped_and_structural_types_are_unioned():
    markup = (
        '<body xmlns:book="http://www.idpf.org/2007/ops">'
        '<section book:type="bodymatter chapter"><span book:type="pagebreak">7</span>A</section>'
        '<div xmlns:book="urn:not-epub"><span book:type="pagebreak">Keep</span></div>'
        '<p><span book:type="pagebreak"/>B</p></body>'
    )
    types, _title, body = li._html_extract(markup)
    assert types == {"bodymatter", "chapter"}
    assert "7" not in body and "Keep" in body and "B" in body


def test_parser_failure_returns_text_collected_before_the_error(monkeypatch, caplog):
    real_feed = li._TextExtractor.feed

    def fail_after_opening(self, markup):
        real_feed(self, "<body><p>Recovered words.</p>")
        raise RuntimeError("test parser interruption")

    monkeypatch.setattr(li._TextExtractor, "feed", fail_after_opening)
    with caplog.at_level("WARNING", logger="omnivoice.longform_import"):
        _title, body = li._html_to_title_body("<body>ignored</body>")
    assert body == "Recovered words."
    assert any("HTML parsing failed" in record.message for record in caplog.records)


def test_required_container_and_package_validation_errors_are_actionable():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w"):
        pass
    with pytest.raises(ValueError, match="container.xml.*missing"):
        li.epub_to_chapter_script(stream.getvalue())


def test_malformed_package_is_rejected_after_a_valid_container():
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="book.opf"/></rootfiles></container>'
    )
    with pytest.raises(ValueError, match="package.*well-formed"):
        li.epub_to_chapter_script(_archive(container, "<package>"))


def test_optional_budget_refusal_does_not_decompress_and_unreadable_members_are_wrapped():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as book:
        book.writestr("chapter.xhtml", b"12345")
    with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as book:
        budget = li._ReadBudget(4, 100)
        with patch.object(book, "read", wraps=book.read) as read:
            assert li._read_member(book, "chapter.xhtml", budget) is None
            read.assert_not_called()
        budget = li._ReadBudget(10, 100)
        with patch.object(book, "read", side_effect=RuntimeError("encrypted")):
            with pytest.raises(ValueError, match="chapter.xhtml.*unreadable"):
                li._read_member(book, "chapter.xhtml", budget)
