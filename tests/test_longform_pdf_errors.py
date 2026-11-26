"""PDF parser errors from lazy page-tree access."""

import pytest



def test_unreadable_pdf_page_tree_reports_import_error(monkeypatch):
    import importlib

    pdf_to_chapter_script = importlib.import_module("services.longform_import").pdf_to_chapter_script  # resolved at test time
    import pypdf

    class BrokenPages:
        def __len__(self):
            raise pypdf.errors.PdfReadError("missing page tree")

    class Reader:
        is_encrypted = False
        pages = BrokenPages()

    monkeypatch.setattr(pypdf, "PdfReader", lambda _stream: Reader())
    with pytest.raises(ValueError, match="not a valid PDF file: missing page tree"):
        pdf_to_chapter_script(b"PDF")
