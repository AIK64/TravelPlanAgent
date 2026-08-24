from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CriticErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    INVALID_REQUEST = "invalid_request"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    REFUSAL = "refusal"
    INCOMPLETE = "incomplete"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"


@dataclass
class CriticProviderError(Exception):
    category: CriticErrorCategory
    code: str
    retryable: bool
    safe_message: str
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.safe_message)


@dataclass
class CriticUnavailableError(Exception):
    provider: str
    model: str
    category: CriticErrorCategory
    code: str
    retryable: bool
    safe_message: str
    thread_id: str
    attempt_count: int
    elapsed_ms: float

    def __post_init__(self) -> None:
        Exception.__init__(self, "The soft critic is unavailable.")

