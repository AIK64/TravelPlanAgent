from __future__ import annotations


class MemoryErrorBase(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def safe_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


class MemoryNotFoundError(MemoryErrorBase):
    def __init__(self, identifier: str) -> None:
        super().__init__("memory_not_found", f"memory resource not found: {identifier}")


class MemoryConflictError(MemoryErrorBase):
    def __init__(self, code: str = "memory_conflict") -> None:
        super().__init__(code, "memory state changed; reload and retry")


class MemoryPolicyError(MemoryErrorBase):
    pass


class MemoryForbiddenError(MemoryErrorBase):
    def __init__(self) -> None:
        super().__init__("memory_forbidden", "memory resource is not owned by caller")
