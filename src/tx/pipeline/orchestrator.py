"""Stage sequencing.

OCR always, vision only where the router says it earns its cost, then reconcile, merge, flatten
and validate. A page that fails does not fail the document:
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from tx import __version__
from tx.core.config import settings
from tx.core.logging import get_logger
from tx.core.models import (
    ExtractionInfo,
    Job,
    Progress,
    Result,
    Summary,
    Table,
    utcnow,
)
from tx.metrics import PAGES_ROUTED_RATIO
from tx.pipeline import merge as merge_stage
from tx.pipeline import ocr as ocr_stage
from tx.pipeline import render as render_stage
from tx.pipeline import router as router_stage
from tx.pipeline import validate as validate_stage
from tx.pipeline import vision as vision_stage
from tx.pipeline.base import ProgressFn
from tx.pipeline.flatten import flatten
from tx.pipeline.grid import norm_text, parse_table, table_text
from tx.pipeline.llm import Usage
from tx.pipeline.pdfdoc import parse_page_range
from tx.pipeline.reconcile import ReconcileReport, reconcile_html

log = get_logger(__name__)


def _is_furniture(rows: int, cols: int) -> bool:
    """Roughly half the tables on a real contract are the running header block.

    Short blocks are furniture. A narrow one is not, if it is long: a 181-row single-column
    equipment list is content that happens to have one column.
    """
    if rows < settings.suppress_min_rows:
        return True
    return cols < settings.suppress_min_cols and rows < settings.suppress_min_list_rows


def drop_repeating_furniture(
    fragments: list[merge_stage.Fragment],
) -> tuple[list[merge_stage.Fragment], int]:
    """Remove short blocks whose content repeats across pages: letterheads and banners.

    This has to happen before merging. A letterhead is two rows on each of eighteen pages, and
    merging them first produces an eighteen-row table that passes every later filter.
    """
    pages_by_signature: dict[str, set[int]] = {}
    signatures: dict[str, str] = {}
    for fragment in fragments:
        grid = parse_table(fragment.html)
        if len(grid.body()) >= settings.suppress_min_rows:
            continue
        signature = norm_text(table_text(fragment.html)).lower()
        if not signature:
            continue
        signatures[fragment.fragment_id] = signature
        pages_by_signature.setdefault(signature, set()).add(fragment.page)

    repeating = {
        signature
        for signature, pages in pages_by_signature.items()
        if len(pages) >= settings.furniture_repeat_pages
    }
    if not repeating:
        return fragments, 0

    kept = [f for f in fragments if signatures.get(f.fragment_id) not in repeating]
    return kept, len(fragments) - len(kept)


def _raster_pages(pdf: bytes, pages: set[int]) -> set[int]:
    """Pages that are mostly a bitmap, which the router tiles more finely."""
    out: set[int] = set()
    for page in pages:
        try:
            if render_stage.has_large_raster(pdf, page):
                out.add(page)
        except Exception:  # noqa: BLE001 - a page we cannot inspect is simply not special-cased
            log.debug("raster probe failed", extra={"page": page})
    return out


class PipelineExtractor:
    name = "pipeline"

    def extract(self, pdf: bytes, job: Job, on_progress: ProgressFn) -> Result:
        started = utcnow()
        clock = time.perf_counter()
        usage = Usage()

        progress = Progress(stage="ocr", pages_total=job.document.pages)
        on_progress(progress)

        ocr = ocr_stage.analyze(pdf)
        progress.pages_total = ocr.n_pages or job.document.pages
        allowed = set(parse_page_range(job.options.pages, progress.pages_total))

        plans = router_stage.plan(
            ocr,
            allowed_pages=allowed,
            mode=job.options.mode,
            raster_pages=_raster_pages(pdf, allowed),
        )
        progress.pages_routed = len(plans)
        progress.stage = "vision"
        on_progress(progress)
        if progress.pages_total:
            PAGES_ROUTED_RATIO.observe(len(plans) / progress.pages_total)
        log.info(
            "routing decided",
            extra={
                "pages_total": progress.pages_total,
                "pages_routed": len(plans),
                "strips": sum(p.strips for p in plans),
            },
        )

        fragments: list[merge_stage.Fragment] = []
        failed_pages: list[int] = []

        if plans and settings.vision_configured:
            with ThreadPoolExecutor(max_workers=settings.vision_concurrency) as pool:
                futures = {
                    pool.submit(vision_stage.parse_page, pdf, p.page, p.strips, usage): p
                    for p in plans
                }
                for future in as_completed(futures):
                    plan = futures[future]
                    try:
                        page_result = future.result()
                        for index, html in enumerate(page_result.tables):
                            fragments.append(
                                merge_stage.Fragment(
                                    fragment_id=f"p{plan.page:04d}_{index}",
                                    html=html,
                                    page=plan.page,
                                )
                            )
                    except Exception:  # noqa: BLE001 - one page must not lose the document
                        log.exception("page failed", extra={"page": plan.page})
                        failed_pages.append(plan.page)
                    progress.pages_done += 1
                    progress.tables_found = len(fragments)
                    on_progress(progress)

        # Pages the router skipped, or every page in fast mode, still contribute their OCR
        # tables, so nothing is dropped merely because it did not earn a vision call.
        covered = {p.page for p in plans} - set(failed_pages)
        for index, table in enumerate(ocr.tables):
            page = table.pages[0] if table.pages else 1
            if page in covered or page not in allowed:
                continue
            fragments.append(
                merge_stage.Fragment(fragment_id=f"o{page:04d}_{index}", html=table.html, page=page)
            )

        progress.stage = "reconcile"
        on_progress(progress)
        report = ReconcileReport()
        for fragment in fragments:
            fragment.html, one = reconcile_html(fragment.html, ocr.page_texts)
            report.merge(one)

        progress.stage = "merge"
        on_progress(progress)
        fragments, furniture_dropped = drop_repeating_furniture(fragments)
        merged, merge_log = merge_stage.merge_fragments(fragments, usage)

        progress.stage = "flatten"
        on_progress(progress)
        tables: list[Table] = []
        suppressed: list[Table] = []
        for index, item in enumerate(merged, start=1):
            table = flatten(
                table_id=f"t_{index:04d}",
                html=item.html,
                pages=item.pages,
                title=item.title,
                locale_hint=job.options.locale_hint,
                default_currency=job.options.default_currency or settings.default_currency,
                confidence=report.confidence,
                provenance={
                    "source": "vision" if item.merged_from[0].startswith("p") else "ocr",
                    "merged_from": item.merged_from,
                },
            )
            grid = parse_table(item.html)
            if _is_furniture(len(grid.body()), grid.n_cols):
                suppressed.append(table)
            else:
                tables.append(table)

        verbatim = validate_stage.check([m.html for m in merged], ocr.page_texts)

        completed = utcnow()
        totals = usage.snapshot()
        progress.stage = "done"
        progress.tables_found = len(tables)
        on_progress(progress)

        result = Result(
            job_id=job.job_id,
            document=job.document.model_copy(update={"pages": progress.pages_total}),
            extraction=ExtractionInfo(
                engine_version=__version__,
                mode=job.options.mode,
                started_at=started,
                completed_at=completed,
                duration_s=round(time.perf_counter() - clock, 2),
                pages_routed_to_vision=len(plans),
                llm_calls=totals["calls"],
                prompt_tokens=totals["prompt_tokens"],
                completion_tokens=totals["completion_tokens"],
                partial=bool(failed_pages),
                failed_pages=sorted(failed_pages),
            ),
            summary=Summary(
                tables=len(tables),
                tables_suppressed_as_furniture=len(suppressed),
                rows=sum(t.n_rows for t in tables),
                cells_populated=sum(int(t.provenance.get("cells_populated", 0)) for t in tables),
                verbatim_score=round(verbatim.score, 4),
                warnings=sum(len(t.warnings) for t in tables),
            ),
            tables=tables,
            suppressed_tables=suppressed,
        )
        log.info(
            "extraction complete",
            extra={
                "tables": len(tables),
                "suppressed": len(suppressed),
                "furniture_dropped": furniture_dropped,
                "verbatim": result.summary.verbatim_score,
                "llm_calls": totals["calls"],
                "merge_decisions": len(merge_log),
                "identifiers_repaired": report.repaired,
                "numbers_ungrounded": report.numbers_ungrounded,
            },
        )
        return result
