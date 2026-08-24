# Travel Agent v0.9 → v1.2 迭代路线

> 状态：当前唯一权威版本路线  
> 当前基线：v1.2.0（实现候选；外部 Live/容器演练待运行者提供环境）
> 决策日期：2026-08-24

## 1. 路线决策

项目按三个完成层级推进：

1. `v1.0` 完成可用于简历和面试演示的核心 Travel Agent：约束规划、真实工具、确定性验证、Soft Critic、局部重规划、HITL、环境事件和量化评测形成闭环。
2. `v1.1` 增加 Preference Memory、上下文裁剪、跨会话个性化及 Memory 消融实验。
3. `v1.2` 增加 Travel MCP Server、备用 Provider、完整前端和生产化部署。

v1.0、v1.1 与 v1.2 主干代码均已落地：核心 Agent、Preference Memory、Context Projection、Specialist Handoff、MCP、Provider Chain、异步 Run、前端主界面和部署适配已经集成。真实 DeepSeek/地图/天气 Live Smoke 与 Docker 全栈演练仍必须在具备凭证和容器环境的机器上单独执行，不能由 Mock 结果代替。

多 Agent 不作为强制版本目标。只有当某项职责确实需要独立推理上下文、独立决策循环或独立运行预算，并且消融实验能证明它优于单 Graph/Subgraph，才允许升级为 Agent。地图查询、时间计算、约束验证、缓存和 Provider 适配器始终是工具或确定性服务，不为“多 Agent”标签进行包装。

## 2. v1.0 的完整 Agent 效果

v1.0 应能稳定演示以下轨迹：

```text
自然语言需求
  → DeepSeek / OpenAI 结构化抽取
  → Deterministic Requirement Validation
  → Interrupt / Resume 澄清
  → POI、详情、路线和天气 Tool Calling
  → Relaxed / Balanced / Exploration 多候选优化
  → Deterministic Hard Validator
  → Grounded LLM Soft Critic
  → Violation-driven Local Replan
  → 用户选择、锁定和编辑计划
  → Plan V1 / V2 与局部 Diff
  → 天气 ChangeEvent 触发 affected-days 重规划
  → Trace、Benchmark、Baseline 和消融结果
```

这里的“完整”指 Agent 行为闭环完整，不表示已经完成大规模商业系统、OTA 交易或所有地图供应商接入。

## 3. 版本总览

| 版本 | 唯一核心能力 | 关键 Graph 变化 | 主要验收证据 |
|---|---|---|---|
| v0.5（已完成） | 违规驱动的局部自修复 | `Validate → Critic → RepairPlan → LocalReplan → Revalidate` | Replanning Locality、路线复用率、轨迹测试 |
| v0.6（已完成） | 约束优化与真实路线质量 | `BuildProblem → Optimize → Materialize → Validate` | 路线效率、求解终止、优化器消融 |
| v0.7（已完成） | Grounded LLM Soft Critic | `HardValidate → SoftCritic → EvidenceGuard → QualityGate` | Grounding 准确率、软约束评测、Provider 降级轨迹 |
| v0.8（已完成） | 计划生命周期 HITL | `Select/Change → Interrupt → Impact → LocalReplan → Version` | 锁定项不变、V1/V2 Diff、重启恢复 |
| v0.9（已完成） | 天气事件驱动重规划 | `WeatherEvent → ImpactAnalyzer → LocalReplan` | 事件幂等性、affected-days 保持率、故障语义 |
| v1.0（已完成） | 评测驱动的核心 Agent 发布 | 统一 ExecutionBudget、Trace 和回归门禁 | 120 次发布工作流、180 次消融、故障注入 |
| v1.1（已完成） | 长期 Preference Memory | `Retrieve → ComposeContext → Plan → Propose → Confirm → Persist` | 跨会话测试、上下文预算、Memory 消融 |
| v1.2（实现候选） | MCP 与平台化 | MCP/API 共用 Application Service；生产存储和部署 | MCP Contract、Failover、安全测试已通过；外部部署演练待执行 |

版本必须按依赖顺序推进。允许在当前版本内提前建立下一版本所需的 Protocol，但不把下一版本能力混入当前验收指标。

## 4. v0.5：违规驱动的局部自修复

### 4.1 Agent 能力

已把固定的“降低密度、优先低成本”全量候选重生成，升级为读取结构化违规证据、生成 RepairPlan、只修改受影响日期并重新验证的有界循环。实现细节与验收证据见 [v0.5 局部自修复文档](v0.5/README.md)。

### 4.2 State、Node、Edge

新增最小强类型对象：

- `CriticReport`：目标候选、违规指纹、严重度、affected days/POIs、可修复性。
- `RepairAction`：动作类型、目标实体、来源违规、预期效果。
- `RepairPlan`：本轮动作、受影响日期、保留日期、失效路线键。
- `RepairAttempt`：修复前后违规指纹、动作指纹、结果。

新增节点：

```text
validate_candidates
  → select_repair_target
  → analyze_violations
  → build_repair_plan
  → apply_local_repair
  → collect_delta_routes
  → [load_delta_routes，仅有缺失路线时]
  → materialize_candidates
  → validate_candidates
```

验证节点记录 `resolved`、`improved` 或 `no_progress`；条件路由区分可继续修复、无安全动作、重复违规/动作、轮次耗尽和硬冲突。外部 Tool 失败抛出 `ToolUnavailableError`，不进入业务 `infeasible`。

### 4.3 Tool Use

- 保留仍然有效的 POI Facts 和 RouteResult。
- 只失效 affected day 中发生邻接变化的路线键。
- Tool/Provider 失败保持外部失败语义，不能伪装成 `infeasible`。
- 不自动提高预算、步行上限或活动时间上限，不删除 `must_visit`。

### 4.4 Trace 与验收

关键事件：

```text
critic.completed
repair.target.selected
repair.plan.created
repair.action.applied
repair.routes.invalidated
repair.routes.reused
repair.routes.loaded
repair.validation.delta
repair.terminated
```

完成标准：

- 返回的 completed 计划硬约束满足率 100%：已通过 9 条离线 Fixture。
- Replanning Locality 为 1.0，未受影响日期保持率 100%。
- 修复成功率与有界终止率均为 100%；修复轮次上限、Graph recursion limit、Tool 超时和有限重试共同限制执行。
- 路线复用率为 80.77%；只查询局部修复后缺失的邻接路线。
- 全量测试 260 项通过、2 项跳过；总覆盖率 90.54%，Graph 工作流覆盖率 94%（2026-08-24 本机 Mock 基线）。

### 4.5 面试演示

展示“Day 2 步行超限 → Critic 定位影响范围 → 移除低价值跨区域活动 → 复用仍有效路线 → 未受影响日期 Hash 不变 → 再验证通过”；再用“必去项本身超过预算”展示硬冲突不会消耗修复轮次。

## 5. v0.6：约束优化与真实路线质量

### 5.1 Agent 能力

让 Planner 不只依赖最近邻和固定活动上限，而是将地图事实、时间窗、路线成本和用户约束转换为可解释的优化问题，生成质量有差异的多候选。

### 5.2 State、Node、Edge

新增：

- `OptimizationProblem`：候选 POI、时间窗、路线矩阵、硬约束、目标权重。
- `OptimizationResult`：求解状态、选中顺序、目标分解、耗时和降级原因。
- `OptimizationBudget`：最大求解时间、候选数量和搜索上限。

Graph：

```text
build_route_matrix
  → build_optimization_problem
  → solve_candidate_variants
  → materialize_optimized_candidates
  → validate_candidates
```

### 5.3 Tool Use

- 增加真实步行路线模式，不能继续用驾车路线按比例估算步行距离作为最终结果。
- 路线矩阵通过 Tool Gateway 获取，缓存键包含 Provider、模式、策略和坐标版本。
- OR-Tools 只消费标准化领域数据，不读取 Provider 原始响应。
- 求解超时允许回退到确定性启发式，但必须在结果和 Trace 中标记降级。

### 5.4 Trace 与验收

记录 `optimization.problem_built`、`optimization.started/completed/degraded`、目标分数分解、求解耗时和路线矩阵缓存命中率。

Benchmark 至少比较：

- 优化器 vs 当前最近邻启发式；
- 单候选 vs 三种风格候选；
- 真实步行路线 vs 估算路线。

完成标准是在固定数据集上报告路线效率、约束满足率、求解成功率和延迟，不预设无法由数据证明的提升幅度。

### 5.5 实现与验收结果

- 已落地强类型 `OptimizationProblem/Result/Budget` 和四个显式 Graph Node；Validator 与 v0.5 Repair Loop 保持最终硬约束与恢复职责。
- 已接入 AMap/Mock 驾车与真实步行 Tool，矩阵缓存区分 Provider、坐标版本、模式和策略；默认基线 8 个 POI 形成 70 条混合有向查询。
- OR-Tools CP-SAT 默认在 800 ms、20000 搜索预算和 8 个候选 POI 内求解三种风格；超时或无解显式降级到 v0.5 确定性启发式。
- 4 条 Optimization Fixture 上，优化器三候选与单候选的求解成功率、completed 计划约束满足率均为 100%；真实步行 Grounded Fact Rate 为 100%。
- 本机三候选平均延迟 62.80 ms，单候选 33.82 ms；启发式路线分钟在此小数据集上更低，因此不声明优化器已经提升路线效率，后续扩充数据再验证。
- 全量测试 270 项通过、2 项跳过；总覆盖率 90.68%，优化模块覆盖率 95%（2026-08-24 本机 Mock 基线）。
- 详细 Graph、配置、失败语义和消融结果见 `docs/v0.6/README.md`。

## 6. v0.7：Grounded LLM Soft Critic

已按 [v0.7 实现文档](v0.7/README.md) 完成 Evidence Digest、Provider Gateway、Grounding Gate、确定性质量门、一次局部软修复、失败降级和离线消融基线。

### 6.1 Agent 能力

使用 DeepSeek/OpenAI 评价节奏合理性、兴趣覆盖、内容多样性、休息安排和区域连贯性。LLM 只进行软判断，硬约束仍由确定性 Validator 决定。

### 6.2 State、Node、Edge

新增：

- `CandidateEvidenceDigest`：裁剪后的候选事实和指标引用。
- `SoftCritique`：评价维度、分数、证据 ID、问题和建议动作。
- `GroundedExplanation`：只基于已验证事实生成的推荐说明。

Graph：

```text
hard_validate
  → prepare_critic_context
  → soft_constraint_critic
  → validate_critic_evidence
  → quality_gate
      ├─ acceptable → explain_selection
      └─ repairable → build_repair_plan
```

### 6.3 LLM 与失败边界

- DeepSeek/OpenAI 通过现有 Provider Protocol 接入，不在 Graph 中写供应商分支。
- Prompt 只接收裁剪后的标准化摘要，不接收地图原始响应。
- Critic 输出必须满足 Schema，且所有事实引用必须能在 EvidenceDigest 中解析。
- Soft Critic 不可用时，硬约束合法计划可以降级交付，但必须返回 `critic_status=unavailable`；不能把模型故障记为业务不可行。

### 6.4 Trace 与验收

记录模型、Prompt version、输入摘要大小、证据覆盖率、重试、延迟和估算成本，不记录完整用户原文与模型原始响应。

评测包括人工标注软约束集、Grounding 准确率、Schema 成功率，以及 `with_critic` / `without_critic` 消融。

## 7. v0.8：计划生命周期与 HITL

详细实现规格见 [v0.8 计划生命周期 HITL 设计报告](v0.8/design.md)。

### 7.1 Agent 能力

在计划生成后支持用户选择候选、锁定项目、提交自然语言修改，并通过 Interrupt/Resume 审批影响范围和生成新版本。

### 7.2 State、Node、Edge

已实现：

- `PlanVersion`、`PlanPreview`、`PlanLock`、`EditPatch`、`ImpactResult`、`PlanDiff`。
- 当前版本号、父版本、锁定项、待审批变更和幂等 request ID。

Graph：

```text
await_user_action
  → dispatch_action
  → select_candidate / change_lock / parse_edit
  → analyze_change_impact
  → build_local_preview
  → await_user_action (Preview Approval Interrupt)
  → resolve_approval
  → commit V2 / reject Preview
```

### 7.3 持久化与边界

- Repository 和 Checkpointer 使用 Protocol；本地模式可继续使用 SQLite。
- v1.2 再完成 PostgreSQL、多实例锁、租户和备份等生产化能力。
- 锁定项不可被自动修复修改；确需修改时必须产生明确冲突并再次请求用户确认。
- 同一变更 request ID 必须幂等，旧 Interrupt 和并发 Resume 保持 409 语义。

### 7.4 Trace 与验收

已完成服务重启恢复、候选选择、日期/项目锁、自然语言与结构化编辑、编辑澄清、局部 Route Delta、Preview 审批、V2 CAS 提交、拒绝不改 V1、旧 Interrupt 冲突和 request ID 幂等。PlanDiff 可说明新增、删除、移动、重排、时间、路线与日期指标变化。

2026-08-24 本机全 Mock 验收共收集 372 项测试，370 项通过、2 项 Live Smoke 跳过，总覆盖率 90.03%。15 条生命周期 Fixture 的 Intent、Grounding、Impact、锁定保持、未影响日期保持、Diff、Commit、幂等和有界终止指标均为 100%，硬约束回归为 0%，标注路线复用率为 36.84%。Fixture 结果用于确定性回归，不作为真实 DeepSeek 线上准确率声明。

## 8. v0.9：天气事件驱动重规划

使用方法和实际结果见 [v0.9 天气事件驱动局部重规划文档](v0.9/README.md)，完整实现规格见 [设计报告](v0.9/design.md)。

### 8.1 Agent 能力

接入标准化天气工具，把外部环境变化转换为 `ChangeEvent`，通过影响分析复用 v0.5/v0.8 的局部重规划能力。

### 8.2 State、Node、Edge

新增 `WeatherSnapshot`、`DailyWeatherRisk`、`ChangeEvent`、`WeatherImpactResult` 和稳定的 Snapshot/Event Fingerprint。

```text
refresh_weather
  → resolve_weather_location
  → fetch_weather_snapshot
  → classify_weather_risks
  → derive_and_deduplicate_event
  → analyze_weather_impact
  → build_weather_repair_plan
  → local_replan_and_route_delta
  → hard_validate_and_locality_guard
  → persist_preview
  → HITL approve/reject
  → commit Plan V(n+1)
```

### 8.3 Tool Use 与边界

- Provider 原始天气响应在 Adapter 内标准化，State 只保存必要摘要、来源和获取时间。
- 天气不可用不能伪装成“天气良好”，应保留旧事实并标注 stale/unavailable。
- 只允许天气事件修改受影响的户外活动和相邻路线；用户锁定项仍需 HITL。
- 事件使用稳定 Fingerprint 去重，避免重复生成计划版本。
- v0.9 只提供显式刷新 API；定时调度、推送、备用天气 Provider 和生产事件总线延后。

### 8.4 Trace 与验收

Benchmark 至少包含 30 条 Fixture，覆盖降雨、温度、强风、雪冰、天气恢复、重复事件、锁、Provider 超时、未知/未覆盖日期和无可替换室内活动。锁定项与未影响日期保持率、已提交计划硬约束满足率和有界终止率必须为 100%；重复 Event 创建新版本数和错误重规划数必须为 0。

### 8.5 实现与验收结果

已完成 Mock/AMap Weather Provider、可靠 Gateway、版本化风险策略、Snapshot/Event Fingerprint、三层幂等、天气 Impact/Repair、HITL Attention、Preview 审批、查询 API 和 SQLite 重启恢复。Provider Failure 保留 Active Version，并与业务不可行、数据未覆盖和无计划影响保持不同终止语义。

2026-08-24 本机全 Mock 验收全量测试通过，2 项 Live Smoke 跳过，总覆盖率 90.98%。30 条 `weather-fixture-v1` 的 Risk Accuracy、Event F1、Event Deduplication、Impact Exact Match、锁定对象保持、未影响日期保持、Preview/Commit 正确率、失败分类和有界终止均为 100%，硬约束回归率和错误重规划率为 0%，标注路线复用率为 66.41%。固定 Fixture 用于离线代码与策略回归，不代表真实高德预报准确率或线上 Agent 成功率。

## 9. v1.0：评测驱动的核心 Agent 发布

### 9.1 Agent 能力

v1.0 不再横向扩充业务功能，而是把已有循环纳入统一执行预算、轨迹评测和回归门禁，使系统行为可以复现、解释和量化。

详细的 Run 边界、Budget/Trace Schema、Graph 接入点、统一 Benchmark、故障注入、发布门禁和分阶段实施计划见 [v1.0 设计报告](v1.0/design.md)；使用方式与实际结果见 [v1.0 实现文档](v1.0/README.md)。

### 9.2 运行治理

统一 `ExecutionBudget`：

- 最大 Graph 步数；
- 最大 LLM 调用数和 Token/成本预算；
- 最大 Tool 调用数；
- 最大修复和 HITL 轮次；
- 整体 Deadline；
- 重复 State/Action 指纹终止。

统一 Trace 关联 `thread_id`、`run_id`、`plan_version`、Node、Tool call、Provider、缓存、重试、降级和终止原因。

### 9.3 评测矩阵

至少包含：

- 100+ 可版本化 Benchmark；
- 直接 LLM 生成计划 Baseline；
- 去掉 Validator、优化器、Soft Critic、局部重规划和缓存的消融；
- 轨迹测试：工具选择、参数、调用次数、路由、Interrupt 位置和终止条件；
- Tool/LLM 超时、限流、错误响应和 Checkpoint 故障注入；
- 约束满足率、路线效率、Replanning Locality、延迟、Token 和成本报告。

### 9.4 v1.0 发布门禁

- completed 计划的已知硬约束满足率 100%。
- 所有循环在预算内终止。
- Tool/Provider 故障不会被错误分类为业务 `infeasible`。
- 关键节点、工具调用和修复决策具有可关联 Trace。
- Benchmark、命令、数据集版本和报告可以在全 Mock 模式重复运行。
- README 可以用真实报告展示完整系统与 Baseline/消融差异。

实施结果：120 次 Mock 发布工作流和 180 次隔离消融工作流门禁均已通过；全量测试 `462 passed, 2 skipped`，Branch Coverage `90.19%`。Mock Token 与费用保持 unknown，结果不作为真实 LLM 或地图线上质量声明。

## 10. v1.1：Preference Memory 与上下文管理

### 10.1 Agent 能力

从单次旅行上下文扩展到受用户控制的跨会话偏好学习。Memory 的目标是提高个性化和减少重复澄清，不是保存全部聊天记录。

### 10.2 Memory 模型与优先级

`PreferenceMemory` 至少包含：用户命名空间、类别、结构化值、来源、置信度、确认状态、创建时间、过期时间和撤销状态。

上下文优先级固定为：

```text
当前请求的显式约束
  > 当前计划中用户确认的选择
  > 已确认的长期 Preference Memory
  > 系统默认值
```

未经确认的模型推断不能作为硬约束，也不能静默写入长期 Memory。

### 10.3 State、Node、Edge

```text
identify_memory_namespace
  → retrieve_relevant_preferences
  → compose_bounded_context
  → planning_workflow
  → propose_memory_updates
  → await_memory_confirmation
  → persist_or_discard_updates
```

State 只保存本轮检索到的 Memory ID、结构化摘要和使用原因；大型历史、Embedding 和原始会话保存在 State 外。

### 10.4 上下文裁剪

- 按显式相关性、确认状态、置信度、新鲜度和 Token 预算排序。
- 对每个 Memory 记录为什么进入上下文和影响了哪项决策。
- 冲突 Memory 不自动合并；当前请求覆盖历史，并生成可审计的冲突记录。
- 提供查看、修正、删除、清空和禁用个性化的能力。

### 10.5 Memory 评测

必须包含跨会话 Fixture 和以下消融：

```text
with_memory vs without_memory
bounded_context vs full_history
confirmed_memory_only vs inferred_memory
```

指标包括偏好命中率、错误个性化率、重复澄清减少量、上下文 Token、延迟、遗忘/覆盖正确率和跨用户隔离。

## 11. v1.2：MCP、备用 Provider、完整前端和生产化部署

### 11.1 Travel MCP Server

MCP Server 是 Application Service 的适配层，不复制 Graph 或领域逻辑。API、MCP 和后台任务必须调用同一组用例服务。

建议工具：

```text
create_travel_plan
resume_travel_run
select_plan_candidate
apply_plan_change
approve_plan_change
get_plan_diff
replay_execution_trace
get_or_update_preferences
```

建议资源：

```text
travel://plans/{plan_id}
travel://plans/{plan_id}/versions/{version}
travel://plans/{plan_id}/diff
travel://runs/{thread_id}/trace
travel://users/{user_id}/preferences
```

MCP 工具具有明确输入输出 Schema、权限、错误码、幂等规则、超时和 Contract Test。内部 Graph 不为“使用 MCP”而绕行本地网络调用。

### 11.2 备用 Provider

- 地图和天气分别保持 Provider Protocol，增加至少一个可替换或备用实现。
- Failover 只针对可恢复的外部故障，业务空结果不能无条件切换并伪造成成功。
- 所有 Provider 响应标准化为统一领域模型，State 和上层 Graph 不感知供应商字段。
- Trace 记录首选/备用 Provider、切换原因、结果来源、新鲜度、延迟和成本。

### 11.3 完整前端

前端只围绕 Agent 可观察性和交互闭环建设：

- 自然语言输入与结构化约束确认；
- Interrupt/Resume 澄清和审批；
- Graph/Tool 执行时间线；
- 多候选对比和地图日程；
- 锁定、编辑、Plan Diff 和版本回退；
- 天气事件与局部变化高亮；
- Preference Memory 查看、修正和删除；
- 错误、降级和数据来源展示。

不在本版本扩展 OTA 下单、运营后台或与 Agent 展示无关的复杂页面。

### 11.4 生产化部署

- PostgreSQL Checkpointer、Plan/Version/Memory Repository；
- 多实例安全的锁、幂等和线程所有权校验；
- Redis 缓存、限流和短期协调；
- 身份认证、租户隔离、Secret 管理、加密、TTL、删除和备份；
- OpenTelemetry Trace/Metrics/Logs 与告警；
- 容器化、迁移脚本、健康检查、灰度和回滚说明；
- API、MCP、前端和后台事件处理器的端到端测试。

### 11.5 v1.2 完成标准

- API 与 MCP 对相同用例产生一致领域结果和错误语义。
- 首选 Provider 故障时按策略切换，Trace 可解释且不泄露 Secret。
- 多实例下 Resume、版本变更和 Memory 写入保持幂等及用户隔离。
- 完整前端可以演示 v1.0 的主轨迹和 v1.1 的 Memory 管理。
- 生产环境部署、迁移、备份恢复和回滚流程经过演练。

## 12. 多 Agent 决策门槛

在 v1.2 及之前，Planner、Critic 和 Replanner 优先实现为显式 Node 或 Subgraph。只有同时满足以下条件才创建独立 Agent：

1. 职责需要独立上下文或独立推理循环。
2. 输入输出可以收敛为强类型契约，而不是自由文本聊天。
3. 有独立的调用、时间、Token 和循环预算。
4. 故障不会污染其他 Agent 的 State。
5. `multi_agent` 对比 `single_graph` 的消融实验能证明质量、可靠性或上下文成本收益。

若未来满足条件，应新增独立版本或 ADR，不静默塞入现有版本。多 Agent 不是 v1.0、v1.1 或 v1.2 的发布门禁。

## 13. 所有版本共同守则

- 每个 Loop 都有迭代、调用、Token/成本或 Deadline 预算。
- LLM 负责语义理解、软评价和解释；确定性代码负责时间、预算、路线计算、硬约束和安全边界。
- Tool 输入输出有 Schema；Tool 失败、数据不足和业务不可行使用不同状态。
- Provider 原始响应不进入 Graph State 或 Prompt，只保存标准化结果、ID 和必要摘要。
- State 强类型且最小化；大型原始数据、Trace、历史版本和 Memory 正文保存在 State 外。
- 关键行为首先写轨迹测试，再测试最终文本；每个版本都要有 Benchmark 或可量化回归指标。
- 日志覆盖关键方法、Node、Edge、Tool、重试、缓存、修复、Interrupt、Resume 和终止原因，同时保护 API Key 与用户隐私。
- README、版本文档、设计规格、实施计划、学习指南和面试材料默认使用中文。

## 14. 单版本 Definition of Done

每个版本只有同时满足以下条件才可结束：

1. 文档明确新增了什么 Agent 能力及本版本非目标。
2. State、Node、Edge、Conditional Routing 和 Loop 在代码与文档中可见。
3. Tool Schema、失败语义、重试/缓存策略和 State 回写路径明确。
4. 关键轨迹可通过结构化日志或 Trace 回放。
5. 单元、Contract、轨迹、集成和 Benchmark 测试达到该版本门禁。
6. 全 Mock 模式可离线复现；真实 Provider 结果按供应商、模型、数据集版本和日期记录。
7. 面试演示能够解释技术取舍、失败恢复和量化效果。
8. README、版本文档、配置示例和运行命令与实现保持一致。

## 15. 当前下一步

当前开发按“一次连续实现、两个内部 Gate、最终发布 v1.2.0”推进。冻结后的完整设计见 [v1.1 → v1.2 最终开发入口](v1.1-v1.2/README.md)、[统一设计报告](v1.1-v1.2/design.md) 和 [需求追踪矩阵](v1.1-v1.2/requirements-traceability.md)。

v1.1 Gate 完成 Preference Memory、上下文裁剪、跨会话个性化，以及用于上下文隔离的进程内 Planner/Critic/Replanner Specialist Subagent 实验。Orchestrator 保持唯一状态归并和终止权，Specialist 只通过强类型 Handoff 返回结构化结果；single_graph 始终保留为 Baseline，Subagent 只有通过消融门禁才成为生产默认。

v1.2 Gate 完成共享 Application Service、Travel MCP Server、真实备用地图/天气 Provider、完整 Agent 交互前端、PostgreSQL/Redis/Worker/OpenTelemetry 和生产部署演练。API、MCP 和后台任务不得复制 Graph 或领域逻辑。

v1.0 已完成已有 Requirement、Planning、Repair、Critic、Lifecycle 和 Weather 循环的 ExecutionBudget、Trace Schema、故障注入、综合 Benchmark、Baseline 与发布门禁。

v0.9 的稳定 `ChangeEvent`、risk/event fingerprint、affected-days、终止原因和天气 Tool 轨迹已经纳入 v1.0 统一 Run，天气循环未被重写。

```text
Unified Agent Run
  → Shared ExecutionBudget
  → Node / Tool / Decision Trace
  → Fault Injection + 100+ Benchmark
  → Baseline / Ablation Comparison
  → Regression Gate + Interview Evidence
```

连续开发不得削弱 v1.0 已有的用户锁、Hard Validator、Grounding Gate、统一预算、Trace、局部性守卫或版本提交语义。MCP 与完整前端仍属于 v1.2 Gate，不提前复制业务逻辑。
