# Manuscript import

EPUB imports follow the publisher's reading order and keep chapter headings.
Print page-number markers and XHTML subtrees with the boolean `hidden`
attribute are excluded from narration. Nested hidden headings do not replace a
visible chapter title, and hidden inline or self-closing elements do not hide
the text that follows them. Arbitrary CSS is not evaluated during import.
