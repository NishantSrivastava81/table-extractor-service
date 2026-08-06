import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served from the API at /ui, so the built asset paths must be relative to that.
export default defineConfig({
    plugins: [react()],
    base: "/ui/",
    server: {
        port: 5173,
        // Dev server talks to the API through this proxy, which keeps everything same-origin
        // and means CORS stays off.
        proxy: {
            "/v1": "http://127.0.0.1:8000",
            "/healthz": "http://127.0.0.1:8000",
        },
    },
    build: { outDir: "dist", emptyOutDir: true },
});
