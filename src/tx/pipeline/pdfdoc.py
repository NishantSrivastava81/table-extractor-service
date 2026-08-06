"""PDF inspection and page-range parsing.

Validation happens on the request path so a bad upload is rejected before it costs a job, and
it trusts magic bytes rather than the declared content type or the filename.
"""

from __future__ import annotations

import re

import pypdfium2 as pdfium

from tx.core.errors import EncryptedPdf, InvalidPageRange, UnreadablePdf, UnsupportedMediaType

PDF_MAGIC = b"%PDF"
_RANGE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def inspect(data: bytes) -> int:
    """Validate the bytes are an openable PDF and return the page count."""
    if not data[:1024].lstrip().startswith(PDF_MAGIC):
        raise UnsupportedMediaType(
            f"Leading bytes were {data[:4].hex()!r}, expected {PDF_MAGIC.decode()!r}."
        )
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise EncryptedPdf("The document requires a password.") from exc
        raise UnreadablePdf(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - hostile input, never leak the internals
        raise UnreadablePdf(f"{type(exc).__name__} while opening the document.") from exc

    try:
        pages = len(doc)
    finally:
        doc.close()

    if pages < 1:
        raise UnreadablePdf("The document contains no pages.")
    return pages


def parse_page_range(spec: str | None, total: int) -> list[int]:
    """`"1-50,120,130-142"` to a sorted list of 1-based page numbers."""
    if not spec or not spec.strip():
        return list(range(1, total + 1))

    pages: set[int] = set()
    for part in spec.split(","):
        match = _RANGE.match(part)
        if not match:
            raise InvalidPageRange(f"Could not parse {part.strip()!r}.")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start < 1 or end < start:
            raise InvalidPageRange(f"{part.strip()!r} is not a valid range.")
        if end > total:
            raise InvalidPageRange(
                f"{part.strip()!r} exceeds the document, which has {total} pages."
            )
        pages.update(range(start, end + 1))

    if not pages:
        raise InvalidPageRange("The range selected no pages.")
    return sorted(pages)
