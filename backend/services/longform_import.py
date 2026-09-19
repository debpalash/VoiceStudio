"""Import plain text / EPUB into the chapter-delimited script the audiobook
parser understands.

Both helpers are pure (bytes/str in, script-str out) so they're unit-tested
without a server. EPUB parsing is **stdlib only** (zipfile + ElementTree +
html.parser, plus the shared ``services.text_upload`` decoder) — no new
dependency, no network, consistent with the local-first guarantee. The output
is the same ``# Heading`` + body grammar
:func:`services.audiobook.parse_audiobook_script` already consumes, so import is
just a front door onto the existing pipeline.
"""

from __future__ import annotations

import io
import logging
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from services.text_upload import bom_encoding, decode_text_upload

# A line that *starts* with a chapter keyword and is short enough to be a title
# (not a sentence that happens to begin with "Chapter"). Anchored, no ambiguous
# quantifiers → ReDoS-safe and applied per-line (short input) anyway.
_CH_RE = re.compile(r"^(?:chapter|part|book|prologue|epilogue|section)\b", re.IGNORECASE)
# Already-present Markdown H1 — if the text has any, we leave it untouched.
_H1_RE = re.compile(r"^[ \t]*#[ \t]+\S", re.MULTILINE)
_CHAPTER_TITLE_MAX = 60
# Zip-bomb / OOM guards for EPUB ingestion: per-entry and cumulative caps on
# *uncompressed* bytes read from the archive.
_EPUB_MAX_ENTRY_BYTES = 25 * 1024 * 1024
_EPUB_MAX_TOTAL_BYTES = 300 * 1024 * 1024
# An EPUB document declares its own encoding: XML in the declaration, the XHTML
# serialisation additionally in a `<meta charset>`. Both sit in the prologue, so
# only the head of the document is scanned — the patterns never run over a whole
# book, and neither has overlapping quantifiers (ReDoS-safe).
_XML_DECL_ENCODING_RE = re.compile(
    rb"""<\?xml[^>]{0,200}?encoding\s*=\s*["']([A-Za-z0-9_.:+-]{1,40})["']"""
)
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]{0,400}?charset\s*=\s*["']?\s*([A-Za-z0-9_.:+-]{1,40})""",
    re.IGNORECASE,
)
_DECLARATION_SCAN_BYTES = 1024
# Recognize markup or XML whitespace in BOM-less wide documents. Widest
# first: a UTF-32 LE prefix also starts with its UTF-16 LE counterpart.
_NO_BOM_WIDE_PREFIXES = tuple(
    (char.encode(encoding), encoding)
    for encoding in ("utf-32-le", "utf-32-be", "utf-16-le", "utf-16-be")
    for char in "< \t\r\n"
)

logger = logging.getLogger("omnivoice.longform_import")


def _declared_encoding(raw: bytes) -> str | None:
    """The encoding an EPUB document names for itself, as written."""
    head = raw[:_DECLARATION_SCAN_BYTES]
    for pattern in (_XML_DECL_ENCODING_RE, _META_CHARSET_RE):
        match = pattern.search(head)
        if match:
            return match.group(1).decode("ascii", "ignore")
    return None


def _decode_epub_entry(raw: bytes) -> str:
    """Decode one EPUB document by the encoding it actually carries.

    UTF-8 is only the *default* for an XML document — a BOM or an
    ``encoding=``/``charset=`` declaration overrides it, and EPUB 2 books
    (and Calibre conversions of older HTML) routinely declare ISO-8859-1 or a
    CJK code page. Decoding those as UTF-8 with ``errors="ignore"`` silently
    *deleted* every byte their accents, dashes and curly quotes are spelled
    with, so "Le café était fermé" imported — and was narrated — as "Le caf
    tait ferm". ``decode_text_upload`` is the same BOM → UTF-8 →
    Windows-1252 ladder the ``.txt``/``.md`` import branch already uses.
    """
    # A byte-order mark outranks any declaration (XML 1.0 §F), and
    # decode_text_upload owns the one BOM table both front doors read.
    if not bom_encoding(raw):
        for prefix, wide in _NO_BOM_WIDE_PREFIXES:
            if raw.startswith(prefix):
                return raw.decode(wide, errors="replace")
        declared = _declared_encoding(raw)
        if declared:
            try:
                # errors="replace": a mis-declared document still imports, the
                # way an undeclared one does. Nothing here may fail a book.
                return raw.decode(declared, errors="replace")
            except (LookupError, UnicodeError):
                # An encoding Python doesn't have, or a bytes-to-bytes codec
                # such as "hex_codec" — those resolve but refuse to produce
                # text. Guess the way an undeclared document is guessed.
                logger.warning(
                    "EPUB entry declares an encoding that cannot decode text; guessing instead"
                )
    return decode_text_upload(raw)


def chapterize_plaintext(text: str) -> str:
    """Insert ``# `` headings ahead of obvious chapter-title lines.

    No-op if the text already has Markdown H1 headings (the user has structured
    it). Otherwise short standalone lines beginning with a chapter keyword
    (``Chapter 3``, ``Prologue`` …) become headings; everything else is left
    verbatim. Text with no detectable breaks falls through as a single chapter.
    """
    text = text or ""
    if _H1_RE.search(text):
        return text
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s and len(s) <= _CHAPTER_TITLE_MAX and _CH_RE.match(s):
            out.append(f"# {s}")
        else:
            out.append(line)
    return "\n".join(out)


class _TextExtractor(HTMLParser):
    """Collect visible text from XHTML, dropping script/style and collapsing
    whitespace. First <h1>/<h2>/<title> seen is kept as the chapter title."""

    _SKIP = {"script", "style", "head"}
    _BREAK = {"p", "br", "div", "h1", "h2", "h3", "li", "tr"}
    #: A print page number carried into the EPUB (EPUB 3 ``epub:type="pagebreak"``,
    #: ARIA ``role="doc-pagebreak"``, or a publisher class such as
    #: ``pagebreak-rw``). Inline, it glues onto prose ("happily as 2Zoe threw");
    #: block-level, it becomes a lone "120" / "iv" paragraph. Never narrated.
    _PAGEBREAK_CLASS = re.compile(r"page-?(?:break|num(?:ber)?)(?:-rw)?", re.I)
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _EPUB_NS = "http://www.idpf.org/2007/ops"

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._pagebreak_stack: list[str] = []
        self._in_title = False
        self.title = ""
        self._elements: list[tuple[str, dict[str, str]]] = []
        #: ``epub:type`` tokens seen on the document's structural elements
        #: (``body``/``main``/``section``/``article``/``div``): "frontmatter chapter" →
        #: {"frontmatter", "chapter"}. Lets the caller drop title pages,
        #: dedications, copyright pages and other non-narrated matter.
        self.epub_types: set[str] = set()

    @classmethod
    def _is_pagebreak(cls, attrs, types: set[str]) -> bool:
        if "pagebreak" in types:
            return True
        for name, value in attrs:
            if not value:
                continue
            if name == "role" and "doc-pagebreak" in value.split():
                return True
            if name == "class" and any(cls._PAGEBREAK_CLASS.fullmatch(token) for token in value.split()):
                return True
        return False

    def handle_starttag(self, tag, attrs):
        # Resolve semantic attributes by namespace URI, preserving XML scope.
        namespaces = dict(self._elements[-1][1] if self._elements else {"epub": self._EPUB_NS})
        for name, value in attrs:
            if name.startswith("xmlns:"):
                namespaces[name[6:]] = value or ""
        types = set()
        for name, value in attrs:
            prefix, separator, local = name.partition(":")
            if separator and local == "type" and namespaces.get(prefix) == self._EPUB_NS:
                types.update((value or "").split())
        if tag not in self._VOID:
            self._elements.append((tag, namespaces))
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag in ("body", "main", "section", "article", "div"):
            self.epub_types.update(types)
        if self._pagebreak_stack:
            if tag not in self._VOID:
                self._pagebreak_stack.append(tag)
            return
        if self._is_pagebreak(attrs, types):
            if tag not in self._VOID:
                self._pagebreak_stack.append(tag)
            return
        if tag in ("h1", "h2", "title") and not self.title:
            self._in_title = True
        if tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        for index in range(len(self._elements) - 1, -1, -1):
            if self._elements[index][0] == tag:
                del self._elements[index:]
                break
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if self._pagebreak_stack:
            for index in range(len(self._pagebreak_stack) - 1, -1, -1):
                if self._pagebreak_stack[index] == tag:
                    del self._pagebreak_stack[index:]
                    break
            return
        if tag in ("h1", "h2", "title"):
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth or self._pagebreak_stack:
            return
        if self._in_title:
            # The first heading becomes the chapter's `# Title` (metadata, not
            # narrated) — capture it but keep it out of the body. Later headings
            # (title already set) fall through and are narrated as subheadings.
            if not self.title:
                self.title = data.strip()
            return
        self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # Collapse runs of blank lines / trailing spaces into tidy paragraphs.
        lines = [ln.strip() for ln in raw.split("\n")]
        out: list[str] = []
        for ln in lines:
            if ln or (out and out[-1]):
                out.append(ln)
        return "\n".join(out).strip()


def _html_to_title_body(xhtml: str) -> tuple[str, str]:
    """(title, body) — see :func:`_html_extract` for the epub:type tokens too."""
    _, title, body = _html_extract(xhtml)
    return title, body


def _html_extract(xhtml: str) -> tuple[set[str], str, str]:
    p = _TextExtractor()
    try:
        p.feed(xhtml)
    except Exception:
        # Keep whatever the extractor collected before the failure: an empty
        # return would make the caller's `if not body.strip(): continue` drop
        # the whole chapter from the audiobook silently — a partial chapter
        # plus this log line is strictly more recoverable than a missing one.
        logger.warning("HTML parsing failed for EPUB entry; using partial text", exc_info=True)
    return p.epub_types, p.title, p.text()


_OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "c": "urn:oasis:names:tc:opendocument:xmlns:container"}


def _opf_path(zf: zipfile.ZipFile, budget: _ReadBudget) -> str:
    container = _read_member(zf, "META-INF/container.xml", budget, required=True)
    # The EPUB is a local file the user chose to import (not a remote/untrusted
    # surface); stdlib ElementTree doesn't expand external entities by default.
    try:
        root = ET.fromstring(container)  # nosec B314
    except ET.ParseError as e:
        raise ValueError(f"EPUB container.xml is not well-formed XML: {e}") from e
    rootfile = root.find(".//c:rootfiles/c:rootfile", _OPF_NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("EPUB container.xml has no rootfile")
    return rootfile.get("full-path")


#: EPUB 3 structural semantics (``epub:type``) that mark matter a narrator
#: would not read: covers, title/copyright pages, dedications, contents,
#: acknowledgements, landmarks. ``bodymatter``/``chapter``/``part`` override
#: (a chapter tagged "bodymatter chapter" is narrated even inside a "part").
_ANCILLARY_TYPES = frozenset({
    "frontmatter", "backmatter", "cover", "titlepage", "halftitlepage",
    "copyright-page", "toc", "landmarks", "dedication", "acknowledgments",
    "imprint", "colophon", "contributors", "other-credits", "epigraph",
    "loi", "lot", "index", "glossary", "bibliography", "appendix",
})
_BODY_TYPES = frozenset({"bodymatter", "chapter", "part", "prologue", "epilogue", "introduction", "preface", "foreword", "volume"})
#: Fallback for EPUBs without ``epub:type``: the section's TOC label / title.
_ANCILLARY_TITLE = re.compile(
    r"^\s*(cover|half[ -]?title|title[ -]?page|copyright|dedication|contents|"
    r"table of contents|acknowledg\w*|about the (author|illustrator|book)|"
    r"also (by|available)|praise for|imprint|colophon|newsletter|look out for|"
    r"(other )?(works|books|titles) by|about the publisher|footnotes?|endnotes?)\b",
    re.I,
)


def _is_ancillary(types: set[str], title: str) -> bool:
    if types & _BODY_TYPES:
        return False
    if types & _ANCILLARY_TYPES:
        return True
    return bool(title and _ANCILLARY_TITLE.match(title))


def _toc_titles(
    zf: zipfile.ZipFile, base: str, nav_hrefs: list[str], names: set[str], budget: _ReadBudget
) -> tuple[dict[str, str], str | None]:
    """Map each spine document (full zip path) to its table-of-contents label.

    Also returns the EPUB 3 landmarks ``bodymatter`` target (the publisher's
    "the book starts here"), or ``None`` — see :func:`_front_matter_end`.

    Publishers label sections better than their headings do ("Chapter One:
    A New Arrival" versus an ``<h1>`` holding only "A New Arrival"). Reads
    the EPUB 3 nav document (``<a href>`` entries) and the EPUB 2 NCX
    (``navPoint/content@src``); the first label for a document wins.

    Navigation documents come from the user's file like every other member,
    so they are read through the same zip-bomb ``budget`` as the spine.
    """
    body_start: str | None = None
    nav_titles: dict[str, str] = {}  # EPUB 3 nav — authoritative
    ncx_titles: dict[str, str] = {}  # EPUB 2 NCX — fallback
    for href in nav_hrefs:
        full = posixpath.normpath(posixpath.join(base, href)) if base else href
        if full not in names:
            continue
        raw = _read_member(zf, full, budget)
        if raw is None:
            continue
        try:
            root = ET.fromstring(_decode_epub_entry(raw))  # nosec B314 — bounded local EPUB XML
        except ET.ParseError:
            logger.warning("Malformed EPUB navigation; using chapter headings", exc_info=True)
            continue
        nav_dir = posixpath.dirname(full)
        pairs = []
        def local_name(element):
            return element.tag.rsplit("}", 1)[-1]
        if local_name(root) == "ncx":
            target = ncx_titles
            for point in root.iter():
                if local_name(point) != "navPoint":
                    continue
                label = next((child for child in point if local_name(child) == "navLabel"), None)
                content = next((child for child in point if local_name(child) == "content"), None)
                if label is not None and content is not None and content.get("src"):
                    pairs.append((content.get("src"), "".join(label.itertext())))
        else:
            target = nav_titles
            navs = [element for element in root.iter() if local_name(element) == "nav"]
            type_attribute = "{http://www.idpf.org/2007/ops}type"
            toc = [nav for nav in navs if "toc" in nav.get(type_attribute, "").split()]
            # Legacy untyped navs are supported, but landmarks/page lists never name chapters.
            scopes = toc or [nav for nav in navs if not nav.get(type_attribute)]
            for scope in scopes:
                for anchor in scope.iter():
                    if local_name(anchor) == "a" and anchor.get("href"):
                        pairs.append((anchor.get("href"), "".join(anchor.itertext())))
            for nav in navs:
                if "landmarks" not in nav.get(type_attribute, "").split():
                    continue
                for anchor in nav.iter():
                    if (body_start is None and local_name(anchor) == "a" and anchor.get("href")
                            and "bodymatter" in anchor.get(type_attribute, "").split()):
                        target_path = anchor.get("href").split("#", 1)[0]
                        body_start = posixpath.normpath(posixpath.join(nav_dir, target_path))
        for src, label in pairs:
            path = src.split("#", 1)[0]
            label = " ".join(label.split())
            if path and label:
                target.setdefault(posixpath.normpath(posixpath.join(nav_dir, path)), label)
    titles = {**ncx_titles, **nav_titles}
    return {k: v for k, v in titles.items() if v}, body_start


#: Ceiling for dropping an unlisted page on the TOC alone (no declared start):
#: a teaser, epigraph or blurb is a few dozen words; an unlisted prologue is not.
_FRONT_MATTER_MAX_WORDS = 400


def _front_matter_end(
    sections: list[tuple[str, set[str], str, str]], toc: dict[str, str], declared: str | None
) -> tuple[int, bool]:
    """Index of the first spine section that belongs to the book, and whether
    the publisher DECLARED it.

    Untagged EPUBs put unmarked pages — a teaser excerpt, an epigraph, a blurb —
    ahead of chapter one: no ``epub:type``, no heading, not in the contents. The
    only things that say "this is not a chapter" are structural: the package's
    declared reading start (EPUB 2 ``guide`` ``text`` reference / EPUB 3
    landmarks ``bodymatter``) and, failing that, the first section the table of
    contents lists that is not itself front matter.
    """
    paths = [full for full, *_ in sections]
    if declared in paths:
        return paths.index(declared), True
    for i, (full, types, _title, _body) in enumerate(sections):
        if full in toc and not _is_ancillary(types, toc[full]):
            return i, False
    return 0, False


class _ReadBudget:
    """Zip-bomb guard shared by every EPUB member read.

    ``max_entry_bytes`` bounds one member's *uncompressed* size, ``max_total_bytes``
    the running total across all members read (``used``). Both are applied
    before decompression, from the central directory's ``file_size``.
    """

    def __init__(self, max_entry_bytes: int, max_total_bytes: int) -> None:
        self.max_entry_bytes = max_entry_bytes
        self.max_total_bytes = max_total_bytes
        self.used = 0

    def allows(self, info: zipfile.ZipInfo) -> bool:
        return info.file_size <= self.max_entry_bytes and self.used + info.file_size <= self.max_total_bytes


def _read_member(zf: zipfile.ZipFile, name: str, budget: _ReadBudget, *, required: bool = False) -> bytes | None:
    """Read one EPUB member within ``budget``; ``None`` when missing or over the limits.

    ``required`` members (container.xml, the OPF) raise instead of returning
    ``None`` when they are missing or oversized — without them there is no
    book to import, and a crafted upload must not be able to make the route
    decompress an unbounded member before the limits apply.

    A truncated, CRC-broken or encrypted member surfaces from ``zipfile`` as
    ``BadZipFile`` / ``RuntimeError`` (and ``NotImplementedError`` for an
    unsupported compression) — all "this EPUB is unreadable", so they become
    the ``ValueError`` the import route already maps to a 400 with the reason.
    """
    try:
        info = zf.getinfo(name)
    except KeyError:
        if required:
            raise ValueError(f"not a valid EPUB: {name!r} is missing")
        return None
    if not budget.allows(info):
        if required:
            raise ValueError(f"EPUB member {name!r} exceeds the import size limit")
        return None
    try:
        raw = zf.read(name)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as e:
        raise ValueError(f"EPUB member {name!r} is unreadable: {e}") from e
    budget.used += len(raw)
    return raw


def epub_to_chapter_script(
    data: bytes,
    *,
    max_entry_bytes: int = _EPUB_MAX_ENTRY_BYTES,
    max_total_bytes: int = _EPUB_MAX_TOTAL_BYTES,
) -> str:
    """Convert EPUB bytes into a ``# Chapter`` / body script in spine order.

    Reads the OPF manifest + spine (the publisher's reading order), extracts
    each document's title + visible text, and emits one ``# Title`` block per
    document with renderable text. ``max_entry_bytes`` / ``max_total_bytes``
    bound the *uncompressed* bytes read (zip-bomb guard). Raises ``ValueError``
    on a malformed EPUB.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"not a valid EPUB (zip) file: {e}") from e

    budget = _ReadBudget(max_entry_bytes, max_total_bytes)
    opf_path = _opf_path(zf, budget)
    opf_raw = _read_member(zf, opf_path, budget, required=True)
    try:
        opf = ET.fromstring(opf_raw)  # nosec B314 — local user EPUB; see _opf_path
    except ET.ParseError as e:
        raise ValueError(f"EPUB package document is not well-formed XML: {e}") from e
    base = posixpath.dirname(opf_path)

    manifest: dict[str, str] = {}
    nav_hrefs: list[str] = []
    for item in opf.findall(".//opf:manifest/opf:item", _OPF_NS):
        iid, href = item.get("id"), item.get("href")
        if iid and href:
            manifest[iid] = href
            props = (item.get("properties") or "").split()
            if "nav" in props or item.get("media-type") == "application/x-dtbncx+xml":
                nav_hrefs.append(href)

    names = set(zf.namelist())
    toc, declared_start = _toc_titles(zf, base, nav_hrefs, names, budget)
    for ref in opf.findall(".//opf:guide/opf:reference", _OPF_NS):  # EPUB 2 equivalent
        if declared_start is None and (ref.get("type") or "").lower() == "text" and ref.get("href"):
            target = ref.get("href").split("#", 1)[0]
            declared_start = posixpath.normpath(posixpath.join(base, target)) if base else target

    sections: list[tuple[str, set[str], str, str]] = []  # (path, epub:types, heading, body)
    for ref in opf.findall(".//opf:spine/opf:itemref", _OPF_NS):
        href = manifest.get(ref.get("idref") or "")
        if not href or href in nav_hrefs:
            continue  # the table of contents itself is never narrated
        if (ref.get("linear") or "yes").lower() == "no":
            continue  # publisher marked it as outside the reading order
        full = posixpath.normpath(posixpath.join(base, href)) if base else href
        if full not in names:
            continue
        # Bound decompression through the shared budget (an oversized entry is
        # skipped; once the cumulative total is spent nothing more is read).
        if budget.used >= budget.max_total_bytes:
            break
        raw = _read_member(zf, full, budget)
        if raw is None:
            continue
        types, title, body = _html_extract(_decode_epub_entry(raw))
        if not body.strip():
            continue  # nav docs, empty pages
        sections.append((full, types, title, body))

    start, declared = _front_matter_end(sections, toc, declared_start)
    # A declared start is only trusted alongside a TOC: the TOC is what keeps a
    # listed prologue that sits before a (commonly too-late) "start reading" mark.
    trusted = declared and bool(toc)
    blocks: list[str] = []
    in_back_matter = False
    for index, (full, types, heading, body) in enumerate(sections):
        title = toc.get(full) or heading
        if _is_ancillary(types, title):
            # Only a section the contents LISTS opens back matter: an unlisted page
            # that merely looks ancillary must not cost the next chapter's
            # continuation file.
            # Once open it stays open through further ancillary pages, listed or not.
            in_back_matter = in_back_matter or (index > start and full in toc)
            continue  # cover, title page, dedication, copyright, contents, …
        unlisted = full not in toc and not (types & _BODY_TYPES)
        stray = not heading and len(body.split()) <= _FRONT_MATTER_MAX_WORDS
        if index < start and unlisted and (trusted or stray):
            continue  # unlisted page ahead of the book: a teaser/epigraph/blurb
        if in_back_matter and unlisted and stray:
            # Footnotes, a stray ad — but ONLY once listed back matter has begun:
            # an unlisted file straight after a chapter is that chapter's
            # continuation (split chapters list only their first file).
            continue
        in_back_matter = False
        title = title or f"Chapter {len(blocks) + 1}"
        blocks.append(f"# {title}\n\n{body}")

    if not blocks:
        raise ValueError("no readable chapters found in the EPUB")
    return "\n\n".join(blocks)


# Page-count ceiling for PDF ingestion — a defence against a pathological
# document tying up the worker. 5000 pages comfortably covers any real book.
_PDF_MAX_PAGES = 5000


def pdf_to_chapter_script(data: bytes, *, max_pages: int = _PDF_MAX_PAGES) -> str:
    """Convert PDF bytes into a ``# Chapter`` / body script.

    Extracts the embedded text layer page-by-page (in page order), joins it,
    and runs it through :func:`chapterize_plaintext` so ``Chapter N`` /
    ``Prologue`` lines become headings — same grammar EPUB and plaintext emit.
    Unlike EPUB this needs a real parser (``pypdf``, pure-Python, no native
    deps → identical on every platform).

    Limitations surfaced as ``ValueError`` (the route maps these to a 400 with
    the message, so the user gets actionable feedback rather than a silent
    empty import):

    * **Scanned / image-only PDFs** have no text layer — there's nothing to
      extract without OCR, so we raise rather than return an empty script.
    * **Password-protected PDFs** that don't open with an empty password can't
      be read.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, OSError, ValueError) as e:
        raise ValueError(f"not a valid PDF file: {e}") from e

    if reader.is_encrypted:
        # Many PDFs are encrypted with an empty user password (owner-locked but
        # freely readable). Try that; a real password we can't supply.
        try:
            if reader.decrypt("") == 0:  # 0 == wrong password
                raise ValueError("PDF is password-protected")
        except (NotImplementedError, PdfReadError) as e:
            raise ValueError(f"can't read this encrypted PDF: {e}") from e

    pages = reader.pages
    if len(pages) > max_pages:
        raise ValueError(f"PDF has too many pages (max {max_pages})")

    parts: list[str] = []
    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — one bad page shouldn't kill the import
            continue
        if text.strip():
            parts.append(text)

    if not parts:
        raise ValueError(
            "no extractable text — this looks like a scanned or image-only PDF")
    return chapterize_plaintext("\n\n".join(parts))
