import { useRef, useState } from "react";
import { api, type Job } from "../api";

interface Props {
    onSubmitted: (job: Job) => void;
    onError: (message: string) => void;
}

export function Upload({ onSubmitted, onError }: Props) {
    const [over, setOver] = useState(false);
    const [busy, setBusy] = useState(false);
    const [mode, setMode] = useState("balanced");
    const [pages, setPages] = useState("");
    const input = useRef<HTMLInputElement>(null);

    async function send(file: File) {
        if (!file.name.toLowerCase().endsWith(".pdf")) {
            onError("Only PDF files are accepted.");
            return;
        }
        setBusy(true);
        try {
            onSubmitted(await api.submit(file, { mode, pages: pages.trim() || undefined }));
        } catch (err) {
            onError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    }

    return (
        <>
            <div
                className={`drop${over ? " over" : ""}`}
                onDragOver={(e) => {
                    e.preventDefault();
                    setOver(true);
                }}
                onDragLeave={() => setOver(false)}
                onDrop={(e) => {
                    e.preventDefault();
                    setOver(false);
                    const file = e.dataTransfer.files?.[0];
                    if (file) void send(file);
                }}
                onClick={() => input.current?.click()}
            >
                {busy ? "Uploading…" : "Drop a PDF here, or click to choose"}
                <input
                    ref={input}
                    type="file"
                    accept="application/pdf,.pdf"
                    onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) void send(file);
                        e.target.value = "";
                    }}
                />
            </div>

            <div className="controls">
                <label htmlFor="mode">Mode</label>
                <select id="mode" value={mode} onChange={(e) => setMode(e.target.value)}>
                    <option value="fast">fast (OCR only)</option>
                    <option value="balanced">balanced</option>
                    <option value="thorough">thorough</option>
                </select>
                <label htmlFor="pages">Pages</label>
                <input
                    id="pages"
                    value={pages}
                    placeholder="all"
                    onChange={(e) => setPages(e.target.value)}
                />
            </div>
        </>
    );
}
