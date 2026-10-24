import io
import struct
import zipfile

import pytest



def corrupt_bundle(compression):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as bundle:
        bundle.writestr("manifest.json", b'{"name":"voice"}')
    payload = bytearray(buffer.getvalue())
    name_size, extra_size = struct.unpack_from("<HH", payload, 26)
    offset = 30 + name_size + extra_size
    if compression == zipfile.ZIP_LZMA:
        payload[offset + 4:offset + 9] = b"\xff" * 5
    else:
        payload[offset] = 7
    return bytes(payload)


@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_corrupt_compression_is_a_bundle_error(compression):
    from core.safe_archive import ArchiveError, open_bounded_zip, read_member

    with open_bounded_zip(corrupt_bundle(compression)) as bundle:
        with pytest.raises(ArchiveError) as error:
            read_member(bundle, "manifest.json")
    assert error.value.status == 400
    assert error.value.detail == "bundle entry is unreadable"


@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_corrupt_copy_removes_partial_destination(tmp_path, compression):
    from core.safe_archive import ArchiveError, open_bounded_zip, copy_member

    destination = tmp_path / "ref.wav"
    with open_bounded_zip(corrupt_bundle(compression)) as bundle:
        with pytest.raises(ArchiveError) as error:
            copy_member(bundle, "manifest.json", str(destination))
    assert error.value.status == 400
    assert not destination.exists()


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_normal_members_and_byte_caps_remain_supported(compression):
    from core.safe_archive import ArchiveError, open_bounded_zip, read_member

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as bundle:
        bundle.writestr("manifest.json", b'{"name":"voice"}')
    with open_bounded_zip(buffer.getvalue()) as bundle:
        assert read_member(bundle, "manifest.json") == b'{"name":"voice"}'
        with pytest.raises(ArchiveError) as error:
            read_member(bundle, "manifest.json", max_bytes=2)
    assert error.value.status == 413


def test_destination_io_failure_is_not_an_invalid_bundle():
    from core.safe_archive import open_bounded_zip

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", b"{}")
    from core import safe_archive

    def failed_sink(chunk):
        raise OSError("destination full")

    with open_bounded_zip(buffer.getvalue()) as bundle:
        with pytest.raises(OSError, match="destination full"):
            safe_archive._stream(bundle, "manifest.json", 100, failed_sink)


@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
@pytest.mark.parametrize("member", ["META-INF/container.xml", "OEBPS/chapter.xhtml"])
def test_corrupt_epub_compression_is_an_import_error(compression, member):
    from services.longform_import import epub_to_chapter_script

    buffer = io.BytesIO()
    entries = {
        "META-INF/container.xml": '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        "OEBPS/content.opf": '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c" href="chapter.xhtml"/></manifest><spine><itemref idref="c"/></spine></package>',
        "OEBPS/chapter.xhtml": '<html><body><h1>Chapter</h1><p>Text</p></body></html>',
    }
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        offset = archive.getinfo(member).header_offset
    payload = bytearray(buffer.getvalue())
    name_size, extra_size = struct.unpack_from("<HH", payload, offset + 26)
    offset += 30 + name_size + extra_size
    if compression == zipfile.ZIP_LZMA:
        payload[offset + 4:offset + 9] = b"\xff" * 5
    else:
        payload[offset] = 7
    with pytest.raises(ValueError, match="EPUB member .* is unreadable"):
        epub_to_chapter_script(bytes(payload))
