from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlanningEvaluationOverrides:
    """只由离线 Evaluator 注入的规划图消融开关。

    生产 API 不从环境变量读取这些字段，尤其不能关闭 Hard Validator。
    """

    skip_validator: bool = False
    force_heuristic_optimizer: bool = False
    skip_soft_critic: bool = False
    full_replan: bool = False

