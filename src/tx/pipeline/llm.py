"""Azure OpenAI client with retry, usage accounting and both auth modes."""

from __future__ import annotations

import random
import time
from functools import lru_cache
from threading import Lock
from typing import Any

from tx.core.config import settings
from tx.core.errors import DependencyUnavailable, NotConfigured
from tx.core.logging import get_logger
from tx.metrics import LLM_CALLS, LLM_TOKENS, PROVIDER_RETRIES

log = get_logger(__name__)

#: These reject an explicit temperature and use max_completion_tokens instead of max_tokens.
_REASONING_PREFIXES = ("o1", "o3", "o4")


class Usage:
    def __init__(self) -> None:
        self._lock = Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def record(self, prompt: int, completion: int) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self.calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
            }


def is_reasoning(deployment: str) -> bool:
    name = (deployment or "").lower()
    return "gpt-5" in name or name.startswith(_REASONING_PREFIXES)


@lru_cache(maxsize=1)
def _client():
    if not settings.azure_openai_endpoint:
        raise NotConfigured("AZURE_OPENAI_ENDPOINT is not set.")
    from openai import AzureOpenAI

    if settings.azure_openai_key:
        return AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_key,
            api_version=settings.azure_openai_api_version,
        )
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_ad_token_provider=provider,
        api_version=settings.azure_openai_api_version,
    )


def chat(
    deployment: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    usage: Usage | None = None,
    response_format: dict | None = None,
    temperature: float = 0.0,
) -> str:
    if not deployment:
        raise NotConfigured("No deployment name configured for this stage.")

    kwargs: dict[str, Any] = {"model": deployment, "messages": messages}
    if is_reasoning(deployment):
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens
        kwargs["temperature"] = temperature
    if response_format:
        kwargs["response_format"] = response_format

    delay = settings.provider_backoff_seconds
    last: Exception | None = None

    for attempt in range(1, settings.max_provider_attempts + 1):
        try:
            response = _client().chat.completions.create(**kwargs)
            content = (response.choices[0].message.content or "").strip()
            if (used := getattr(response, "usage", None)) is not None:
                prompt = getattr(used, "prompt_tokens", 0) or 0
                completion = getattr(used, "completion_tokens", 0) or 0
                LLM_TOKENS.labels(provider="aoai", kind="prompt").inc(prompt)
                LLM_TOKENS.labels(provider="aoai", kind="completion").inc(completion)
                if usage:
                    usage.record(prompt, completion)
            LLM_CALLS.labels(provider="aoai", outcome="ok").inc()
            return content
        except Exception as exc:  # noqa: BLE001 - classified below, then retried or raised
            last = exc
            if not _retryable(exc) or attempt == settings.max_provider_attempts:
                LLM_CALLS.labels(provider="aoai", outcome="error").inc()
                break
            PROVIDER_RETRIES.labels(provider="aoai", reason=type(exc).__name__).inc()
            sleep_for = min(delay, settings.provider_backoff_max_seconds) * (0.5 + random.random())
            log.warning(
                "model call failed, retrying",
                extra={
                    "attempt": attempt,
                    "sleep_s": round(sleep_for, 2),
                    "err": type(exc).__name__,
                },
            )
            time.sleep(sleep_for)
            delay *= 2

    raise DependencyUnavailable(
        f"Azure OpenAI call failed after {settings.max_provider_attempts} attempts "
        f"({type(last).__name__})."
    ) from last


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(k in name for k in ("timeout", "connection", "ratelimit", "apierror"))
