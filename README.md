# Table Extractor Service

Submit a PDF, poll a job, receive its tables as flat JSON.



## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[azure,dev]"
Copy-Item .env.example .env      # fill in the provider values
.\.venv\Scripts\python.exe -m uvicorn tx.api.app:app --host 127.0.0.1 --port 8000
```

That is the whole thing. No database, no queue broker, no object store: SQLite and the local
filesystem, with the worker running inside the API process.

To run without touching Azure at all, set `EXTRACTOR=null`. The full API works and returns a
fixed sample result.

## Use it

```powershell
$bytes = [System.IO.File]::ReadAllBytes("contract.pdf")
$job = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/jobs?filename=contract.pdf" `
                         -Body $bytes -ContentType "application/pdf"

do {
  Start-Sleep -Seconds 5
  $state = Invoke-RestMethod "http://127.0.0.1:8000/v1/jobs/$($job.job_id)"
  "$($state.status) $($state.progress.stage) $($state.progress.percent)%"
} while ($state.status -notin @("succeeded","failed"))

Invoke-RestMethod "http://127.0.0.1:8000/v1/jobs/$($job.job_id)/result" |
  ConvertTo-Json -Depth 6 | Out-File result.json
```

Useful query parameters: `pages=128-142`, `mode=fast|balanced|thorough`, `locale_hint=de-DE`,
`currency=AUD`.

OpenAPI is at `/docs`. Metrics at `/metrics`.

## What it does

```
PDF ─► Document Intelligence (one call, whole document)
    ─► router      which pages earn a vision call, and how finely to slice each
    ─► render      page to overlapping strips, locally
    ─► vision      one model call per routed page
    ─► reconcile   identifiers checked against what OCR read
    ─► merge       deterministic filter, model decides ties only, deterministic stitch
    ─► flatten     typed columns, nested tables exploded into the parent
    ─► validate    every value must appear in the OCR reading, or it is flagged
```



## Configuration

Everything is an environment variable and nothing is hard-coded. See
[.env.example](.env.example) for the full set. The values most worth tuning:

| variable | default | effect |
|---|---|---|
| `EXTRACTOR` | `pipeline` | `null` runs the service with no provider calls |
| `VISION_CONCURRENCY` | `8` | pages processed in parallel |
| `ROWS_PER_STRIP` | `22` | rows one strip carries legibly |
| `ROUTER_MIN_SHORT_LINES` | `30` | catches list tables OCR does not detect |
| `GROUND_NUMBERS` | `false` | true deletes unconfirmable numbers |
| `SUPPRESS_MIN_LIST_ROWS` | `10` | a taller single-column block is content, not furniture |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Contract tests run against the null extractor, so the suite needs no credentials and costs
nothing.

