# v0.6 约束优化与真实路线质量

v0.6 将 Planner 从“固定活动上限 + 最近邻排序”升级为 Graph 中显式可见的约束优化阶段。地图 Tool 提供标准化的驾车/步行事实，确定性代码负责预算、时间、必去项、活动强度和步行边界，OR-Tools CP-SAT 在明确预算内生成多风格候选；Validator 仍是最终硬约束裁判，v0.5 Critic/Repair 闭环继续承担求解降级或物化偏差后的恢复。

## 1. 版本目标与边界

本版本展示的 Agent Engineering 能力：

1. 把外部 Tool 事实和用户约束转换为强类型 `OptimizationProblem`。
2. 将路线矩阵、问题构建、求解、候选物化和验证拆成独立 Graph Node。
3. 在有界时间和搜索状态内生成 relaxed、balanced、exploration 三种候选，并保留目标分解。
4. 求解超时后显式记录降级，而不是静默切换或伪造成功。
5. 用固定数据集比较优化器/启发式、单候选/三候选、真实步行/估算消融。

本版本不使用 LLM 决定硬约束，也不做 OTA 库存、交易或全城市交通穷举。Grounded LLM Soft Critic 属于 v0.7。

## 2. Graph 与职责边界

```text
build_search_plan
  → load_pois
  → resolve_poi_facts
  → build_route_matrix              # 唯一外部路线 Tool Use
  → build_optimization_problem      # 标准化事实 → 优化领域对象
  → solve_candidate_variants        # 有界 CP-SAT 或显式降级
  → materialize_optimized_candidates
  → validate_candidates             # 最终硬约束裁判
      ├─ deliverable → select_best → END
      └─ invalid → v0.5 Critic → Local Repair → Delta Route → Revalidate
```

求解循环没有隐藏在 Provider 或巨大 Planner 函数中。OR-Tools 不访问 HTTP Client、API Key 或 Provider 原始 JSON，只消费 `OptimizationProblem`。

## 3. State 与领域对象

`TravelState` 新增：

```text
optimization_pois
optimization_problem
optimization_result
route_matrix_cache_hits
route_matrix_provider_calls
```

核心领域对象位于 `src/travel_agent/domain/optimization_models.py`：

- `OptimizationBudget`：`max_solve_ms`、`max_search_states`、`candidate_limit`、`variant_count`。
- `OptimizationPOI`：时长、费用、偏好价值、置信度、必去标记和可用日期。
- `RouteMatrixEntry`：起终点 ID、时长、距离、模式、Provider 和数据置信度。
- `OptimizationProblem`：日期、路线矩阵、总预算、每日活动/步行上限、目标权重和执行预算。
- `OptimizationSolution`：风格、逐日分配、目标值和 `ObjectiveBreakdown`。
- `OptimizationResult`：`optimal/feasible/degraded/infeasible`、求解器、耗时、搜索状态和降级原因。

State 不保存 Provider 原始响应；日志也只输出白名单标量摘要。

## 4. 路线矩阵与真实步行

默认 `PlanningPolicy.route_modes` 为 `driving + walking`：

1. 所有候选有向边获取驾车路线。
2. 只有直线距离不超过 `MAX_WALKING_LEG_METERS × 1.25` 的边才额外请求步行路线，控制调用规模。
3. 实际步行路线不超过单段阈值且未突破当日步行预算时选择步行，否则明确选择驾车。
4. 采用驾车的边对步行累计为 0，不再使用“驾车距离 × 固定比例”作为最终事实。

AMap 实现使用 v5 驾车 `/v5/direction/driving` 和步行 `/v5/direction/walking`。步行请求通过 `show_fields=cost` 获取时间字段；参数和响应依据[高德开放平台路径规划 API 2.0 文档](https://lbs.amap.com/api/webservice/guide/api/newroute)。

Gateway 缓存键由 Provider、`route:v1` 坐标版本、6 位起终点坐标、方向、模式和策略组成。驾车与步行不会互相覆盖；跨线程相同行程可复用矩阵，Checkpoint 记录命中数与 Provider 调用数。

`USE_REAL_WALKING_ROUTES=false` 仅用于消融实验。它恢复 v0.5 的驾车距离估算步行语义，会产生 `walking_distance_estimated` 告警，不应作为最终计划配置。

## 5. 优化模型与确定性边界

CP-SAT 当前建模：

- 必去 POI 必须且只分配一次，可选 POI 最多一次。
- POI 只能分配到营业与旅行时间窗可用的日期。
- 每种风格具有不同的单日活动数量上限。
- 每日活动时长、路线代理时长和可用分钟数受硬约束。
- 已知费用总和不得超过总预算。
- 真实步行路线的距离代理不得超过每日步行上限。
- 目标函数按风格组合偏好、类别多样性、路线时间和已知费用。

求解后使用标准化路线矩阵做确定性逐日排序和候选物化，再由同一个 Validator 检查实际顺序下的时间、费用、步行、活动时长和必去项。优化模型中的代理约束不能绕过 Validator。

## 6. 有界求解与失败语义

默认预算：

| 配置 | 默认值 |
|---|---:|
| `OPTIMIZATION_MAX_SOLVE_MS` | 800 ms |
| `OPTIMIZATION_MAX_SEARCH_STATES` | 20000 |
| `OPTIMIZATION_CANDIDATE_LIMIT` | 8 |
| `OPTIMIZATION_VARIANT_COUNT` | 3 |
| `MAX_WALKING_LEG_METERS` | 1500 m |

语义区分：

| 场景 | 行为 |
|---|---|
| CP-SAT 给出最优/可行解 | 写入 `OptimizationResult` 并物化候选 |
| 超出求解预算 | `degraded + optimizer_timeout`，回退确定性最近邻 |
| 求解无可行解 | `degraded + optimizer_infeasible`，由启发式 + Validator/Critic 给出最终语义 |
| 地图 Tool 超时/限流/协议错误 | 抛出 `ToolUnavailableError`，API 503，不标记为 `infeasible` |
| 候选实际物化后违反硬约束 | 进入 v0.5 Critic/Repair 有界闭环 |

## 7. Trace 与可观察性

关键 INFO/WARNING 事件：

```text
route_matrix.loaded
optimization.problem_built
optimization.started
optimization.completed
optimization.degraded
candidate.generated
candidate.validated
routing.decision
```

其中包含 `thread_id`、问题 ID、POI/路线数量、模式、缓存命中、Provider 调用数、求解预算、解数量、搜索状态、耗时和各风格目标值。完整目标分解保存在 Checkpoint 的 `optimization_result` 中。

## 8. Benchmark 与本机基线

数据集：`evals/optimization/cases.jsonl`，包含 baseline、预算、短时间窗和步行约束 4 类固定 Case。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_optimization.py
```

脚本使用 Mock POI/Route Provider，比较四个变体：

- `optimizer-three-real`：CP-SAT + 三候选 + 真实步行。
- `optimizer-one-real`：CP-SAT + 单候选 + 真实步行。
- `heuristic-three-real`：强制求解降级 + 真实步行。
- `heuristic-three-estimated`：强制求解降级 + 历史步行估算。

2026-08-24 本机观测：

| 变体 | 约束满足率 | 求解成功率 | 平均候选数 | 平均路线分钟 | Grounded 步行事实率 | 平均延迟 |
|---|---:|---:|---:|---:|---:|---:|
| optimizer-three-real | 100% | 100% | 3.0 | 95.25 | 100% | 62.80 ms |
| optimizer-one-real | 100% | 100% | 1.0 | 95.25 | 100% | 33.82 ms |
| heuristic-three-real | 100% | 0%（预期降级） | 2.0 | 86.75 | 100% | 36.40 ms |
| heuristic-three-estimated | 100% | 0%（预期降级） | 1.5 | 75.75 | 0% | 35.80 ms |

这些数值只描述当前小型 Fixture，不声称优化器已在路线分钟上优于启发式。当前证据能证明的是：优化器在全部 Case 内成功求解并生成稳定三风格候选，真实步行消除了估算事实，三候选相对单候选增加约 29 ms 本机延迟。后续应扩充城市、行程长度和拥挤路线样本后再判断质量提升。

同次验收全量测试为 270 项通过、2 项跳过，总覆盖率 90.68%，`planning/optimization.py` 覆盖率 95%。

## 9. 使用与验证

默认全 Mock 运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:TRAVEL_PROVIDER = "mock"
$env:USE_REAL_WALKING_ROUTES = "true"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

真实高德路线：

```powershell
$env:TRAVEL_PROVIDER = "amap"
$env:AMAP_API_KEY = "replace-with-your-own-key"
$env:MAX_WALKING_LEG_METERS = "1500"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_optimization.py tests\test_optimization_benchmark.py
.\.venv\Scripts\python.exe scripts\evaluate_optimization.py
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
.\.venv\Scripts\python.exe -m pip check
```

## 10. 面试演示建议

先展示正常轨迹：70 条混合路线查询进入标准化矩阵，CP-SAT 在预算内生成三种风格，Checkpoint 可展开目标分解，Validator 在 0 次修复下通过。再注入 `OptimizationTimeoutError`：Trace 出现 `optimization.degraded`，Agent 回退 v0.5 最近邻；预算 300 元用例随后触发 Critic 和一次局部修复并完成。

这组演示说明三个技术取舍：外部数据先标准化再参与决策；LLM/求解器不取代确定性硬约束裁判；“正常优化、显式降级、局部恢复、外部失败”是四种不同且可测试的执行语义。
