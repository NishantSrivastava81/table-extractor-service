# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[azure]"

# Not root: this process parses hostile binaries.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown -R app:app /data /app
USER app

ENV DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

# The API also runs the worker unless RUN_WORKER_IN_PROCESS=false, in which case start a
# second container with: python -m tx.worker.runner
CMD ["python", "-m", "uvicorn", "tx.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
