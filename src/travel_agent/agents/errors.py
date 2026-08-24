from __future__ import annotations


class SpecialistError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SpecialistContextRejected(SpecialistError):
    pass


class SpecialistTimeout(SpecialistError):
    pass
