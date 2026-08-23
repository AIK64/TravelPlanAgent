# v0.3 自然语言需求解析与路由

v0.3 新增的是可观察、可替换、可评测的 Requirement Intake，而不是一个藏在 API 函数里的“调用一次 LLM”。它把自然语言转换为最小强类型 State，经确定性校验和地点工具解析后，才进入 v0.2 的规划循环。

```text
parse_requirement
  → validate_requirement
    ├─ blocking issues → request_clarification → END
    └─ valid → resolve_anchors → evaluate_anchors
         ├─ not found / ambiguous → request_clarification → END
         └─ resolved → assemble_trip_spec → execute_planning → END
```

推荐阅读顺序：

1. [Requirement State 与 Graph](01-requirement-state-and-graph.md)
2. [Structured Output 与失败语义](02-structured-output-and-failure-semantics.md)
3. [地点锚点解析](03-anchor-resolution.md)
4. [评测、运行与演示](04-evaluation-and-demo.md)

实现入口：

- `src/travel_agent/requirements/workflow.py`：显式 Graph、路由与 Checkpoint。
- `src/travel_agent/requirements/models.py`：Draft、Issue、自然语言 API 契约。
- `src/travel_agent/requirements/validation.py`：确定性硬约束与 `TripSpec` 组装。
- `src/travel_agent/requirements/gateway.py`：模型调用可靠性边界。
- `src/travel_agent/requirements/providers/deepseek.py`：DeepSeek JSON Output 适配器。
- `src/travel_agent/requirements/anchors.py`：地点检索意图与唯一匹配。
- `src/travel_agent/requirements/evaluation.py`：离线 Benchmark 指标。

版本边界：当前澄清只返回问题，没有把补充答案写回旧线程的 API；Checkpoint 是进程内 `InMemorySaver`，不构成长期 Memory；Requirement Graph 在 `execute_planning` 节点调用已有 compiled planning workflow，并非多 Agent 架构。
