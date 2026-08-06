"""Azure Document Intelligence: the OCR leg.

It does three jobs the vision leg cannot. It reads every page cheaply, it supplies the
vocabulary that generated values are checked against, and it is the only source of real page
numbers, because bare HTML from a model carries no coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from tx.core.config import settings
from tx.core.errors import DependencyUnavailable, NotConfigured
from tx.core.logging import get_logger

log = get_logger(__name__)

#: Roles Document Intelligence assigns to repeating page furniture. Stripping these before
#: anything else is the highest-value preprocessing step in the pipeline.
FURNITURE_ROLES = {"pageHeader", "pageFooter", "pageNumber"}


@dataclass
class OcrTable:
    html: str
    pages: list[int]
    n_rows: int
    n_cols: int
    cell_confidence: dict[tuple[int, int], float] = field(default_factory=dict)


@dataclass
class OcrResult:
    n_pages: int
    page_texts: dict[int, str] = field(default_factory=dict)
    tables: list[OcrTable] = field(default_factory=list)


@lru_cache(maxsize=1)
def _client():
    if not settings.azure_di_endpoint:
        raise NotConfigured("AZURE_DI_ENDPOINT is not set.")
    from azure.ai.documentintelligence import DocumentIntelligenceClient

    if settings.azure_di_key:
        from azure.core.credentials import AzureKeyCredential

        credential = AzureKeyCredential(settings.azure_di_key)
    else:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
    return DocumentIntelligenceClient(endpoint=settings.azure_di_endpoint, credential=credential)


def _cells_to_html(table) -> tuple[str, dict[tuple[int, int], float]]:
    rows: dict[int, list] = {}
    confidence: dict[tuple[int, int], float] = {}
    for cell in table.cells or []:
        rows.setdefault(cell.row_index, []).append(cell)
        if (value := getattr(cell, "confidence", None)) is not None:
            confidence[(cell.row_index, cell.column_index)] = float(value)

    header_rows = sorted(
        {c.row_index for c in (table.cells or []) if getattr(c, "kind", "") == "columnHeader"}
    )
    n_header = 0
    while n_header in header_rows:
        n_header += 1

    def render(index: int, tag: str) -> str:
        out = []
        for cell in sorted(rows.get(index, []), key=lambda c: c.column_index):
            attrs = ""
            if (cell.column_span or 1) > 1:
                attrs += f' colspan="{cell.column_span}"'
            if (cell.row_span or 1) > 1:
                attrs += f' rowspan="{cell.row_span}"'
            text = (
                (cell.content or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            out.append(f"<{tag}{attrs}>{text}</{tag}>")
        return "<tr>" + "".join(out) + "</tr>"

    parts = ["<table>"]
    if n_header:
        parts.append("<thead>" + "".join(render(i, "th") for i in range(n_header)) + "</thead>")
    parts.append(
        "<tbody>" + "".join(render(i, "td") for i in sorted(rows) if i >= n_header) + "</tbody>"
    )
    parts.append("</table>")
    return "".join(parts), confidence


def analyze(pdf: bytes) -> OcrResult:
    from azure.ai.documentintelligence.models import (
        AnalyzeDocumentRequest,
        DocumentContentFormat,
    )

    try:
        poller = _client().begin_analyze_document(
            settings.azure_di_model,
            AnalyzeDocumentRequest(bytes_source=pdf),
            output_content_format=DocumentContentFormat.MARKDOWN,
        )
        result = poller.result()
    except NotConfigured:
        raise
    except Exception as exc:  # noqa: BLE001 - provider detail never reaches the client
        raise DependencyUnavailable(
            f"Document Intelligence failed ({type(exc).__name__})."
        ) from exc

    content = result.content or ""

    furniture: list[tuple[int, int]] = []
    for paragraph in result.paragraphs or []:
        if getattr(paragraph, "role", None) in FURNITURE_ROLES:
            for span in paragraph.spans or []:
                furniture.append((span.offset, span.offset + span.length))

    page_texts: dict[int, str] = {}
    for page in result.pages or []:
        chunks = []
        for span in page.spans or []:
            segment = content[span.offset : span.offset + span.length]
            for start, end in furniture:
                if span.offset <= start < span.offset + span.length:
                    segment = segment.replace(content[start:end], " ")
            chunks.append(segment)
        page_texts[page.page_number] = " ".join(chunks)

    tables: list[OcrTable] = []
    for table in result.tables or []:
        html, confidence = _cells_to_html(table)
        pages = sorted({r.page_number for r in (table.bounding_regions or [])}) or [1]
        tables.append(
            OcrTable(
                html=html,
                pages=pages,
                n_rows=table.row_count or 0,
                n_cols=table.column_count or 0,
                cell_confidence=confidence,
            )
        )

    log.info(
        "ocr complete",
        extra={"pages": len(result.pages or []), "tables": len(tables)},
    )
    return OcrResult(n_pages=len(result.pages or []), page_texts=page_texts, tables=tables)
