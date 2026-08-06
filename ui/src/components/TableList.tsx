import { useEffect, useState } from "react";
import { api, type TableIndex } from "../api";

interface Props {
    jobId: string;
    onOpen: (tableId: string) => void;
}

/**
 * A table shaped `N cols x N rows spanning N consecutive pages` is almost always a running
 * header welded together by the merge stage, not real content. Flagging it here is how that
 * class of bug becomes visible instead of hiding in a megabyte of JSON.
 */
function looksLikeMergedFurniture(pages: number[], rows: number): boolean {
    if (pages.length < 3 || rows !== pages.length) return false;
    return pages.every((p, i) => i === 0 || p === pages[i - 1] + 1);
}

export function TableList({ jobId, onOpen }: Props) {
    const [index, setIndex] = useState<TableIndex | null>(null);
    const [showSuppressed, setShowSuppressed] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setIndex(null);
        setError(null);
        api
            .tables(jobId, showSuppressed)
            .then(setIndex)
            .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    }, [jobId, showSuppressed]);

    if (error) return <div className="error">{error}</div>;
    if (!index) return <div className="empty">Loading…</div>;

    const { summary, extraction } = index;
    const suspicious = index.tables.filter((t) => looksLikeMergedFurniture(t.pages, t.n_rows));

    return (
        <div>
            <div className="stats">
                <Stat k="tables" v={summary.tables} />
                <Stat k="rows" v={summary.rows} />
                <Stat k="cells" v={summary.cells_populated.toLocaleString()} />
                <Stat
                    k="verbatim"
                    v={summary.verbatim_score.toFixed(4)}
                    warn={summary.verbatim_score < 0.99}
                />
                <Stat k="warnings" v={summary.warnings} warn={summary.warnings > 0} />
                <Stat k="pages to vision" v={extraction.pages_routed_to_vision} />
                <Stat k="llm calls" v={extraction.llm_calls} />
                <Stat k="duration" v={`${Math.round(extraction.duration_s)}s`} />
            </div>

            {extraction.partial && (
                <div className="error">
                    Partial result: {extraction.failed_pages.length} pages failed (
                    {extraction.failed_pages.join(", ")}). The rest of the document extracted normally.
                </div>
            )}

            {suspicious.length > 0 && (
                <div className="warnings">
                    <h3>{suspicious.length} tables look like merged page furniture</h3>
                    <p style={{ margin: "4px 0 0" }}>
                        One row per page across consecutive pages is the signature of a running header
                        joined by the merge stage. Check these before trusting them:{" "}
                        {suspicious.map((t) => t.table_id).join(", ")}
                    </p>
                </div>
            )}

            <div className="detail-head">
                <h2>Tables</h2>
                <label style={{ fontSize: 12, color: "var(--muted)" }}>
                    <input
                        type="checkbox"
                        checked={showSuppressed}
                        onChange={(e) => setShowSuppressed(e.target.checked)}
                    />{" "}
                    show {summary.tables_suppressed_as_furniture} suppressed
                </label>
                <span className="spacer" style={{ flex: 1 }} />
                <a href={api.resultUrl(jobId)} download>
                    <button>Download JSON</button>
                </a>
            </div>

            <div className="table-list">
                {index.tables.map((t) => (
                    <div
                        key={t.table_id}
                        className={`table-row${t.suppressed ? " suppressed" : ""}`}
                        onClick={() => onOpen(t.table_id)}
                    >
                        <span className="id">{t.table_id}</span>
                        <span className="title">
                            {t.title ?? t.columns.filter(Boolean).slice(0, 3).join(" · ") ?? "untitled"}
                        </span>
                        {looksLikeMergedFurniture(t.pages, t.n_rows) && (
                            <span className="chip flag">furniture?</span>
                        )}
                        {t.warnings > 0 && <span className="chip">{t.warnings} warnings</span>}
                        <span className="shape">
                            {t.n_cols}×{t.n_rows}
                        </span>
                        <span className="shape">
                            p{t.pages[0]}
                            {t.pages.length > 1 ? `–${t.pages[t.pages.length - 1]}` : ""}
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}

function Stat({ k, v, warn }: { k: string; v: string | number; warn?: boolean }) {
    return (
        <div className={`stat${warn ? " warn" : ""}`}>
            <div className="v">{v}</div>
            <div className="k">{k}</div>
        </div>
    );
}
