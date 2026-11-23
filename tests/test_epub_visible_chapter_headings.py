"""EPUB document metadata and visible narration are distinct."""

import pytest


@pytest.mark.parametrize(
    ("body", "title", "narration"),
    [
        ("<h1>Chapter One</h1><p>Opening line.</p>", "Chapter One", "Opening line."),
        ("<p>Book Name</p><p>Opening line.</p>", "Book Name", "Book Name\n\nOpening line."),
        ("<h2>Book Name</h2><p>Opening line.</p>", "Book Name", "Opening line."),
    ],
)
def test_epub_visible_heading_and_prose_survive_document_metadata(body, title, narration):
    from services.longform_import import _html_to_title_body

    document = f"<html><head><title>Book Name</title></head><body>{body}</body></html>"
    assert _html_to_title_body(document) == (title, narration)
