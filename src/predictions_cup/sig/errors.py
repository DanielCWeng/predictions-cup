"""Typed, secret-safe SIG REST error model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    error: ErrorBody


class SigApiError(Exception):
    """Base for safe, typed failures returned by or while calling SIG REST."""

    def __init__(
        self,
        *,
        status_code: int | None,
        code: str | None,
        safe_message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.safe_message = safe_message
        self.details = details
        super().__init__(safe_message)

    def __str__(self) -> str:
        labels: list[str] = []
        if self.status_code is not None:
            labels.append(f"HTTP {self.status_code}")
        if self.code is not None:
            labels.append(self.code)
        prefix = f"SIG REST ({', '.join(labels)}): " if labels else "SIG REST: "
        return prefix + self.safe_message


class SigAuthenticationError(SigApiError):
    pass


class SigAuthorizationError(SigApiError):
    pass


class SigRateLimitError(SigApiError):
    pass


class SigClientRequestError(SigApiError):
    pass


class SigNotFoundError(SigApiError):
    pass


class SigConflictError(SigApiError):
    pass


class SigTemporaryServiceError(SigApiError):
    pass


class SigUnexpectedServerError(SigApiError):
    pass


class SigMalformedResponseError(SigApiError):
    pass


class SigTransportError(SigApiError):
    pass


def error_from_payload(*, status_code: int, payload: object) -> SigApiError:
    try:
        envelope = ErrorEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise SigMalformedResponseError(
            status_code=status_code,
            code=None,
            safe_message="SIG returned a malformed error response",
        ) from exc

    error = envelope.error

    def build(error_type: type[SigApiError]) -> SigApiError:
        return error_type(
            status_code=status_code,
            code=error.code,
            safe_message=error.message,
            details=error.details,
        )

    if status_code == 401:
        return build(SigAuthenticationError)
    if status_code == 403:
        return build(SigAuthorizationError)
    if status_code == 404:
        return build(SigNotFoundError)
    if status_code == 429:
        return build(SigRateLimitError)
    if status_code == 409:
        return build(SigConflictError)
    if status_code == 503 and error.code in {"TX_CONFLICT", "SERVICE_UNAVAILABLE"}:
        return build(SigTemporaryServiceError)
    if 400 <= status_code < 500:
        return build(SigClientRequestError)
    if status_code >= 500:
        return build(SigUnexpectedServerError)
    return build(SigApiError)
