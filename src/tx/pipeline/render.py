"""Page rendering and strip cropping.

Two things here are not obvious and both were measured.

PDFium is not thread-safe. Concurrent access crashes the interpreter with a native access
violation rather than a Python exception, so every call is serialised behind one lock. The
vision calls are the slow part and they parallelise freely, so this costs almost nothing.

Render scale cannot buy resolution on a whole page: the chat API normalises every image to a
fixed visual-token budget, so scale 2, 4 and 6 all cost the same and all return the same thing.
Scale matters only after cropping, because a strip must still carry more pixels than the
normalisation target or it is an upscaled blur.
"""

from __future__ import annotations

import base64
import io
import threading

import pypdfium2 as pdfium

from tx.core.config import settings

_LOCK = threading.Lock()


def page_count(pdf: bytes) -> int:
    with _LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            return len(doc)
        finally:
            doc.close()


def has_large_raster(pdf: bytes, page: int, min_area_fraction: float = 0.25) -> bool:
    """True when a page is largely a bitmap, which is where OCR does its worst reading."""
    with _LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            obj_page = doc[page - 1]
            width, height = obj_page.get_width(), obj_page.get_height()
            area = max(width * height, 1)
            for obj in obj_page.get_objects():
                if type(obj).__name__ != "PdfImage":
                    continue
                try:
                    x0, y0, x1, y1 = obj.get_bounds()
                except Exception:  # noqa: BLE001 - some images report no usable bounds
                    continue
                if abs((x1 - x0) * (y1 - y0)) / area >= min_area_fraction:
                    return True
            return False
        finally:
            doc.close()


def render_strips(pdf: bytes, page: int, strips: int) -> list[bytes]:
    """One page as overlapping horizontal strips, top to bottom.

    Each strip gets its own visual-token budget, which is the only way to make a dense
    rasterised grid legible. On the reference contract a whole page returned 0 of ~900 values
    and four strips returned 888.
    """
    strips = max(1, min(strips, settings.max_strips))
    with _LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            image = doc[page - 1].render(scale=settings.render_scale).to_pil()
        finally:
            doc.close()

    if strips == 1:
        return [_png(image)]

    width, height = image.size
    step = height / strips
    overlap = settings.strip_overlap * height
    out: list[bytes] = []
    for i in range(strips):
        top = max(0, int(i * step - overlap))
        bottom = min(height, int((i + 1) * step + overlap))
        out.append(_png(image.crop((0, top, width, bottom))))
    return out


def render_page(pdf: bytes, page: int, scale: float | None = None) -> bytes:
    """A whole page as one PNG, for showing a human where a value came from."""
    with _LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            image = doc[page - 1].render(scale=scale or settings.page_image_scale).to_pil()
        finally:
            doc.close()
    return _png(image)


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()
