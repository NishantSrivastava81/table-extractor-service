import { useEffect, useState } from "react";
import { api, isCell, type Cell, type TableDetail } from "../api";

interface Props {
    jobId: string;
    tableId: string;
    onBack: () => void;
}

/** Columns a locale check could not resolve: every cell there is raw-only by design. */
function ambiguousColumns(table: TableDetail): Set<string> {
    return new Set(
        table.warnings
            .filter((w) => w.code === "LOCALE_AMBIGUOUS" && w.column)
            .map((w) => w.column as string),
    );
}

export function TableView({ jobId, tableId, onBack }: Props) {
    const [table, setTable] = useState<TableDetail | null>(null);
    const [page, setPage] = useState<number | null>(null);
    const [zoom, setZoom] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setTable(null);
        setError(null);
        api
            .table(jobId, tableId)
            .then((t) => {
                setTable(t);
                setPage(t.pages[0] ?? null);
            })
            .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    }, [jobId, tableId]);

    if (error) return <div className="error">{error}</div>;
    if (!table) return <div className="empty">Loading…</div>;

    const keys = Object.keys(table.rows[0] ?? {}).filter((k) => k !== "row_index");
    const ambiguous = ambiguousColumns(table);

    return (
        <div>
            <div className="detail-head">
                <button onClick={onBack}>← Tables</button>
                <h2>{table.title ?? table.table_id}</h2>
                <span className="shape">
                    {table.n_cols} cols × {table.n_rows} rows
                    {table.rows_before_explosion != null &&
                        ` (exploded from ${table.rows_before_explosion})`}
                </span>
            </div>

            <div className="legend">
                <span>
                    <i className="swatch" style={{ background: "#fdeceb", borderColor: "#c2382c" }} />
                    value not parsed
                </span>
                <span>
                    <i className="swatch" style={{ background: "#fff4e0", borderColor: "#d99b28" }} />
                    locale ambiguous, raw kept
                </span>
                <span>Hover a cell to see the raw text and its confidence source.</span>
            </div>

            <div className="split">
                <div className="grid-wrap">
                    <table className="grid">
                        <thead>
                            <tr>
                                <th>#</th>
                                {keys.map((k) => (
                                    <th key={k}>{k}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {table.rows.map((row, i) => (
                                <tr key={i}>
                                    <td className="rowno">{String(row.row_index ?? i)}</td>
                                    {keys.map((k) => {
                                        const cell = row[k];
                                        if (!isCell(cell)) return <td key={k}>{String(cell ?? "")}</td>;
                                        return <CellCell key={k} cell={cell} ambiguous={ambiguous.has(k)} />;
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                {page != null && (
                    <div className="page-wrap">
                        <div className="page-tabs">
                            {table.pages.map((p) => (
                                <button
                                    key={p}
                                    className={p === page ? "active" : ""}
                                    onClick={() => setPage(p)}
                                >
                                    p{p}
                                </button>
                            ))}
                            <span className="spacer" />
                            <button onClick={() => setZoom(!zoom)}>{zoom ? "Fit" : "Actual size"}</button>
                        </div>
                        <div className="page-scroll">
                            <img
                                className={zoom ? "full" : "fit"}
                                src={api.pageImageUrl(jobId, page)}
                                alt={`Source page ${page}`}
                            />
                        </div>
                    </div>
                )}
            </div>

            {table.warnings.length > 0 && (
                <div className="warnings">
                    <h3>{table.warnings.length} warnings</h3>
                    <ul>
                        {table.warnings.slice(0, 25).map((w, i) => (
                            <li key={i}>
                                <code>{w.code}</code>
                                {w.column && <> in <code>{w.column}</code></>}
                                {w.row_index != null && <> row {w.row_index}</>}
                                {w.value && <> — <code>{w.value}</code></>} {w.detail}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

function CellCell({ cell, ambiguous }: { cell: Cell; ambiguous: boolean }) {
    const numeric = typeof cell.value === "number";
    const unparsed = cell.raw.trim() !== "" && cell.value === null && !ambiguous;
    const shown = cell.value === null || cell.value === undefined ? cell.raw : String(cell.value);
    const className = [numeric ? "num" : "", unparsed ? "unparsed" : "", ambiguous ? "flagged" : ""]
        .filter(Boolean)
        .join(" ");
    return (
        <td
            className={className}
            title={`raw: ${cell.raw}\nvalue: ${String(cell.value)}\nconfidence: ${cell.confidence ?? "n/a"
                } (${cell.confidence_source})`}
        >
            {shown}
            {cell.currency && numeric ? ` ${cell.currency}` : ""}
        </td>
    );
}
