/**
 * Hash routing: `#/job/{jobId}/table/{tableId}`.
 *
 * The hash is used rather than a path because the UI is served by StaticFiles, which has no
 * SPA fallback: a real path would 404 on refresh.
 */

import { useCallback, useEffect, useState } from "react";

export interface Route {
    jobId: string | null;
    tableId: string | null;
}

function parse(hash: string): Route {
    const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    const route: Route = { jobId: null, tableId: null };
    for (let i = 0; i < parts.length - 1; i += 2) {
        if (parts[i] === "job") route.jobId = decodeURIComponent(parts[i + 1]);
        if (parts[i] === "table") route.tableId = decodeURIComponent(parts[i + 1]);
    }
    return route;
}

export function toHash({ jobId, tableId }: Route): string {
    if (!jobId) return "#/";
    return tableId ? `#/job/${jobId}/table/${tableId}` : `#/job/${jobId}`;
}

export function useHashRoute(): [Route, (next: Route) => void] {
    const [route, setRoute] = useState<Route>(() => parse(window.location.hash));

    useEffect(() => {
        const onChange = () => setRoute(parse(window.location.hash));
        window.addEventListener("hashchange", onChange);
        return () => window.removeEventListener("hashchange", onChange);
    }, []);

    const navigate = useCallback((next: Route) => {
        const hash = toHash(next);
        if (hash !== window.location.hash) {
            // pushState keeps browser back working through table selections.
            window.history.pushState(null, "", hash);
        }
        setRoute(next);
    }, []);

    // pushState does not raise hashchange, so back and forward need popstate too.
    useEffect(() => {
        const onPop = () => setRoute(parse(window.location.hash));
        window.addEventListener("popstate", onPop);
        return () => window.removeEventListener("popstate", onPop);
    }, []);

    return [route, navigate];
}
