import type { Job } from "../api";

interface Props {
    jobs: Job[];
    selected: string | null;
    onSelect: (id: string) => void;
}

const ACTIVE = new Set(["queued", "running"]);

export function JobList({ jobs, selected, onSelect }: Props) {
    if (!jobs.length) {
        return <div className="empty">No jobs yet.</div>;
    }
    return (
        <div>
            {jobs.map((job) => {
                const running = ACTIVE.has(job.status);
                return (
                    <div
                        key={job.job_id}
                        className={`job${job.job_id === selected ? " active" : ""}`}
                        onClick={() => onSelect(job.job_id)}
                    >
                        <div className="name">{job.document.filename ?? job.job_id}</div>
                        <div className="meta">
                            <span className={`badge ${job.status}`}>{job.status}</span>{" "}
                            {job.document.pages} pages
                            {running && ` · ${job.progress.stage}`}
                            {job.status === "succeeded" && ` · ${job.progress.tables_found} tables`}
                        </div>
                        {running && (
                            <div className="bar">
                                <div style={{ width: `${job.progress.percent}%` }} />
                            </div>
                        )}
                        {job.error && <div className="meta">{job.error.code}</div>}
                    </div>
                );
            })}
        </div>
    );
}
