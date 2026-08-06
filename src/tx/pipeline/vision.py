"""The vision leg: page strips in, HTML tables out.

The prompt is a fixed constant. No caller-supplied text ever reaches it, which closes prompt
injection from the request side. Document content is still untrusted, so the response is parsed
as a grid and treated as data: never executed, never used to build a query, never obeyed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tx.core.config import settings
from tx.pipeline.grid import top_level_tables
from tx.pipeline.llm import Usage, chat
from tx.pipeline.render import data_url, render_strips

SYSTEM = """You are a document parser. Convert the document pages into structured HTML.

- Emit tables as <table> with <thead>/<tbody>, <tr>, <th>, <td>.
- Use colspan and rowspan to preserve merged cells and multi-level headers.
- If a cell itself contains a table (sub-lines, rate tiers), emit a nested <table> inside
  that <td>. Never flatten a nested table into extra columns.
- Ignore page furniture: running headers, footers, confidentiality banners, page numbers.
- Transcribe every numeric cell. Do not summarise, do not write notes in place of values.
- Reproduce every code, number and identifier EXACTLY as printed, including apparent
  misspellings. Never normalise, correct or reformat them.
- Output only HTML. No commentary, no markdown fences."""

_FENCE = re.compile(r"^```(?:html)?|```$", re.M)


@dataclass
class VisionPage:
    page: int
    tables: list[str]
    strips: int


def _instruction(page: int, strips: int) -> str:
    if strips == 1:
        return f"This is page {page} of a document. Parse it. Transcribe every numeric cell."
    return (
        f"This is page {page} of a document, supplied as {strips} horizontal strips from top "
        "to bottom that overlap slightly. They are ONE page: reassemble them into a single "
        "table and drop rows the overlap duplicated. Transcribe every numeric cell."
    )


def parse_page(pdf: bytes, page: int, strips: int, usage: Usage | None = None) -> VisionPage:
    images = render_strips(pdf, page, strips)
    content: list[dict] = [{"type": "text", "text": _instruction(page, len(images))}]
    content += [
        {
            "type": "image_url",
            "image_url": {"url": data_url(png), "detail": settings.strip_detail},
        }
        for png in images
    ]

    raw = chat(
        settings.foundry_vision_deployment,
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        max_tokens=settings.vision_max_tokens,
        usage=usage,
    )
    return VisionPage(
        page=page, tables=top_level_tables(_FENCE.sub("", raw).strip()), strips=len(images)
    )
