# v0.5 违规驱动的局部自修复

v0.5 把规划阶段原有的整轮候选重建升级为显式、可解释、可评测的局部修复循环。Validator 仍是硬约束的唯一裁判；Critic 只分析结构化违规并确定影响范围，Repair Policy 用确定性代码产生安全动作。任何修复都必须重新经过同一个 Validator，不能通过放宽预算、步行上限、活动时长或删除 `must_visit` 来制造“成功”。

## 1. 版本目标与边界

本版本新增的核心 Agent 能力是：

1. 从候选的结构化违规中选择修复目标，而不是盲目重跑 Planner。
2. 将“分析、动作计划、局部应用、增量 Tool Use、验证、终止”拆成 Graph 中可见的职责。
3. 只改变受影响日期，并用日期 Hash 检查未受影响日期没有漂移。
4. 复用已有 RouteResult，只对新邻接关系调用路线工具。
5. 通过修复历史、日志事件和离线 Benchmark 证明循环行为。

本版本不包含 LLM Soft Critic、OR-Tools、真实步行路线、完成计划后的编辑审批、长期 Memory 或 MCP。这些能力按路线图进入 v0.6 及后续版本。

## 2. Graph 闭环

```text
build_search_plan
  → load_pois
  → resolve_poi_facts
  → prepare_candidate_drafts
  → load_routes
  → materialize_candidates
  → validate_candidates
      ├─ 有合法候选 → select_best → END
      ├─ 无合法候选且有修复预算
      │    → select_repair_target
      │    → analyze_violations
      │        ├─ 不可安全修复 → mark_infeasible → END
      │        └─ build_repair_plan
      │             ├─ 无动作/重复动作 → mark_infeasible → END
      │             └─ apply_local_repair
      │                  → collect_delta_routes
      │                      ├─ 有缺失路线 → load_delta_routes
      │                      └─ 无缺失路线 ─────────────┐
      │                                                 ↓
      │                                    materialize_candidates
      │                                      → validate_candidates
      └─ 预算耗尽或无进展 → mark_infeasible → END
```

Graph 没有把循环藏进大函数。`validate_candidates` 在局部修复后还负责比较修复前后的违规指纹和 error 数量，将本轮标记为 `resolved`、`improved` 或 `no_progress`。

## 3. 强类型 State 与领域对象

新增领域对象位于 `src/travel_agent/domain/repair_models.py`：

- `CriticReport`：候选 ID、违规指纹、error/warning 数、违规类型、影响日期/POI、是否可修复和终止原因。
- `RepairAction`：动作类型、来源违规、POI、来源/目标日期、原因和预期效果。
- `RepairPlan`：轮次、动作列表、受影响/保留日期、失效路线键、动作指纹。
- `RepairAttempt`：修复前后违规指纹和 error 数、结果、局部性、路线复用/加载数量、终止原因。

`TravelState` 增加：

```text
repair_target_candidate_id
critic_report
repair_plan
repair_history
pending_replan_round
preserved_day_hashes
repair_terminal_reason
delta_route_queries
reused_route_keys
last_route_loaded_count
last_route_reused_count
```

State 只保存标准化领域数据和必要摘要，不写入 Provider 原始响应。`iterations` 只有在增量路线加载和重新验证成功后才提交；如果 Tool 在中途失败，本轮不会被错误计为已完成修复。

## 4. 确定性修复策略

| 违规 | 安全动作 | 明确禁止 |
|---|---|---|
| `budget_exceeded` | 按已知费用、兴趣匹配和非必去条件移除可选活动 | 提高预算；移除必去项 |
| `walking_limit` | 在违规日期移除步行贡献较高的非必去活动 | 提高步行上限 |
| `activity_time_limit` | 在违规日期移除时长贡献较高的非必去活动 | 提高活动时长上限 |
| `missing_must_visit` | 将已有必去 POI 移到可用日期，或插入可执行日期 | 忽略必去约束 |
| 局部时间/营业冲突 | 移除冲突的可选活动；必去项尝试移动日期 | 修改营业事实或截断活动 |
| `empty_plan` | 从已有标准化 POI 中补入一个可执行地点 | 绕过 Validator |

以下情况直接终止，不生成不安全 RepairPlan：

- 必去 POI 的已知费用本身已经超过总预算；
- 必去地点没有对应的标准化 POI Facts；
- 当前违规没有安全动作；
- 违规指纹重复、RepairAction 指纹重复或修复后没有进展；
- `max_replan_rounds` 已耗尽。

## 5. Route Delta 与局部性守卫

局部修复不是“少写几个日志”的整轮重建。`planning/impact.py` 根据修复前后的日程邻接关系计算 Route Key：

1. 已存在且仍被新日程需要的 RouteResult 直接复用。
2. 受影响日期中不再需要的邻接关系失效。
3. 只把缺失的新邻接关系放入 `delta_route_queries`。
4. 新结果合并回 `route_results`，不让 Provider 原始响应进入 State。
5. 重新物化后比较 `preserved_day_hashes`；任何未受影响日期变化都会抛出错误。

路线 Provider 超时、限流、协议错误或不可用时仍抛出 `ToolUnavailableError`，由 API 返回 503。它不会被包装成“当前旅行约束不可行”。

## 6. 有界终止与失败语义

- `max_replan_rounds` 限制已成功验证的修复轮次，模型层限制为 0 到 5。
- LangGraph `recursion_limit` 随允许轮次计算，为所有显式节点留出足够但有限的步数。
- Tool Gateway 继续提供单次调用超时、有限重试、并发上限、缓存和安全错误分类。
- 每轮 RepairPlan 与违规都有稳定指纹，用于阻止重复动作和重复状态循环。
- Tool 失败发生在验证之前时，`pending_replan_round` 保留诊断上下文，但 `iterations` 不增加，`repair_history` 不伪造成功记录。

业务不可行和外部失败的代表性结果：

| 场景 | 结果 |
|---|---|
| 必去项本身超过预算 | `infeasible` + `hard_constraint_conflict:budget`，0 次修复 |
| 必去项缺少 POI Facts | `infeasible` + `missing_required_poi_facts` |
| 重复违规指纹 | `infeasible` + `repeated_violation_fingerprint` |
| 增量路线 Tool 失败 | 抛出 `ToolUnavailableError`，API 503，不改写为 `infeasible` |

## 7. 可观测轨迹

INFO 日志中的关键事件：

```text
repair.target.selected
critic.completed
repair.plan.created
repair.action.applied
repair.routes.invalidated
repair.routes.reused
repair.routes.loaded
repair.validation.delta
repair.terminated
```

所有事件包含 `thread_id`；轮次事件还包含 round、候选 ID、动作类型、影响日期数、路线复用/加载数量或终止原因。完整 `CriticReport`、`RepairPlan` 和 `RepairAttempt` 可通过同一线程的 LangGraph Checkpoint 检查。

## 8. Benchmark 与验收结果

数据集位于：

- `evals/repairs/base_trip.json`：固定杭州三日基线。
- `evals/repairs/cases.jsonl`：9 条 Patch Case。

覆盖场景包括：无需修复、两档预算修复、必去项预算硬冲突、必去项跨日移动、必去 Facts 缺失、禁用修复、活动时长超限和步行超限。

执行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_local_repair.py
```

脚本强制使用全 Mock Runtime，不读取当前 shell 中的地图或 LLM Provider 配置，不产生外部 API 调用和费用。2026-08-24 本机基线：

| 指标 | 结果 |
|---|---:|
| Case 数 | 9 |
| 执行失败 | 0 |
| 精确用例准确率 | 100% |
| 预期修复成功率 | 100% |
| completed 计划硬约束满足率 | 100% |
| 有界终止率 | 100% |
| Replanning Locality | 1.0 |
| Route Reuse Rate | 80.77% |

耗时受机器和运行环境影响，只输出观测值，不作为固定正确性门禁。

## 9. 测试与开发命令

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest tests\test_local_repair.py tests\test_repair_benchmark.py
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing --cov-fail-under=90
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
.\.venv\Scripts\python.exe -m pip check
```

关键测试不仅断言最终文本，还断言 Graph 节点、修复动作、Checkpoint 状态、未受影响日期 Hash、Route Tool 调用增量、硬冲突零轮次终止、重复指纹终止和 Tool 失败语义。

## 10. 面试演示建议

先运行预算 300 元用例：初始候选预算违规，Critic 定位候选和受影响日期，RepairPlan 移除一个高费用非必去活动，只加载一条新路线，保留两个日期并重新验证为 completed。再运行预算 10 元用例：必去项本身超过预算，Critic 直接给出硬冲突，Agent 不删除必去项也不浪费修复轮次。

这两个轨迹共同说明本版本的技术取舍：LLM 可以在后续版本负责软质量判断，但当前硬约束、自修复安全边界、增量影响分析和终止条件必须由可测试的确定性代码负责。
