from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LifecycleErrorCode(StrEnum):
    NOT_FOUND = "plan_session_not_found"
    CONFLICT = "plan_session_conflict"
    STALE_INTERRUPT = "stale_interrupt"
    STALE_VERSION = "stale_version"
    STALE_REVISION = "stale_revision"
    LOCK_CONFLICT = "lock_conflict"
    INVALID_ACTION = "invalid_action"


@dataclass
class LifecycleNotFoundError(Exception):
    session_id: str

    def __post_init__(self) -> None:
        Exception.__init__(self, "The plan session was not found.")

    def safe_detail(self) -> dict[str, str]:
        return {
            "code": LifecycleErrorCode.NOT_FOUND.value,
            "session_id": self.session_id,
            "message": "没有找到计划生命周期会话",
        }


@dataclass
class LifecycleConflictError(Exception):
    session_id: str
    code: str = LifecycleErrorCode.CONFLICT.value
    message: str = "该计划操作已过期、已处理或与当前版本冲突"

    def __post_init__(self) -> None:
        Exception.__init__(self, "The plan session action conflicts with current state.")

    def safe_detail(self) -> dict[str, str]:
        return {"code": self.code, "session_id": self.session_id, "message": self.message}


@dataclass
class LifecycleActionError(Exception):
    session_id: str
    code: str
    message: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def safe_detail(self) -> dict[str, str]:
        return {"code": self.code, "session_id": self.session_id, "message": self.message}

