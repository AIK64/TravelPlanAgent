from __future__ import annotations


class ApplicationError(RuntimeError):
    code = "application_error"
    retryable = False

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ApplicationNotFoundError(ApplicationError):
    code = "not_found"


class ApplicationForbiddenError(ApplicationError):
    code = "forbidden"


class ApplicationConflictError(ApplicationError):
    code = "conflict"
