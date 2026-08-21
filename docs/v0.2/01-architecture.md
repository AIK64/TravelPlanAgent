# 01. Agent 架构：把事实获取放进 Graph

## 运行主线

```text
PlanningRequest + thread_id
  → build_search_plan（Search Intent）
  → load_pois（POI Tool Use）
  → resolve_poi_facts（事实补全与 provenance）
  → prepare_candidate_drafts（Planner phase 1）
  → load_routes（Route Tool Use）
  → materialize_candidates（Planner phase 2）
  → validate_candidates（确定性 Critic）
  ├─ select_best → END
  ├─ replan → prepare_candidate_drafts
  └─ mark_infeasible → END
```

这是一条显式的 `Plan → Tool Use → Validate → Replan` 有界 Loop。Planner 分两阶段：先只根据 POI 事实产出草案，再仅消费标准化 `RouteResult` 把草案物化为带交通指标的候选；因此路线调用和结果回写在轨迹中可见，而不是隐藏在一个大函数中。

## 模块职责与依赖方向

```text
api/app → runtime → graph/workflow → tools/gateway → providers
                    ↓                    ↓
              planning + domain      cache / retry
```

- `domain/`：Pydantic 的旅行、工具事实、校验结果；不依赖 Provider。
- `tools/protocols.py`：Provider 抽象；`providers/` 仅负责将外部或 Mock 数据归一化。
- `tools/gateway.py`：缓存、并发、重试和安全事件；不做业务可行性判断。
- `graph/workflow.py`：显式节点、边、State 与条件路由；只接受 Gateway 注入。
- `planning/`：检索意图、默认事实、候选物化和硬约束 Validator。
- `api/`：生成 `thread_id`、映射安全错误至 HTTP；不是规划决策层。

这个方向防止 Provider 原始 JSON 进入 Prompt 或 Graph State：State 中保存的是可序列化的标准模型和摘要，Provider 原始响应停留在适配层。
