from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecutionBudgetExceeded(Exception):
    run_id: str
    limit: str
    used: int | float
    maximum: int | float
    deadline: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, f"execution budget exceeded: {self.limit}")

    def safe_detail(self) -> dict[str, str | bool]:
        return {
            "code": (
                "deadline_exceeded" if self.deadline else "execution_budget_exhausted"
            ),
            "run_id": self.run_id,
            "limit": self.limit,
            "retryable": self.deadline,
            "message": "Agent 执行已达到安全预算上限",
        }


@dataclass
class RunNotFoundError(Exception):
    run_id: str

    def __post_init__(self) -> None:
        Exception.__init__(self, "The agent run was not found.")

    def safe_detail(self) -> dict[str, str]:
        return {
            "code": "agent_run_not_found",
            "run_id": self.run_id,
            "message": "没有找到 Agent Run",
        }
