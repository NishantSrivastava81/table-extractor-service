/** Types mirroring the service result schema, and the calls the UI makes. */

export type JobStatus =
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "expired";

export interface Progress {
    stage: string;
    pages_total: number;
    pages_routed: number;
    pages_done: number;
    tables_found: number;
    percent: number;
}

export interface Job {
    job_id: string;
    status: JobStatus;
    mode: string;
    created_at: string;
    completed_at: string | null;
    document: { filename: string | null; pages: number; bytes: number; sha256: string };
    progress: Progress;
    error: { code: string; message: string } | null;
}

export interface TableSummary {
    table_id: string;
    title: string | null;
    pages: number[];
    n_rows: number;
    n_cols: number;
    rows_before_explosion: number | null;
    columns: string[];
    warnings: number;
    suppressed: boolean;
}

export interface Column {
    index: number;
    key: string;
    label: string;
    type: string;
    currency: string | null;
}

export interface Cell {
    raw: string;
    value: unknown;
    currency: string | null;
    confidence: number | null;
    confidence_source: "ocr" | "grounding" | "unverified";
}

export interface Warning {
    code: string;
    detail: string;
    row_index: number | null;
    column: string | null;
    value: string | null;
}

/** The detail is not a superset of the summary: `columns` and `warnings` differ in shape. */
export interface TableDetail {
    table_id: string;
    title: string | null;
    pages: number[];
    n_rows: number;
    n_cols: number;
    rows_before_explosion: number | null;
    columns: Column[];
    header_rows: string[][];
    rows: Record<string, Cell | number>[];
    provenance: Record<string, unknown>;
    warnings: Warning[];
}

export interface TableIndex {
    job_id: string;
    document: Job["document"];
    summary: {
        tables: number;
        tables_suppressed_as_furniture: number;
        rows: number;
        cells_populated: number;
        verbatim_score: number;
        warnings: number;
    };
    extraction: {
        duration_s: number;
        pages_routed_to_vision: number;
        llm_calls: number;
        prompt_tokens: number;
        completion_tokens: number;
        partial: boolean;
        failed_pages: number[];
    };
    tables: TableSummary[];
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, init);
    if (!response.ok) {
        // The API speaks problem+json; surface its message rather than a bare status.
        let detail = `${response.status} ${response.statusText}`;
        try {
            const body = await response.json();
            detail = body.detail || body.title || detail;
        } catch {
            /* keep the status line */
        }
        throw new Error(detail);
    }
    return response.json() as Promise<T>;
}

export const api = {
    submit(file: File, options: { mode: string; pages?: string }): Promise<Job> {
        const params = new URLSearchParams({ filename: file.name, mode: options.mode });
        if (options.pages) params.set("pages", options.pages);
        return request<Job>(`/v1/jobs?${params}`, {
            method: "POST",
            headers: { "Content-Type": "application/pdf" },
            body: file,
        });
    },

    job: (id: string) => request<Job>(`/v1/jobs/${id}`),

    jobs: () => request<{ jobs: Job[] }>("/v1/jobs?limit=50"),

    tables: (id: string, includeSuppressed: boolean) =>
        request<TableIndex>(`/v1/jobs/${id}/tables?include_suppressed=${includeSuppressed}`),

    table: (id: string, tableId: string) =>
        request<TableDetail>(`/v1/jobs/${id}/tables/${tableId}`),

    pageImageUrl: (id: string, page: number) => `/v1/jobs/${id}/pages/${page}`,

    resultUrl: (id: string) => `/v1/jobs/${id}/result`,

    remove: (id: string) => fetch(`/v1/jobs/${id}`, { method: "DELETE" }),
};

export function isCell(value: unknown): value is Cell {
    return typeof value === "object" && value !== null && "raw" in value;
}
