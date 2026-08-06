import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Job } from "./api";
import { JobList } from "./components/JobList";
import { TableList } from "./components/TableList";
import { TableView } from "./components/TableView";
import { Upload } from "./components/Upload";
import { useHashRoute } from "./useHashRoute";

const POLL_MS = 2000;
const ACTIVE = new Set(["queued", "running"]);

export function App() {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [route, navigate] = useHashRoute();
    const [linked, setLinked] = useState<Job | null>(null);
    const [error, setError] = useState<string | null>(null);
    const timer = useRef<number>();

    const refresh = useCallback(async () => {
        try {
            const body = await api.jobs();
            setJobs(body.jobs);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    // A linked job may sit outside the listed page, so fetch it on its own.
    const listed = jobs.some((j) => j.job_id === route.jobId);
    useEffect(() => {
        if (!route.jobId || listed) {
            setLinked(null);
            return;
        }
        let live = true;
        api.job(route.jobId).then(
            (job) => live && setLinked(job),
            (e: unknown) => live && setError(e instanceof Error ? e.message : String(e)),
        );
        return () => {
            live = false;
        };
    }, [route.jobId, listed]);

    const current = (listed ? jobs.find((j) => j.job_id === route.jobId) : linked) ?? null;

    // Poll only while something is actually in flight, so an idle tab is quiet.
    useEffect(() => {
        const busy =
            jobs.some((j) => ACTIVE.has(j.status)) || (current != null && ACTIVE.has(current.status));
        if (!busy) return;
        timer.current = window.setTimeout(() => {
            void refresh();
            if (current && !listed) void api.job(current.job_id).then(setLinked, () => undefined);
        }, POLL_MS);
        return () => window.clearTimeout(timer.current);
    }, [jobs, current, listed, refresh]);

    const openJob = (id: string | null) => {
        setError(null);
        navigate({ jobId: id, tableId: null });
    };

    return (
        <div className="app">
            <header>
                <h1>Table Extractor</h1>
                <span className="spacer" />
                {current && (
                    <span style={{ color: "var(--muted)", fontSize: 12 }}>
                        {current.document.filename} · {current.document.pages} pages
                        {current.mode && ` · ${current.mode}`}
                    </span>
                )}
            </header>

            <div className="layout">
                <aside className="sidebar">
                    <Upload
                        onSubmitted={(job) => {
                            openJob(job.job_id);
                            void refresh();
                        }}
                        onError={setError}
                    />
                    <JobList jobs={jobs} selected={route.jobId} onSelect={openJob} />
                </aside>

                <main className="main">
                    {error && <div className="error">{error}</div>}

                    {!current && !route.jobId && (
                        <div className="empty">Upload a PDF, or pick a job on the left.</div>
                    )}
                    {!current && route.jobId && !error && <div className="empty">Loading job…</div>}

                    {current && ACTIVE.has(current.status) && (
                        <div className="empty">
                            <div style={{ fontSize: 16, marginBottom: 8 }}>
                                {current.progress.stage} · {current.progress.percent}%
                            </div>
                            <div style={{ fontSize: 13 }}>
                                {current.progress.pages_routed > 0 && (
                                    <>
                                        {current.progress.pages_done} of {current.progress.pages_routed} pages sent
                                        to the model
                                        {current.progress.pages_total > 0 && (
                                            <>
                                                {" "}
                                                ({current.mode === "thorough"
                                                    ? `thorough mode sends all ${current.progress.pages_total}`
                                                    : `${current.progress.pages_routed} of ${current.progress.pages_total} pages needed one`}
                                                )
                                            </>
                                        )}
                                    </>
                                )}
                            </div>
                            <div className="bar" style={{ maxWidth: 380, margin: "12px auto" }}>
                                <div style={{ width: `${current.progress.percent}%` }} />
                            </div>
                        </div>
                    )}

                    {current?.status === "failed" && (
                        <div className="error">
                            {current.error?.code}: {current.error?.message}
                        </div>
                    )}

                    {current?.status === "succeeded" &&
                        (route.tableId ? (
                            <TableView
                                jobId={current.job_id}
                                tableId={route.tableId}
                                onBack={() => navigate({ jobId: current.job_id, tableId: null })}
                            />
                        ) : (
                            <TableList
                                jobId={current.job_id}
                                onOpen={(tableId) => navigate({ jobId: current.job_id, tableId })}
                            />
                        ))}
                </main>
            </div>
        </div>
    );
}
