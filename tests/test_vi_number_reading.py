"""Vietnamese number pass (#4319): dot thousands grouping + nghìn -> ngàn."""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services.text_normalization import normalize_text


@pytest.mark.parametrize("src,out", [
    ("3500 oát", "3.500 oát"),
    ("7500 oát", "7.500 oát"),
    ("12000000 đồng", "12.000.000 đồng"),
    ("giá 3500.", "giá 3.500."),
    ("ba nghìn năm trăm oát", "ba ngàn năm trăm oát"),
    ("Nghìn người, NGHÌN", "Ngàn người, NGÀN"),
    ("3500%", "3.500%"),
])
def test_rewrites(src, out):
    assert normalize_text(src, "Vietnamese") == out
    assert normalize_text(out, "Vietnamese") == out  # idempotent


@pytest.mark.parametrize("src", [
    "năm 2026", "Năm 2025", "473%", "78,9 triệu đồng", "170,1%", "3.500 oát",
    "1.860.000 xe", "0123456", "gọi 1234567890123", "lúc 10:3500", "v1.23500",
    "mã A3500", "3500A", "3,500", "có 999 xe", "nghìnx",
])
def test_unchanged(src):
    assert normalize_text(src, "Vietnamese") == src


def test_other_languages_untouched():
    assert "nghìn" in normalize_text("3500 nghìn", "Indonesian")
    assert normalize_text("3500", "Korean") == "3500"


def test_nfd_input():
    import unicodedata
    src = unicodedata.normalize("NFD", "ba nghìn năm trăm 3500")
    assert normalize_text(src, "Vietnamese") == "ba ngàn năm trăm 3.500"
