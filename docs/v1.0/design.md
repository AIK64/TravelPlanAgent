# v1.0 评测驱动的核心 Agent 发布设计报告

> 状态：已实现并通过 Mock 发布门禁  
> 基线版本：v0.9.0  
> 目标版本：v1.0.0  
> 设计日期：2026-08-24  
> 核心能力：`AgentRun → ExecutionBudget → Observable Trace → Executable Benchmark → Release Gate`

## 1. 设计结论

v1.0 不再新增旅行领域功能，而是把 v0.9 已有的 Requirement、Planning、Hard Validation、Soft Critic、Local Repair、Lifecycle HITL 和 Weather Replanning 纳入一套统一、可证明的 Agent 运行治理体系。

本版本只强化一项核心 Agent 能力：**每次 Agent 执行都有明确边界、共享预算、完整因果轨迹和可重复评测证据；系统能证明它为何调用工具、为何改变路线、为何中断或终止，并用发布门禁阻止行为回归。**

```text
API / Benchmark Command
  → RunCoordinator 创建 AgentRun + ExecutionBudget
  → Requirement / Planning / Lifecycle Graph
      → 显式 Budget Guard
      → Instrumented Node / Conditional Route
      → Observed LLM / Tool Gateway
      → Hard Validator / Critic / Replan / Interrupt
  → RunCoordinator 归并 Usage + TerminalReason
  → RunRepository + append-only TraceEvent
  → Trajectory Assertions + Metrics + Release Gate
```

关键取舍：

1. `run_id` 不复用 `thread_id` 或 `session_id`。一次请求、恢复或天气刷新是一个有界 Run，多个 Run 通过会话和因果字段关联。
2. 统一预算既在 Graph 的关键循环边界显式检查，也在 Tool/LLM Gateway 调用前原子扣减，不能只靠日志事后统计。
3. Trace 是独立的强类型、追加式运行证据，不把完整事件列表塞进 Graph State，也不把普通文本日志当唯一事实源。
4. Hard Validator 和最终状态收敛拥有预留预算；预算不足时不得返回未经验证的 `completed` 计划。
5. Tool/Provider 故障、执行预算耗尽和业务不可行是三种独立终止语义。
6. 发布指标只使用实际执行工作流后计算的结果；人工标注布尔值只能作为 Contract Fixture，不能冒充端到端准确率。
7. v1.0 不引入多 Agent、长期 Memory、MCP、备用 Provider、完整前端或生产级多实例基础设施。

## 2. 当前基线与核心问题

### 2.1 v0.9 已有能力

当前代码已经具备：

- `RequirementState` 的抽取、确定性校验、澄清 Interrupt/Resume 和锚点 Tool Use；
- `TravelState` 的路线矩阵、约束优化、Hard Validator、违规修复和 Grounded Soft Critic；
- `PlanLifecycleState` 的候选选择、锁、编辑、Preview、审批、版本 CAS 和天气重规划；
- POI、路线、天气、Requirement LLM、Edit LLM 和 Critic LLM 的独立 Gateway；
- Provider、缓存、重试、调用耗时、Token 等局部执行摘要；
- LangGraph Checkpoint，以及内存/SQLite Plan Repository；
- Requirement、Clarification、Repair、Optimization、Soft Critic、Lifecycle 和 Weather 七套离线评测。

### 2.2 v0.9 尚未统一的部分

这些能力目前仍是分散的：

1. 不同 Graph 和 Gateway 各自记录日志，但没有统一 `run_id`、事件 Schema 和跨子流程因果链。
2. Tool、LLM、修复、优化和天气分别有局部限制，没有覆盖整个执行段的总步数、总调用数和 Deadline。
3. API 返回终态，但无法通过稳定接口查询“走过哪些节点、调用了什么、为什么路由到这里”。
4. 现有评测的数据格式、执行深度和指标口径不一致，部分 Fixture 只计算预先标注的布尔字段。
5. 没有统一故障注入点，难以系统证明超时、限流、错误响应和存储故障不会破坏失败语义。
6. 没有一条可在全 Mock 模式复现的 v1.0 发布命令和机器可读 Gate 结果。

v1.0 的任务不是推翻这些实现，而是增加一层共享的执行治理和证据治理。

## 3. 目标与非目标

### 3.1 必须完成

- 定义统一 `AgentRun`、`ExecutionBudget`、`ExecutionUsage`、`TraceEvent` 和 `RunTerminalReason`。
- 所有会改变 Agent 状态的 API 都通过 `RunCoordinator` 启动和结束 Run。
- 嵌套 Requirement → Planning 子 Graph 共享同一 Run 和预算，不重复创建 Run。
- Graph 的入口、外部调用前、修复循环和生命周期动作边界具有显式预算守卫。
- 所有 Node、Conditional Route、LLM、Tool、重试、缓存、Validator、Repair、Interrupt、Checkpoint 和终止均可关联到同一 Run。
- Trace 只保存标准化、安全、最小字段；原始请求、Prompt、Provider 响应、API Key 和 Approval Token 不进入 Trace。
- 内存和 SQLite Run Repository 支持 Run 摘要、事件分页和会话 Run 链查询。
- 提供确定性故障注入机制，覆盖 Tool、LLM、Checkpoint、Plan Repository 和 Trace Sink。
- 建立 120+ 条版本化统一 Benchmark，其中至少 100 条实际执行工作流。
- 提供直接 LLM 单次规划 Baseline，以及 Validator、优化器、Soft Critic、局部重规划和缓存消融。
- 输出约束、轨迹、局部性、调用、延迟、Token 和成本口径明确的 JSON/Markdown 报告。
- 将安全正确性、终止性、失败分类、Trace 完整性和可复现性变成自动发布门禁。

### 3.2 明确不做

- 不增加新的旅游业务类型、OTA 库存、下单、支付或实时价格聚合。
- 不实现长期 Preference Memory 或跨用户画像；这些属于 v1.1。
- 不实现 MCP Server、备用 Provider 自动切换、完整前端和生产部署；这些属于 v1.2。
- 不为“多 Agent”标签拆分 Requirement、Critic、Weather 或 Validator；它们继续是 Graph、模型 Gateway 或确定性组件。
- 不接入必须依赖外部 SaaS 的 Trace 平台作为唯一实现；v1.0 的离线 CI 必须独立运行。
- 不把 SQLite 描述为多实例生产存储，不实现分布式锁、消息队列和全局调度。
- 不把 Mock 指标表述为真实 DeepSeek、OpenAI 或 AMap 线上质量。
- 不用单一最终文本相似度代替 Agent 轨迹和硬约束评测。

## 4. v1.0 成功标准

v1.0 的“完成”由四层证据共同定义：

| 层级 | 要回答的问题 | 主要证据 |
|---|---|---|
| Safety | completed 结果是否始终满足已知硬约束 | Hard Validator、关键 Gate |
| Behavior | Agent 是否走了正确节点、工具、参数、路由和中断 | Trace Assertion |
| Boundedness | 所有循环和外部调用是否在共享预算内停止 | ExecutionUsage、TerminalReason |
| Value | 完整循环相对单次生成和消融是否有可量化收益 | Baseline/Ablation Report |

最终展示的不是“测试很多”，而是以下可验证结论：

- 完整 Agent 在固定数据集上不会交付硬约束非法计划；
- 局部修复能减少无关日期变化和重复路线调用；
- Validator、Repair、Critic 和缓存各自贡献什么；
- Provider 失败不会被解释成业务 `infeasible`；
- 每个结论都能定位到具体 Run、Trace 和数据集版本。

## 5. AgentRun 的边界

### 5.1 Run、Thread、Session 的区别

| 标识 | 生命周期 | 含义 |
|---|---|---|
| `run_id` | 一次有界执行段 | 一次创建、恢复、编辑、审批或天气刷新 |
| `thread_id` | Requirement 澄清链 | 自然语言需求及多轮澄清 Checkpoint |
| `session_id` | Plan Lifecycle | 候选、Active Version、锁、Preview、天气事件 |
| `request_id` | 客户端命令幂等 | 防止同一恢复/编辑/天气动作重复生效 |
| `parent_run_id` | Run 因果关系 | 恢复当前 Interrupt 或处理前一 Run 的 Preview |

### 5.2 Run 类型

```python
class RunKind(StrEnum):
    STRUCTURED_PLAN = "structured_plan"
    NATURAL_PLAN = "natural_plan"
    CLARIFICATION_RESUME = "clarification_resume"
    LIFECYCLE_CREATE = "lifecycle_create"
    LIFECYCLE_CREATE_FROM_TEXT = "lifecycle_create_from_text"
    LIFECYCLE_RESUME = "lifecycle_resume"
    WEATHER_REFRESH = "weather_refresh"
```

只读的 Session、Version、Diff、Weather Event 和 Trace 查询不创建 AgentRun，因为它们不驱动 Agent 决策。

### 5.3 HITL 与 Run 终止

Run 不跨越人类等待时间：

```text
Run R1: parse → validate → interrupt(needs_clarification)
Run R2: resume patch → planning → interrupt(select_candidate)
Run R3: resume select → commit V1 → interrupt(await_action)
Run R4: weather refresh → preview → interrupt(await_approval)
Run R5: approve → commit V2 → interrupt(await_action)
```

`interrupted` 是一个成功且有界的终态，不是“仍在运行”。恢复操作创建新 Run，并通过 `parent_run_id`、`thread_id/session_id` 和 `causation_id` 关联前一 Interrupt。

### 5.4 幂等重放

同一 `request_id` 重放时：

- 不重复执行 Graph、LLM、Tool 或版本提交；
- 创建一个轻量新 Run，终态为 `replayed`；
- 记录 `replay_of_run_id` 和 `run.replayed` 事件；
- 返回原命令的领域结果，但响应头使用本次观测 Run ID。

这样既保持业务幂等，也能审计每次外部调用，不会把 HTTP 重试隐藏掉。

## 6. 总体架构

```text
FastAPI Route / Evaluation Runner
  │
  ▼
RunCoordinator
  ├─ create AgentRunRecord
  ├─ bind RunContext (ContextVar, async-task safe)
  ├─ invoke current Runtime / Service
  ├─ classify terminal result
  └─ finalize Usage + Trace completeness
       │
       ├──────────────► RunRepository
       │                 ├─ agent_runs
       │                 └─ trace_events
       │
       ▼
Existing Graphs
  ├─ instrumented nodes and route decisions
  ├─ explicit budget_guard nodes
  └─ existing strongly typed State
       │
       ▼
Observed Gateways
  ├─ Requirement / Edit / Critic LLM
  ├─ POI / Route / Weather Tool
  └─ Checkpoint / Plan Repository observers
```

不新增一个把所有业务包进巨大函数的“总 Agent”。`RunCoordinator` 是应用层执行边界，现有三个 Graph 仍保留各自清晰的 State、Node、Edge 和 Loop。

## 7. 统一领域模型

新增 `src/travel_agent/execution/models.py`。

### 7.1 Run 状态与终止原因

```python
class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    REPLAYED = "replayed"
    FAILED = "failed"

class RunTerminalReason(StrEnum):
    PLAN_COMPLETED = "plan_completed"
    BUSINESS_INFEASIBLE = "business_infeasible"
    NEEDS_CLARIFICATION = "needs_clarification"
    AWAITING_CANDIDATE_SELECTION = "awaiting_candidate_selection"
    AWAITING_USER_ACTION = "awaiting_user_action"
    AWAITING_APPROVAL = "awaiting_approval"
    REQUIRES_NEW_PLAN = "requires_new_plan"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    EXECUTION_BUDGET_EXHAUSTED = "execution_budget_exhausted"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    EXTERNAL_TOOL_FAILURE = "external_tool_failure"
    LLM_PROVIDER_FAILURE = "llm_provider_failure"
    CHECKPOINT_FAILURE = "checkpoint_failure"
    REPOSITORY_FAILURE = "repository_failure"
    INVALID_INTERNAL_STATE = "invalid_internal_state"
```

`degraded` 不作为独占 RunStatus。一个经过硬验证的计划可以 `COMPLETED + degraded_reasons=[soft_critic_unavailable]`；这样不会把可交付结果和运行失败混在一起。

### 7.2 AgentRunRecord

```python
class AgentRunRecord(BaseModel):
    schema_version: str = "agent-run-v1"
    run_id: str
    run_kind: RunKind
    status: RunStatus
    terminal_reason: RunTerminalReason | None
    thread_id: str | None
    session_id: str | None
    request_id: str | None
    parent_run_id: str | None
    replay_of_run_id: str | None
    causation_id: str | None
    plan_version_id: str | None
    budget: ExecutionBudget
    usage: ExecutionUsage
    degraded_reasons: tuple[str, ...]
    trace_status: TraceStatus
    started_at: datetime
    ended_at: datetime | None
    elapsed_ms: int | None
    config_fingerprint: str
```

RunRecord 不保存自然语言原文、完整 TripSpec、候选计划或 Provider 响应。领域结果继续由 Checkpoint 和 Plan Repository 管理。

### 7.3 Graph State Slice

三个 State 新增同构的最小字段：

```python
class ExecutionState(TypedDict):
    run_id: str
    budget_profile: str
    graph_steps_used: int
    repair_rounds_used: int
    last_action_fingerprint: str | None
    repeated_fingerprint_count: int
    execution_terminal_reason: str | None
```

State 只保留路由需要的快照，不保存 TraceEvent 列表、Token 明细或 Provider 原始数据。预算的并发一致性以 `ExecutionLedger` 为准，State Slice 用于 Graph 可见路由和 Checkpoint 恢复解释。

## 8. ExecutionBudget 设计

### 8.1 限额模型

```python
class ExecutionBudget(BaseModel):
    profile: str
    max_graph_steps: int
    max_tool_calls: int
    max_provider_attempts: int
    max_llm_calls: int
    max_llm_attempts: int
    max_llm_input_chars: int
    max_input_tokens: int
    max_output_tokens: int
    max_repair_rounds: int
    max_interrupts: int
    max_checkpoint_writes: int
    max_trace_events: int
    max_repeated_fingerprint_count: int
    deadline_ms: int
    max_estimated_cost_microunits: int | None
    terminal_step_reserve: int = 2
    terminal_trace_reserve: int = 4
```

```python
class ExecutionUsage(BaseModel):
    graph_steps: int = 0
    tool_calls: int = 0
    provider_attempts: int = 0
    cache_hits: int = 0
    llm_calls: int = 0
    llm_attempts: int = 0
    llm_input_chars: int = 0
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    repair_rounds: int = 0
    interrupts: int = 0
    checkpoint_writes: int = 0
    trace_events: int = 0
    estimated_cost_microunits: int | None = None
```

### 8.2 计数口径

- 一次逻辑 POI/Route/Weather 请求计一个 `tool_call`；批量请求中的每个独立 Query 分别计数。
- Cache Hit 计 `tool_call`，但不计 `provider_attempt`。
- Provider 首次调用和每次 Retry 都计 `provider_attempt`。
- Requirement、Clarification、Edit 和 Critic 每次逻辑模型任务计 `llm_call`，重试计 `llm_attempt`。
- Node 成功、失败或 Interrupt 前实际开始执行均计 `graph_step`；纯 Conditional Router 记录事件但不重复计 Node。
- Hard Repair 和 Soft Repair 各进入一次应用节点计一个 `repair_round`。
- Checkpoint 的成功写入计 `checkpoint_write`；读取只记录 Trace，不占写预算。
- Token 缺失时字段为 `None`，不能伪造为 0；Mock Provider 可以显式声明 `synthetic_usage=true`。

### 8.3 原子预留与提交

外部调用采用两阶段计量：

```text
reserve(operation, worst_case_usage)
  ├─ 超限 → 拒绝调用，记录 budget.exceeded
  └─ 通过 → 执行 Provider
                ├─ success → commit(actual_usage)
                └─ failure → commit(attempt_usage)
```

这样并发路线查询不会分别看到“还有一个名额”后共同突破总预算。`ExecutionLedger` 内部使用异步锁，预留对象在取消和异常时也必须结算。

### 8.4 Deadline 传播

每个 Gateway 的实际超时为：

```text
effective_timeout = min(component_timeout, run_remaining_deadline)
```

Retry 退避前再次检查剩余时间，Deadline 不够时不开始新 Attempt。Deadline 使用单调时钟计算，UTC 时间只用于展示。

### 8.5 Token 与成本限制

- 调用前用输入字符上限、Provider 最大输出 Token 和版本化 Pricing Registry 做保守预留。
- 调用后优先使用 Provider 返回的实际 Token；缺失时只报告 `unknown`，不得声称精确成本。
- `max_estimated_cost_microunits` 是保护上限，不在仓库硬编码易变化的商业价格。
- Pricing Registry 记录 `provider/model/effective_date/currency/input_rate/output_rate/source_note`，Benchmark 报告必须带版本。
- Mock 运行的成本为 `not_applicable` 或 `synthetic`，不能与真实 API 费用混合汇总。

### 8.6 必要与可选阶段

| 阶段 | 分类 | 预算不足时行为 |
|---|---|---|
| Requirement 解析 | 自然语言入口必要 | Run 失败，不生成猜测 TripSpec |
| POI/Route 事实 | 计划合法性必要 | Run 失败为 Tool/预算语义 |
| Hard Validator | 必要且预留 | 未执行不得返回 completed |
| 最终状态收敛 | 必要且预留 | 写入明确终止原因 |
| Soft Critic | 可选 | 跳过并标记降级，硬合法计划可交付 |
| Soft Repair | 可选 | 保留经过验证的 baseline |
| 优化器 | 可降级 | 使用确定性启发式回退并记录原因 |

`terminal_step_reserve` 保证可选节点不能耗尽 Hard Validator 和终态收敛所需步骤。预留步骤仍计入总使用量，不是绕过限制。

`terminal_trace_reserve` 专用于 `budget.exceeded`、必要降级和 Run 终态事件，普通事件不能占用。Trace Recorder 不为“记录事件”再次递归生成预算事件；事件上限接近耗尽时先写一次聚合告警，再只接受预留终态事件。

### 8.7 重复指纹终止

当前 Repair、Event 和 Action 已有多个领域 Fingerprint。v1.0 统一登记：

```text
fingerprint = hash(graph + node + plan_version + violation/action signature)
```

相同指纹连续达到上限时终止该循环。若存在合法但软质量未提升的 baseline，则降级交付；若仍有硬违规，则返回 `execution_budget_exhausted` 或明确业务不可行，不能笼统写成 `budget_or_candidates_exhausted`。

## 9. Trace 设计

### 9.1 TraceEvent Schema

```python
class TraceEvent(BaseModel):
    schema_version: str = "trace-event-v1"
    event_id: str
    run_id: str
    sequence: int
    event_type: TraceEventType
    timestamp: datetime
    monotonic_offset_ms: int
    duration_ms: int | None
    graph: str | None
    node: str | None
    operation: str | None
    status: str
    parent_event_id: str | None
    attempt: int | None
    plan_version_id: str | None
    attributes: dict[str, JsonScalar]
```

事件按 Run 内单调 `sequence` 排序；测试注入 Clock 和 ID Factory，确保轨迹断言稳定。

### 9.2 事件类型

```text
run.started / run.completed / run.interrupted / run.failed / run.replayed
node.started / node.completed / node.failed
route.decided
tool.started / tool.cache_hit / tool.retry / tool.completed / tool.failed
llm.started / llm.retry / llm.completed / llm.failed
validation.completed
repair.planned / repair.applied / repair.no_progress
interrupt.created / interrupt.resumed
checkpoint.read / checkpoint.written / checkpoint.failed
repository.cas_succeeded / repository.cas_conflict / repository.failed
budget.reserved / budget.updated / budget.exceeded
degradation.applied
```

### 9.3 安全属性白名单

允许记录：

- Provider/Model 名、Prompt Version；
- Tool Operation、Query Count、Route Mode；
- Cache Hit、Attempt、Elapsed、结果数量；
- Candidate/Plan/Item/Evidence 的稳定 ID；
- Validation Status、Violation Code、affected day 数量；
- 路由目标、终止原因、调用量和 Token 数；
- 领域 Fingerprint 和配置 Fingerprint。

禁止记录：

- API Key、Authorization Header、Approval Token；
- 原始自然语言请求、澄清回答、编辑文本；
- 完整 Prompt、模型原始输出和 Provider 原始响应；
- 完整坐标列表、完整 Plan/TripSpec、Checkpoint Payload；
- 异常对象中未经清洗的请求 URL 或响应正文。

Trace 属性通过显式 allowlist 编码，字符串默认最多 256 字符。不能依赖“调用方记得脱敏”。

### 9.4 Trace 与普通日志的关系

- `TraceEvent` 是 Benchmark、API 查询和轨迹断言的结构化事实源。
- 现有日志继续保留，用于人工排障；日志增加 `run_id`、`sequence`、`thread_id/session_id`。
- 日志写入失败不修改 Agent 决策；Trace 持久化降级会标记 `trace_status=degraded`。
- 发布 Benchmark 中 Trace 不完整直接 Gate 失败，不能用文本日志补算通过。

### 9.5 Trace 完整性

每个 Run 至少满足：

1. 恰好一个 `run.started`。
2. 恰好一个终态事件。
3. 每个 `node.started` 有 completed、failed 或 interrupt 配对。
4. 每个 Provider Attempt 有终态。
5. 所有 `route.decided` 的源节点和目标节点有效。
6. `ExecutionUsage` 与事件聚合结果一致。
7. sequence 唯一且严格递增。

## 10. Graph 接入设计

### 10.1 接入原则

不能把全部预算逻辑隐藏在 `RunCoordinator`。影响 Agent 路由的预算必须在 Graph 中可见；细粒度调用配额则由 Gateway 原子执行。

新增两种共享机制：

- `instrument_node(graph, node_name, callable)`：统一发出 Node 事件并计步。
- `execution_budget_guard` Node：把 Ledger 快照写回 State，路由到继续、降级或终止。

### 10.2 Requirement Graph

```text
START
  → requirement_budget_guard
  → parse_requirement
  → validate_requirement
      ├─ needs clarification → request_clarification → interrupt
      └─ resolve_anchors → evaluate_anchors
           ├─ needs clarification → interrupt
           └─ pre_planning_budget_guard
                ├─ continue → assemble_trip_spec → execute_planning
                └─ exhausted → execution_failed
```

Resume 进入 `resume_budget_guard` 后才允许调用 Clarification LLM。`execute_planning` 调用 Planning 子 Graph 时复用当前 RunContext，不创建第二个 Run。

### 10.3 Planning Graph

```text
START
  → planning_budget_guard
  → Search / POI / Route / Optimize / Materialize
  → validate_candidates
      ├─ deliverable → optional_phase_budget_guard
      │                  ├─ critic allowed → Critic / Grounding / Quality
      │                  └─ critic skipped → Select Valid Baseline
      └─ invalid → repair_budget_guard
                     ├─ repair allowed → Critic → RepairPlan → Local Repair
                     │                    → Route Delta → Revalidate
                     └─ exhausted → classify_unresolved_plan
```

`classify_unresolved_plan` 必须区分：

- 已证明在完整硬约束下无可行解：`business_infeasible`；
- 仍可能有解但执行预算不足：`execution_budget_exhausted`；
- Tool/Provider 证据缺失：对应外部失败。

### 10.4 Lifecycle / Weather Graph

```text
await_user_action
  → dispatch_action
  → lifecycle_budget_guard
      ├─ select / lock / approve / reject
      ├─ edit → Impact → local_preview_budget_guard → Preview
      └─ weather → Fetch / Risk / Event / Impact
                    → weather_repair_budget_guard → Preview
  → await_user_action (Interrupt，本 Run 终止)
```

编辑和天气复用相同的局部 Preview 预算与 Hard Validator 预留。锁冲突、未知暴露或需要用户选择仍进入 HITL，不因“还有预算”而自动扩大修改范围。

### 10.5 Conditional Route 观测

所有路由函数通过轻量包装器记录：

```text
route.decided
  graph=planning
  node=validate_candidates
  target=select_repair_target
  reason_code=hard_violation_present
```

`reason_code` 必须来自固定枚举，不能只记录任意 message。轨迹测试对 reason 和 target 共同断言。

## 11. Gateway 与存储观测

### 11.1 ExecutionObserver

新增共享 Protocol：

```python
class ExecutionObserver(Protocol):
    async def before_operation(self, request: OperationRequest) -> Reservation: ...
    async def on_retry(self, event: RetryObservation) -> None: ...
    async def after_operation(self, result: OperationResult) -> None: ...
    async def on_failure(self, failure: OperationFailure) -> None: ...
```

现有 Gateway 增加 Observer 依赖，不重写 Provider Protocol。Observer 从 async-safe `RunContext` 读取当前 Run；测试或非 API 直接调用时使用 No-op Context。

### 11.2 复用现有执行摘要

已有 `ToolResult`、`ToolExecutionSummary`、Requirement/Critic/Edit Execution Summary 继续作为领域响应和 State 摘要。Observer 从这些标准化对象提取 Trace 字段，Provider 原始响应仍不能进入 State。

### 11.3 Checkpoint 与 Repository

- 用装饰器包装 LangGraph Checkpointer 的 `get/put/aput`，记录读写和故障。
- `PlanRepository` 增加 Observer，CAS 冲突与存储不可用分开记录。
- RunRepository 独立于 Plan Repository，避免 Run 轨迹和计划业务对象形成循环依赖。
- v1.0 不要求 Plan、Checkpoint 和 Trace 跨库事务；必须记录部分失败并使用明确终态，不能伪造原子成功。

## 12. 失败与终止语义

| 场景 | RunStatus | TerminalReason | 领域结果 |
|---|---|---|---|
| 硬合法计划完成 | completed | plan_completed | completed |
| Soft Critic 不可用但硬合法 | completed | plan_completed | completed + degraded |
| 已证明约束下无可行解 | completed | business_infeasible | infeasible |
| 等待澄清/选择/审批 | interrupted | 对应 awaiting reason | HITL response |
| request_id 重放 | replayed | idempotent_replay | 原领域结果 |
| 预算不足且不能安全交付 | failed | execution_budget_exhausted | 安全错误 |
| 总 Deadline 到期 | failed | deadline_exceeded | 安全错误 |
| POI/Route/Weather 故障 | failed | external_tool_failure | 503，不是 infeasible |
| Requirement/Edit/Critic 必要调用失败 | failed 或 completed degraded | llm_provider_failure | 取决于阶段是否必要 |
| Checkpoint 无法保存 Interrupt | failed | checkpoint_failure | 不声称可恢复 |
| Plan CAS 冲突 | failed | repository_failure 或现有 conflict | 409/503 |

预算错误新增统一安全详情：

```json
{
  "detail": {
    "code": "execution_budget_exhausted",
    "run_id": "...",
    "limit": "max_tool_calls",
    "retryable": false
  }
}
```

API 不返回当前具体成本上限、Prompt 内容或内部 State。服务端配置导致的执行耗尽默认使用 HTTP 503；业务可行性仍通过正常响应表达。

## 13. Run Repository 与查询 API

### 13.1 存储结构

```text
agent_runs(
  run_id PK, kind, status, terminal_reason,
  thread_id, session_id, request_id, parent_run_id,
  started_at, ended_at, budget_json, usage_json,
  trace_status, config_fingerprint
)

trace_events(
  run_id, sequence, event_id, event_type,
  timestamp, duration_ms, graph, node, operation,
  status, attributes_json,
  PRIMARY KEY(run_id, sequence)
)
```

默认内存实现支持单元测试；SQLite 实现用于本地演示和重启查询，路径置于 `.data/`。事件只追加，Run 终态通过带 revision 的更新完成。

### 13.2 新增 API

```text
GET /api/v1/runs/{run_id}
GET /api/v1/runs/{run_id}/trace?after_sequence=0&limit=100
GET /api/v1/plan-sessions/{session_id}/runs?limit=50
GET /api/v1/requirement-threads/{thread_id}/runs?limit=50
```

所有会驱动 Agent 的 POST 响应增加：

```text
X-Agent-Run-Id: <run_id>
X-Agent-Trace-Status: complete|degraded
```

领域 Response Model 不直接增加易混淆的 `run_id` 字段。应用层使用泛型 `ExecutionResult[T]` 返回 payload 和 Run 摘要，Route 解包领域响应并设置 Header。这样保持现有 API Body 兼容，也能稳定定位 Trace。

RunCoordinator 创建 Run 后立即把 ID 写入 `request.state.agent_run_id`；即使领域调用抛出异常，统一 Exception Handler 也能安全返回同一 `run_id` 并完成 Run 终态，避免成功响应有 Trace、失败响应却无法定位。

Trace API 默认返回摘要属性；不提供原始 Prompt/State 下载端点。

## 14. 故障注入设计

### 14.1 FaultInjector

```python
class FaultPoint(StrEnum):
    REQUIREMENT_LLM = "requirement_llm"
    CLARIFICATION_LLM = "clarification_llm"
    EDIT_LLM = "edit_llm"
    CRITIC_LLM = "critic_llm"
    POI_PROVIDER = "poi_provider"
    ROUTE_PROVIDER = "route_provider"
    WEATHER_PROVIDER = "weather_provider"
    CHECKPOINT_READ = "checkpoint_read"
    CHECKPOINT_WRITE = "checkpoint_write"
    PLAN_REPOSITORY = "plan_repository"
    TRACE_SINK = "trace_sink"

class FaultMode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    CONNECTION_ERROR = "connection_error"
    INVALID_SCHEMA = "invalid_schema"
    EMPTY_BUSINESS_RESULT = "empty_business_result"
    WRITE_FAILURE = "write_failure"
```

FaultPlan 还包含触发 Attempt、Operation、最大次数和 expected classification。

### 14.2 安全边界

- Fault Injection 默认禁用，只能通过测试依赖注入或评测 CLI 启用。
- 生产 API 不接受来自客户端的 FaultPlan。
- 注入点位于 Protocol/Gateway/Repository 边界，不在领域算法中散落特殊分支。
- `empty_business_result` 与网络/Schema 错误分开，证明空结果不会被错误转换为 Provider 故障，反之亦然。
- Trace Sink 故障不改变计划硬约束判断，但 Run 标记 `trace_status=degraded`；任何发布评测遇到该状态都失败。

## 15. 统一 Benchmark 数据集

### 15.1 数据目录

```text
evals/v1_0/
  manifest.json
  cases/
    requirements.jsonl
    planning.jsonl
    repair_critic.jsonl
    lifecycle_weather.jsonl
    budget_faults.jsonl
  baselines/
    direct_plan_expected.jsonl
  fixtures/
    provider_responses/
    checkpoints/
```

Manifest 记录 `dataset_version`、Schema Version、Case 文件 Hash、能力标签和最小 Runner Version。

### 15.2 Case Schema

```python
class BenchmarkCase(BaseModel):
    case_id: str
    dataset_version: str
    evidence_level: EvidenceLevel
    run_kind: RunKind
    capability_tags: tuple[str, ...]
    input_ref: str
    provider_profile: str
    budget_override: dict[str, int] | None
    fault_plan: FaultPlan | None
    expected_terminal: ExpectedTerminal
    trace_assertions: tuple[TraceAssertion, ...]
    metric_eligibility: tuple[str, ...]
```

### 15.3 证据等级

| 等级 | 定义 | 可否进入发布 Gate |
|---|---|---|
| `workflow_execution` | Runner 实际调用 Graph/Service 并从结果和 Trace 计算 | 是 |
| `component_execution` | 实际运行 Policy/Gateway/Repository | 仅组件 Gate |
| `annotated_contract` | 读取人工预标注结果，没有执行目标路径 | 否 |
| `live_provider` | 显式调用真实 LLM/AMap | 单独报告，不作为默认 CI Gate |

已有评测可以通过 Adapter 迁移，但必须诚实标注证据等级。v0.9 天气 Fixture 中预填的 Graph 行为字段，在没有执行 Lifecycle Graph 前只能算 `annotated_contract`。

### 15.4 数据量与覆盖

v1.0 固定数据集至少 120 条，其中至少 100 条为 `workflow_execution`：

| 能力域 | 最少用例 | 重点 |
|---|---:|---|
| Requirement / Clarification | 30 | 字段、冲突、Patch、Interrupt |
| Planning / Optimization | 22 | 路线、候选、硬验证、降级 |
| Repair / Soft Critic | 20 | Grounding、局部修复、无进展 |
| Lifecycle / Weather | 24 | 锁、Preview、审批、事件、幂等 |
| Budget / Fault / Persistence | 24 | 超限、Retry、存储、重启、分类 |

同一 Case 可以贡献多个指标，但报告必须同时给出按能力域分组结果，不能只展示一个掩盖弱项的总平均。

## 16. Baseline 与消融

### 16.1 直接 LLM Baseline

直接 LLM Baseline 只执行一次结构化计划生成：

```text
Natural Request + bounded frozen EvidenceBundle
  → one LLM call
  → parse CandidatePlan
  → evaluator-only Hard Validator
  → report result，不把违规反馈给模型
```

公平性规则：

- 使用与完整系统相同的请求和版本化 EvidenceBundle；
- EvidenceBundle 构建成本单独报告，不能隐形忽略；
- 使用相同 CandidatePlan Schema 和外部评分器；
- Baseline 无工具循环、Validator 反馈、Repair、Critic 或 HITL；
- Mock Baseline 只验证 Runner Contract，不用于声称真实模型质量；
- DeepSeek/OpenAI Baseline 仅显式运行，并记录 Provider、Model、Prompt、日期、Token 和费用。

### 16.2 EvaluationVariant

```python
class EvaluationVariant(StrEnum):
    FULL = "full"
    DIRECT_LLM = "direct_llm"
    NO_VALIDATOR = "no_validator"
    NO_OPTIMIZER = "no_optimizer"
    NO_SOFT_CRITIC = "no_soft_critic"
    FULL_REPLAN = "full_replan"
    CACHE_OFF = "cache_off"
```

消融通过显式 `EvaluationConfig` 注入，不能在脚本中临时改环境变量并污染并行 Case。

### 16.3 安全消融边界

- `NO_VALIDATOR` 只能在评测沙箱中运行；其输出由独立 Evaluator Validator 评分，但不能通过用户 API 交付。
- `NO_OPTIMIZER` 使用已有确定性启发式，不改 Provider 数据。
- `NO_SOFT_CRITIC` 仍执行 Hard Validator。
- `FULL_REPLAN` 与 Local Replan 比较 locality、路线调用和延迟，锁与 must_visit 规则保持一致。
- `CACHE_OFF` 只用于测量调用和延迟，不宣称提高计划质量。

## 17. 指标体系

### 17.1 Safety 指标

```text
completed_hard_constraint_satisfaction_rate
must_visit_preservation_rate
locked_artifact_preservation_rate
unaffected_day_preservation_rate
unsafe_delivery_count
```

Safety 指标按 Case 的已知事实计算。未知 Provider 事实不能当作满足，也不能直接当作违反，应单列 `unknown_fact_count`。

### 17.2 Agent Behavior 指标

```text
trajectory_assertion_pass_rate
route_decision_accuracy
tool_selection_accuracy
tool_argument_contract_rate
interrupt_position_accuracy
repair_action_match_rate
failure_classification_accuracy
bounded_termination_rate
trace_completeness_rate
```

轨迹断言支持：事件存在/不存在、严格先后、计数范围、属性匹配、终止原因和跨 Run 因果关系。

### 17.3 Quality 与效率指标

```text
route_efficiency
optimization_objective_delta
replanning_locality
route_reuse_rate
tool_calls / provider_attempts / cache_hit_rate
llm_calls / input_tokens / output_tokens
latency_p50 / latency_p95
estimated_cost_by_provider_model
```

Windows 本地耗时易波动。正确性 Gate 使用确定性计数；性能报告至少重复三次取中位数，p95 只做非阻断回归告警，除非在固定 CI 环境建立稳定基线。

### 17.4 报告来源信息

每份 JSON 和 Markdown 报告必须包含：

- Git commit 和 dirty 状态；
- 项目版本、Python 版本和平台；
- Dataset/Runner/Trace Schema 版本；
- 配置和 Fixture Fingerprint；
- 随机 Seed；
- Provider、Model、Prompt Version；
- Mock、Live、Synthetic Usage 标识；
- Pricing Registry 版本；
- 开始时间、结束时间和 Case 数。

## 18. v1.0 发布门禁

默认命令：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_v1_release.py --profile mock --gate
```

机器可读输出：

```text
reports/v1_0/<dataset-version>/<run-stamp>/report.json
reports/v1_0/<dataset-version>/<run-stamp>/summary.md
```

报告目录默认忽略提交；README 只提交经人工确认的版本摘要和数据集/命令信息。

### 18.1 阻断 Gate

1. 固定数据集不少于 120 条，`workflow_execution` 不少于 100 条。
2. 所有 completed 计划的已知硬约束满足率为 100%。
3. `unsafe_delivery_count = 0`。
4. must_visit、锁定项和标注未影响日期保持率均为 100%。
5. 所有循环在 ExecutionBudget 内终止，无遗留 `RUNNING` Run。
6. Tool/LLM/Checkpoint 故障分类准确率为 100%，误报 `business_infeasible` 数为 0。
7. 关键 Case 的 Trace 完整率和关键轨迹断言通过率为 100%；普通轨迹断言总体至少 98%。
8. 幂等重放不产生新的 Tool/LLM 调用、Preview 或 Plan Version。
9. Trace/日志脱敏测试中秘密和原始 Prompt 泄漏数为 0。
10. 全 Mock 模式相同 Seed 连续两次的领域结果 Hash、终止原因和调用计数一致。
11. 单元/集成测试通过，Branch Coverage 不低于 90%。

### 18.2 比较 Gate

- FULL 相对 DIRECT_LLM 的硬约束满足率不得更差；真实模型结论只来自 Live 报告。
- FULL 相对 NO_VALIDATOR 的 unsafe delivery 必须不增加。
- Local Replan 相对 FULL_REPLAN 的未影响日期保持率不得更差，且路线调用数应更低；若数据集无差异必须解释。
- CACHE_ON 相对 CACHE_OFF 的 Provider Attempt 不得更多，领域结果 Hash 必须一致。
- Soft Critic 开关不得造成硬约束回归。

比较 Gate 不设置未经数据校准的“必须提升 20%”之类任意阈值。首次实现先生成基线，再只把稳定、可解释的差异固化为非回归门槛。

## 19. 配置设计

`.env.example` 计划增加：

```dotenv
RUN_STORE_BACKEND=memory
RUN_SQLITE_PATH=.data/travel-agent-runs.sqlite3
RUN_BUDGET_PROFILE=default-v1
RUN_MAX_GRAPH_STEPS=120
RUN_MAX_TOOL_CALLS=160
RUN_MAX_PROVIDER_ATTEMPTS=240
RUN_MAX_LLM_CALLS=8
RUN_MAX_LLM_ATTEMPTS=12
RUN_MAX_LLM_INPUT_CHARS=80000
RUN_MAX_INPUT_TOKENS=40000
RUN_MAX_OUTPUT_TOKENS=12000
RUN_MAX_REPAIR_ROUNDS=4
RUN_MAX_INTERRUPTS=1
RUN_MAX_CHECKPOINT_WRITES=160
RUN_MAX_TRACE_EVENTS=512
RUN_MAX_REPEATED_FINGERPRINT_COUNT=2
RUN_DEADLINE_SECONDS=120
TRACE_ATTRIBUTE_MAX_CHARS=256
```

约束：

- 配置值必须为正并有安全上限，`terminal_step_reserve < max_graph_steps` 且 `terminal_trace_reserve < max_trace_events`。
- 组件局部上限不能高于 Run 总上限；不一致时配置启动失败。
- 评测允许通过代码注入更小预算，不允许通过 Case 提高系统安全上限。
- Run Profile、Provider 和现有局部预算共同进入 `config_fingerprint`。
- 默认全 Mock 可离线运行；真实 Provider 不自动 fallback 到 Mock。

默认值是首轮实现起点，完成 120+ Benchmark 后允许基于 p95 Usage 收紧，但变更必须更新 Profile Version 和设计偏差记录。

## 20. 计划代码结构

```text
src/travel_agent/
  execution/
    __init__.py
    models.py              # Run/Budget/Usage/Trace Schema
    context.py             # async-safe RunContext
    budget.py              # Ledger、Reservation、Deadline
    tracing.py             # TraceRecorder、Node/Route 包装
    observer.py            # Gateway/Repository Observer
    coordinator.py         # AgentRun 应用边界
    repository.py          # memory/sqlite RunRepository
    errors.py              # 预算和运行安全错误
    fingerprints.py
  evaluation/
    __init__.py
    models.py
    loader.py
    runner.py
    trajectory.py
    metrics.py
    variants.py
    faults.py
    baselines.py
    report.py
  graph/
    state.py               # ExecutionState Slice
    workflow.py            # Budget Guard + instrument_node
  requirements/
    state.py
    workflow.py            # Requirement/Resume Guard
  lifecycle/
    state.py
    workflow.py            # Lifecycle/Weather Guard
    repository.py          # Observer 接入
  tools/gateway.py         # Observer + Reservation
  weather/gateway.py
  requirements/gateway.py
  critique/gateway.py
  edits/gateway.py
  api/routes.py            # Run Header + Trace API
  api/errors.py
  config.py
  runtime.py

evals/v1_0/
scripts/evaluate_v1_release.py
tests/execution/
tests/evaluation/
docs/v1.0/
```

`evaluation/` 负责统一框架，现有各领域 evaluation 模块继续提供领域指标函数，通过 Adapter 复用，避免一次性重写所有评测逻辑。

## 21. 分阶段实施计划

### Phase A：Run、Trace 与 Repository

- 建立 Run/Budget/Usage/Trace 强类型模型；
- 实现内存 RunRepository、TraceRecorder、Clock/ID 注入；
- 实现 RunCoordinator 和 API `X-Agent-Run-Id`；
- 给现有入口建立 Run，但暂只观测，不改变 Graph 路由。

验收：一次结构化计划、自然语言中断和生命周期恢复都能查询完整 Run 摘要，Run 边界正确。

### Phase B：Node、Route 与 Gateway 计量

- 为三个 Graph 注册 instrumented node/route；
- 为 Tool、Weather、Requirement、Edit、Critic Gateway 接入 Observer；
- 为 Checkpointer 和 Plan Repository 接入存储事件；
- 建立 Usage 与 Trace 聚合一致性测试。

验收：Node、Tool、LLM、Retry、Cache、Interrupt 和 CAS 均可关联；并发 Tool 计数不丢失或重复。

### Phase C：共享预算与失败语义

- 实现 Ledger 的原子 Reservation、Deadline 和预留终态步骤；
- 在 Requirement、Planning、Lifecycle/Weather 插入显式 Guard；
- 区分必要/可选阶段降级；
- 修正含糊的 `budget_or_candidates_exhausted` 终止语义；
- 增加预算安全错误与 API Handler。

验收：任一限额都能通过测试触发；没有未经最终 Hard Validation 的 completed 结果；Tool 故障不变成 infeasible。

### Phase D：故障注入与轨迹断言

- 实现 FaultInjector 和 Fixture Provider；
- 实现 TraceAssertion DSL；
- 覆盖超时、限流、Schema、空结果、Checkpoint、CAS 和 Trace Sink；
- 增加秘密泄漏、事件配对和 Run 终态一致性测试。

验收：故障分类、Retry 次数、降级路径和终止原因均由实际 Trace 证明。

### Phase E：统一数据集与 Runner

- 建立 v1.0 Manifest 和 120+ Case；
- 将已有七套评测接入统一 EvidenceLevel；
- 升级关键 Weather/Lifecycle Fixture 为实际 Graph 执行；
- 输出 JSON、Markdown、Case 明细和失败 Trace 摘要。

验收：全 Mock 单命令可离线运行，相同 Seed 结果可复现，报告包含完整来源信息。

### Phase F：Baseline、消融与发布

- 实现 DirectPlanModel 和单次 LLM Baseline；
- 实现五组 EvaluationVariant；
- 固化阻断 Gate 与稳定比较 Gate；
- 更新 README、版本号、配置示例和实际评测报告；
- 运行全量测试、覆盖率、编译和依赖检查。

验收：v1.0 DoD 全部满足，README 只陈述实际测量结果。

## 22. 测试矩阵

### 22.1 单元测试

- Budget Reservation 的并发、取消、Retry、Deadline 和终态预留；
- Usage 缺 Token、真实 Token、Synthetic Token 和成本 Registry；
- Trace sequence、事件配对、属性白名单、长度限制和脱敏；
- Run 终态分类、父子关联和 replay；
- Fingerprint 重复终止；
- TraceAssertion 顺序、计数、属性和负断言。

### 22.2 Graph 轨迹测试

- Requirement 完整、缺字段、冲突、澄清恢复和耗尽；
- Planning 合法直达、Hard Repair、Soft Repair、无进展和预算不足；
- Lifecycle select/edit/lock/approve/reject；
- Weather no-change/event/lock/preview/approve/deduplicate；
- 每条路径断言 Node 顺序、Route Reason、Tool 参数摘要、调用计数、Interrupt 和 TerminalReason。

### 22.3 API 测试

- 所有 Agent POST 返回 Run Header；
- Run/Trace/Session Run Chain 分页；
- 不存在的 Run、非法分页和权限边界；
- 503/409/404 错误均带安全 `run_id`；
- 同 request_id 重放不重复副作用；
- 错误和 Trace 不含 Key、Prompt、原文或 Approval Token。

### 22.4 持久化测试

- SQLite 重启后 Run 和 Trace 可查询；
- Interrupt 前 Checkpoint 写失败不声称可恢复；
- Plan Commit 成功但 Trace Sink 失败时领域版本正确、观测状态降级；
- CAS 冲突只生成一个版本；
- 部分 Trace 写入后仍能把 Run 收敛为明确失败/降级状态。

### 22.5 评测框架测试

- Manifest/Case Hash 和版本校验；
- EvidenceLevel 不允许 annotated case 进入发布 Gate；
- 全 Mock 双跑领域 Hash、终止原因和调用计数一致；
- Baseline 不读取 Full Variant 的 Validator 反馈；
- Variant 之间 Cache、Repository 和 Context 隔离；
- 报告缺来源信息时 Gate 失败。

## 23. 风险与缓解

| 风险 | 后果 | 缓解 |
|---|---|---|
| 观测代码改变业务行为 | 指标本身污染结果 | No-op Observer 对照、Hash 回归、包装器最小化 |
| 并发 Tool 双重扣减 | 误判预算或突破上限 | 原子 Reservation、并发测试 |
| State 与 Ledger 用量不一致 | 路由和报告冲突 | Ledger 为事实源，节点结束同步快照并做一致性 Gate |
| Token/价格缺失 | 成本报告虚假精确 | `None/unknown` 语义、版本化 Pricing Registry |
| Deadline 测试不稳定 | CI 偶发失败 | 单调 FakeClock，性能阈值非阻断 |
| Trace 量过大 | State/SQLite 膨胀 | State 外存储、事件上限、属性白名单、分页 |
| Trace 写失败掩盖执行 | 无法审计 | trace_status 降级、发布 Gate 阻断、紧急终态摘要 |
| Direct LLM 比较不公平 | 得出错误结论 | 同 Schema/请求/Evidence，单列 Evidence 成本和 Mock/Live |
| NO_VALIDATOR 被误用于 API | 交付非法计划 | Evaluation-only 类型和依赖边界，生产配置禁止 |
| 旧 Fixture 冒充 E2E | 指标看似优秀但无证据 | EvidenceLevel 强制标记，Gate 只读实际执行用例 |

## 24. Definition of Done

v1.0 完成必须同时满足：

1. 每次 Agent mutation 都有唯一 Run，HITL Resume 和幂等 Replay 的边界正确。
2. Requirement、Planning、Lifecycle/Weather Graph 都有显式 Budget Guard 和可观察 Conditional Route。
3. Tool、LLM、Retry、Cache、Validator、Repair、Interrupt、Checkpoint、CAS 和终止可通过同一 Run Trace 关联。
4. 统一预算在并发外部调用下仍严格生效，所有 Loop 有界终止。
5. 必要阶段预算不足时不会交付未经硬验证的计划；可选阶段能安全降级。
6. 业务 infeasible、执行预算、外部故障、HITL 和内部状态错误语义分离。
7. Trace 不保存原始 Provider 响应、Prompt、用户原文、Key 或 Approval Token。
8. Memory/SQLite Run Repository、Run API 和事件分页均有测试。
9. 至少 120 条统一 Fixture，至少 100 条实际执行工作流；证据等级如实标记。
10. Direct LLM Baseline、五组消融和故障注入可由统一 Runner 执行。
11. 阻断 Release Gate 全部通过，Branch Coverage 不低于 90%。
12. README 给出 Mock Gate、Live Eval、Run/Trace 查询和报告解释方式。
13. 实际报告包含版本、配置、数据、Provider、成本口径和可复现信息。
14. 文档不把 Mock/Annotated 指标包装成真实模型、地图或端到端线上效果。

## 25. 面试演示脚本

推荐演示一条由多个 Run 组成的因果链：

```text
R1 Natural Plan
  → Requirement LLM → deterministic issues
  → needs_clarification interrupt

R2 Clarification Resume
  → patch → anchor tools → planning
  → route matrix → optimizer → hard validation
  → soft critic → candidate selection interrupt

R3 Select Candidate
  → persist V1 → await_user_action

R4 Weather Refresh
  → weather event → affected day → local route delta
  → hard validation → Preview → approval interrupt

R5 Approve
  → CAS commit V2 → await_user_action
```

演示界面或命令行同时展示：

- Session Run Chain；
- 每个 Run 的预算与实际 Usage；
- Route Decision、Cache、Retry、Repair 和 Interrupt；
- V1/Preview/V2 Diff 与未影响日期 Hash；
- 同 request_id 重放时 Tool/LLM 调用数为 0；
- 一个 Route timeout 故障注入，证明终态是外部失败而非 infeasible；
- FULL 与 Direct LLM、No Validator、Full Replan、Cache Off 的对比表。

面试重点解释：

1. 为什么 Run 不能等于长期 Session。
2. 为什么预算既需要 Graph Guard，也需要 Gateway 原子计量。
3. 为什么 Trace 存 State 外，但 State 仍保留最小预算路由快照。
4. 为什么 Hard Validator 使用预留预算，Soft Critic 可以降级。
5. 如何证明局部重规划真的只改必要部分，而不是只看最终文本。
6. 为什么 Mock、Live、Annotated 三类证据必须分开报告。
7. 为什么 v1.0 仍不需要机械拆成多 Agent。

## 26. 对后续版本的接口承诺

### 26.1 v1.1 Preference Memory

v1.1 的 Memory 读取、上下文裁剪、提议和确认将作为新的可观测 Operation 接入同一 RunContext，并受 Token、调用和 Deadline 预算约束。Trace 只记录 Memory ID、类别、使用原因和数量，不记录完整长期内容。

v1.0 不提前实现 Memory，但必须保证 ExecutionObserver、Trace Schema 和 Benchmark Variant 可以增加：

```text
memory.retrieve / context.compose / memory.propose / memory.confirm
```

### 26.2 v1.2 MCP 与生产化

v1.2 的 MCP Tool 和 REST API 必须复用相同 Application Service、RunCoordinator、ExecutionBudget 和 Trace，避免 MCP 形成第二套 Agent 逻辑。备用 Provider 的 Failover 也要以 Provider Attempt、Fallback Reason 和成本进入现有 Trace。

v1.0 的 Repository Protocol 允许未来替换生产存储，但本版本不实现多实例一致性或外部 Trace SaaS。

## 27. 最终取舍

v1.0 的项目价值不在于“增加了一套监控”或“多写了一批测试”，而在于把 Agent Engineering 的核心主张变成可执行证据：状态和循环可见，工具调用受控，失败语义清晰，HITL 可恢复，局部修复可量化，完整系统相对 Baseline 和消融的收益可复现。

完成本版本后，项目可以作为一个边界清晰的核心 Travel Agent 发布：它不声称已经具备长期 Memory、MCP 或生产平台能力，但能可靠回答面试中最关键的问题——**Agent 做了什么、为什么这样做、在什么预算内停止，以及如何证明它没有破坏用户约束。**

## 28. 实施结果（2026-08-24）

v1.0 已按本设计落地 `AgentRun`、共享 `ExecutionBudget`、强类型安全 Trace、Memory/SQLite Run Store、Run/Trace 查询 API、Graph/Gateway/Checkpoint/Repository 观测、故障注入、统一发布门禁、评测专用五组消融和单次 DeepSeek Baseline 接口。

实际本地证据：全量测试 `462 passed, 2 skipped`，Branch Coverage `90.19%`；120 次 Mock 实际工作流发布门禁通过，completed 硬约束满足率、有界终止率、故障分类准确率和 Trace 完整率均为 100%，不安全交付与外部故障误判为业务不可行均为 0。180 次消融执行通过比较门禁，其中 `NO_VALIDATOR` 出现 2 次不安全交付，CACHE_OFF 的 Provider Attempt 为 403，FULL 为 49。

使用、API、配置、报告解释和限制以 [v1.0 实现文档](README.md) 为准。Mock Token/费用保持 unknown，Direct Mock 只属于 annotated contract；这些结果不代表真实 DeepSeek、OpenAI 或 AMap 线上质量。
