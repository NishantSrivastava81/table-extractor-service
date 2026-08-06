"""Typed errors that render as RFC 9457 problem details.

A client sees a stable machine-readable code and a safe message. Provider errors and stack
traces never cross this boundary.
"""

from __future__ import annotations

from typing import Any


class TxError(Exception):
    status: int = 500
    code: str = "INTERNAL"
    title: str = "Internal error"

    def __init__(self, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extra = extra

    def to_problem(self, instance: str, trace_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"about:blank#{self.code.lower().replace('_', '-')}",
            "title": self.title,
            "status": self.status,
            "code": self.code,
            "instance": instance,
        }
        if self.detail:
            body["detail"] = self.detail
        if trace_id:
            body["trace_id"] = trace_id
        body.update(self.extra)
        return body


class InvalidPageRange(TxError):
    status, code, title = 400, "INVALID_PAGE_RANGE", "Page range is not valid"


class JobNotFound(TxError):
    status, code, title = 404, "JOB_NOT_FOUND", "No such job"


class ResultNotReady(TxError):
    status, code, title = 409, "RESULT_NOT_READY", "Job has not finished"


class ResultExpired(TxError):
    status, code, title = 410, "RESULT_EXPIRED", "Result is no longer available"


class PayloadTooLarge(TxError):
    status, code, title = 413, "PAYLOAD_TOO_LARGE", "Upload exceeds the configured limit"


class UnsupportedMediaType(TxError):
    status, code, title = 415, "UNSUPPORTED_MEDIA_TYPE", "Uploaded bytes are not a PDF"


class EncryptedPdf(TxError):
    status, code, title = 422, "ENCRYPTED_PDF", "PDF is password protected"


class UnreadablePdf(TxError):
    status, code, title = 422, "UNREADABLE_PDF", "PDF could not be opened"


class DependencyUnavailable(TxError):
    status, code, title = 503, "DEPENDENCY_UNAVAILABLE", "An upstream service is unavailable"


class NotConfigured(TxError):
    status, code, title = 503, "NOT_CONFIGURED", "Required provider is not configured"
