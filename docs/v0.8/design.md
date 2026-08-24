# v0.8 计划生命周期 HITL 设计报告

> 文档状态：已实现设计基线  
> 上一版本：v0.7.0 Grounded LLM Soft Critic  
> 目标版本：v0.8.0  
> 设计日期：2026-08-24

## 1. 设计结论

v0.8 将“生成一次计划”升级为“围绕计划持续协作”：用户可以从候选中选择基线版本，锁定不希望变化的日期或项目，用自然语言提出修改；Agent 只分析并修改必要范围，重新调用缺失的路线或 POI 工具，再经过 Hard Validator 与 Grounded Soft Critic，向用户展示可审计的 Preview 和 Diff。只有用户批准后，系统才提交新的不可变计划版本。

```text
Completed Candidate Set
  → User Select Candidate
  → Persist Plan V1
  → User Lock / Edit Intent
  → Parse + Ground Edit Intent
  → Deterministic Impact Analysis / Lock Guard
  → Local Draft Change / Delta Tool Use
  → Hard Validate / Grounded Soft Critic
  → Build Preview + PlanDiff
  → Interrupt: Await Approval
      ├─ approve → CAS Commit Plan V2 → Await Next Change
      └─ reject  → Discard Preview  → Await Next Change
```

本版本的核心不是增加一组行程 CRUD 接口，而是把用户反馈真正放进可暂停、可恢复、可验证的 Agent 决策闭环。

## 2. 当前基线与待解决问题

现有版本已经具备：

- v0.4 的 `Interrupt → Resume → Patch → Validate`、旧 Interrupt 409、单线程 Resume 串行化和 SQLite Checkpoint；
- v0.5 的违规影响分析、Route Delta、未受影响日期 Hash、局部修复与有界终止；
- v0.6 的多候选、真实驾车/步行路线和优化器；
- v0.7 的 Grounded Soft Critic、Evidence Guard、确定性 Quality Gate 和最多一次软修复。

但当前 `PlanningResponse` 返回后 Graph 已经结束，用户只能接受自动选择结果，不能安全地表达以下操作：

- 选择另一个候选作为后续修改基线；
- 锁定 Day 2 或某个活动，防止后续重规划破坏它；
- 用“把博物馆移到第三天”“换掉第二天下午的商场”等自然语言编辑计划；
- 在提交前查看影响范围、硬约束结果、软质量变化和 V1/V2 Diff；
- 服务重启后继续待选择、待澄清或待审批任务。

如果直接在 API 层修改 `PlanCandidate`，会绕过 Graph、Tool Gateway、Validator、Critic、Checkpoint 和轨迹测试，无法体现 Agent Engineering。因此 v0.8 新建独立的计划生命周期 Graph，复用已有规划能力但不改写其职责。

## 3. 目标与非目标

### 3.1 必须完成

1. 支持用户接受推荐候选或选择其他硬合法候选，并持久化不可变 `PlanVersion V1`。
2. 支持项目锁和日期锁；任何自动编辑、硬修复或软修复都不能越过锁定边界。
3. 支持结构化编辑命令和自然语言编辑，LLM 只负责语义解析，确定性代码负责实体落地、影响分析和安全判定。
4. 对编辑范围执行局部重规划，只补查新增或失效的 POI/Route Facts。
5. 编辑后的 Preview 必须重新经过 Hard Validator、Grounding Gate 和 Soft Critic；硬非法 Preview 不允许进入审批。
6. 用户审批前不修改 Active Version；批准后以乐观并发方式提交 V2，拒绝则保留 V1。
7. 生成结构化 `PlanDiff`，解释新增、删除、移动、时间和路线变化，以及硬/软质量变化。
8. 待选择、待编辑澄清和待审批状态支持 Checkpoint 恢复；旧 Interrupt、重复 request ID 和并发 Resume 有稳定语义。
9. 用轨迹测试和离线 Benchmark 证明锁定保持、局部性、Diff 正确性、幂等性和失败不污染 Active Version。

### 3.2 明确不做

- 不修改旅行日期、总预算、交通锚点、住宿、行动能力和 `must_visit` 等 `TripSpec` 硬约束；这类请求返回 `requires_new_plan`，由新规划会话处理。
- 不支持天气事件；天气驱动的 `ChangeEvent` 属于 v0.9。
- 不支持长期 Preference Memory、跨会话个性化或上下文裁剪；这些属于 v1.1。
- 不支持 MCP、完整前端、PostgreSQL、多实例锁、租户和生产部署；这些属于 v1.2。
- 不支持多人协作、分支合并、任意历史版本编辑或 OTA 下单。
- 不为“多 Agent”标签拆分 Selection Agent、Edit Agent 或 Approval Agent；v0.8 仍是一个显式编排 Graph。

## 4. 关键架构决策

### 4.1 新增生命周期 Graph，不把交互塞回规划大函数

保留现有 `PlanningWorkflow` 和 `RequirementWorkflow`。新建 `PlanLifecycleWorkflow` 管理计划生成后的选择、锁定、修改、审批和版本提交。应用层负责把一次成功规划生成的标准化快照交给生命周期 Graph。

新入口是显式 opt-in，会话接口不会改变现有接口语义：

- 现有 `/api/v1/plans` 和 `/api/v1/plans/from-text` 继续返回一次性规划结果；
- 新增 `/api/v1/plan-sessions...` 进入可恢复生命周期；
- 结构化和自然语言入口都复用现有规划流程，不复制 Planner 逻辑。

自然语言入口遇到需求缺失时，应用服务先恢复现有 Requirement Graph；规划完成后再 bootstrap Lifecycle Graph。对外只暴露一个 `session_id`，内部使用独立的 `intake_thread_id` 与 `lifecycle_thread_id`，避免不同 State Schema 共用 Checkpoint namespace。

### 4.2 Preview 与 Commit 分离

用户提交 Edit Intent 只授权生成预览，不授权覆盖当前计划。Graph 可以调用只读 POI/Route 工具构造 Preview，但只有 `approve_preview` 才能提交新版本。

```text
Active V1 ── edit ──> Pending Preview P1
    │                       ├─ reject ──> Active V1
    │                       └─ approve ─> Active V2
    └───────────────────────────────────> 始终可恢复
```

Preview 绑定 `base_version_id + session_revision + request_id`。锁变化、候选重新选择或另一个版本先提交后，旧 Preview 自动变为 stale，批准时返回 409。

### 4.3 LLM 只做软语义解析

LLM 可以把“把西湖边那个博物馆挪到第三天上午”解析成候选 `EditPatch`，但不能：

- 直接写 `PlanCandidate` 或 Graph State；
- 决定项目 ID、影响日期、路线失效范围或锁冲突；
- 修改预算、时间窗、地图事实和 `must_visit`；
- 宣称计划硬合法，或直接批准/提交版本。

实体解析、动作白名单、Impact Analysis、Lock Guard、Hard Validation、Diff 和 Commit 全部由确定性代码负责。

### 4.4 线性版本历史

v0.8 只支持单 Active Version 的线性历史：`V1 → V2 → V3`。旧版本可读取和比较，但不能从任意旧版本创建分支。这样可以把并发语义收敛为 `expected_active_version_id` 的 Compare-And-Swap，并为 v0.9 的事件重规划提供稳定基线。

## 5. 用户可执行动作与安全边界

### 5.1 动作白名单

| 动作 | 是否调用 LLM | 允许范围 | 主要守卫 |
|---|---:|---|---|
| `accept_recommendation` | 否 | 接受推荐候选为 V1 | 候选必须硬合法 |
| `select_candidate` | 否 | 选择候选 ID 为 V1 | 候选存在且硬合法 |
| `lock_day` / `unlock_day` | 否 | 锁定或解锁整天 | expected revision |
| `lock_item` / `unlock_item` | 否 | 锁定或解锁单个项目 | item ID 存在 |
| `move_item` | 结构化时否 | 跨日移动可选项目 | 最多影响两天 |
| `reorder_item` | 结构化时否 | 同日调整顺序 | 不跨锁定锚点 |
| `remove_item` | 结构化时否 | 删除非 `must_visit` 项目 | 不得造成硬违规 |
| `add_item` | 结构化时否 | 按查询新增可选 POI | POI Tool + Route Delta |
| `replace_item` | 结构化时否 | 替换非 `must_visit` 项目 | POI Tool + Route Delta |
| `edit_text` | 是 | 解析为以上最多 3 个原子动作 | Schema + Grounding + 白名单 |
| `approve_preview` / `reject_preview` | 否 | 批准或拒绝当前 Preview | token + version + revision |

单次修改最多包含 3 个原子动作，最多影响 2 个日期。超出范围、涉及 TripSpec 或需要整程重做时返回 `requires_new_plan`，不能静默退化为全量重规划。

### 5.2 稳定项目标识

锁和 Diff 不能依赖名称。为 `PlanItem` 增加向后兼容的 `item_id` 字段：普通一次性规划响应允许为空，进入生命周期并持久化 V1 前必须补齐。

- 初始项目 ID 由 `session_id + candidate_id + poi_id/name + original_date + occurrence` 的稳定摘要生成；
- 移动或重排保留原 `item_id`；
- 新增/替换项目使用 `session_id + request_id + operation_index` 生成稳定 ID；
- 同一 request ID 重放会得到同一个 ID 和同一个 Action Receipt。

### 5.3 锁语义

- `day` 锁保存整天的 `day_fingerprint`，日期内项目、顺序、时间和路线摘要都必须保持不变。
- `item` 锁保存项目的 `item_fingerprint`，项目存在性、所属日期、开始/结束时间、POI 和时长必须保持不变。
- `must_visit` 是系统保护约束，即使用户没有显式锁定也不能被删除；它仍可在不违反用户锁的前提下移动。
- 只有显式 `unlock_*` 动作能解除用户锁。LLM 解析出的编辑动作无权携带 unlock。
- Lock Guard 在任何外部 Tool 调用之前执行；Locality Guard 在 Preview 物化后再次比较锁定和未影响范围的指纹。

锁定 Day 2 后修改 Day 1，不是把 `locked=true` 传给 Prompt，而是由两个确定性守卫证明 Day 2 从输入到 Preview 完全一致。

## 6. 领域模型

新增模型建议放在 `src/travel_agent/domain/lifecycle_models.py`。

### 6.1 会话、版本与锁

```text
PlanSession
  session_id
  status
  intake_thread_id
  lifecycle_thread_id
  candidate_set_id
  active_version_id
  pending_preview_id
  session_revision
  created_at / updated_at

PlanVersion
  version_id              # session 内单调 V1/V2/V3
  parent_version_id
  source_request_id
  selected_candidate_id
  plan_artifact_id
  plan_fingerprint
  validation_summary
  critic_summary
  created_at

PlanLock
  lock_id
  kind                    # day | item
  target_id               # YYYY-MM-DD | item_id
  expected_fingerprint
  created_by_request_id
  created_at
```

选择/接受候选后才创建 V1。锁定和解锁只更新控制元数据并增加 `session_revision`，不虚构新的行程版本；只有批准过的日程内容变化才生成 V2/V3。Approval Token 同时绑定版本号和 session revision，因此锁变化会使旧 Preview 失效。

### 6.2 编辑、影响和 Preview

```text
PlanChangeRequest
  request_id
  expected_active_version_id
  expected_session_revision
  structured_action | edit_text

EditPatch
  operations[]            # 最多 3 个白名单动作
  parser_summary

EditOperation
  kind
  source_item_ref/id
  target_date
  anchor_item_ref/id
  poi_query
  user_reason

ImpactResult
  scope                   # item | day | multi_day | requires_new_plan
  affected_dates[]
  affected_item_ids[]
  preserved_dates[]
  invalidated_route_keys[]
  required_tool_operations[]
  lock_conflicts[]
  reasons[]

PlanPreview
  preview_id
  base_version_id
  base_session_revision
  plan_artifact_id
  impact
  hard_validation
  soft_evaluation
  diff
  approval_token_hash
  status                  # pending | approved | rejected | stale | invalid

PlanDiff
  from_version_id
  to_preview_id/version_id
  added_items[]
  removed_items[]
  moved_items[]
  reordered_items[]
  time_changes[]
  route_changes[]
  day_metric_changes[]
  trip_metric_changes
  validation_changes[]
  soft_quality_change
```

`ImpactResult` 是 v0.9 可复用的稳定领域接口，但 v0.8 只接受用户编辑事件。`PlanDiff` 只保存标准化差异，不保存 Provider 原始响应或完整 Prompt。

### 6.3 状态枚举

```text
planning
needs_requirement_clarification
awaiting_candidate_selection
active
needs_edit_clarification
building_preview
awaiting_change_approval
completed_with_rejection
requires_new_plan
failed_external
```

`active` 表示当前有可用版本且可以继续发起动作，不表示 Graph 永久结束。一次 Resume 只消费一个当前 Interrupt；每个用户动作完成后 Graph 再次停在下一处 `interrupt()`。

## 7. Graph State 与外部存储

### 7.1 最小 Lifecycle State

新增 `src/travel_agent/lifecycle/state.py`：

```text
PlanLifecycleState
  session_id
  lifecycle_thread_id
  status
  candidate_set_id
  active_version_id
  pending_preview_id
  session_revision
  current_interrupt_kind
  resume_value
  change_request
  edit_patch
  edit_grounding_issues
  clarification_round
  impact_result
  approval_decision
  llm_summaries[]
  tool_summaries[]
  transition_count
  terminal_reason
```

Graph State 只保存控制流数据、ID 和必要摘要。完整 Candidate Set、Plan Version、Preview、POI Facts、Route Results 与 Diff 存入 Repository，节点按 ID 加载。这避免随着版本增加把大型历史反复复制进 Prompt 和 Checkpoint。

### 7.2 Repository Protocol

```text
PlanRepository
  create_session(...)
  save_candidate_set(...)
  get_session(session_id)
  get_version(version_id)
  get_artifact(artifact_id)
  save_preview(...)
  mark_preview_rejected(...)
  update_locks(expected_revision, ...)
  commit_version(expected_active_version, expected_revision, ...)
  get_action_receipt(session_id, request_id)
```

提供：

- `InMemoryPlanRepository`：单元测试和默认全 Mock 演示；
- `SQLitePlanRepository`：单机重启恢复；
- `PlanRepository` Protocol：为 v1.2 的 PostgreSQL 留边界，不在 v0.8 实现多实例生产语义。

版本 Artifact 为不可变记录。Preview 被拒绝后只改变状态，不物理删除，以便轨迹审计。敏感原始用户文本和 Provider 原始响应不进入 Artifact。

### 7.3 Checkpoint 与 Repository 一致性

SQLite Repository 提交使用事务和 Compare-And-Swap。由于 LangGraph Checkpoint 与领域 Repository 不是同一个事务，所有有副作用节点必须具备幂等恢复能力：

1. 版本 ID、Preview ID 和新增 Item ID 从 request ID 稳定派生；
2. 先查询 `ActionReceipt`，已成功动作直接回放同一结果；
3. Repository 原子写入 Artifact、Active Pointer 和 Receipt；
4. Graph 恢复后即使节点重跑，也不会重复创建版本；
5. Checkpoint 与 Repository 不一致时，以 Repository Receipt/Active Pointer 为事实来源重新水合 State，并记录 reconciliation 日志。

这比假设 `interrupt()` 前节点绝不重跑更安全，也能覆盖进程在 Commit 后、Checkpoint 前崩溃的窗口。

## 8. Graph 设计

### 8.1 候选选择与 V1

```text
START
  → load_candidate_set
  → await_candidate_selection
       ↓ interrupt(kind=candidate_selection)
  → validate_selection_resume
       ├─ stale/invalid → domain conflict
       └─ valid
            → persist_plan_v1
            → prepare_active_interrupt
            → await_change_request
```

Interrupt Payload 包含候选 ID、风格、硬验证等级、确定性指标、Soft Critic 摘要、推荐候选和允许动作。它不包含原始 Evidence Digest 或 Provider 响应。

候选选择不再次调用 LLM；推荐结果来自 v0.7 Quality Gate，最终选择由用户显式决定。

### 8.2 锁定动作

```text
await_change_request
  ↓ resume(lock/unlock)
validate_resume
  → validate_lock_target
  → update_locks_with_revision
  → await_change_request
```

锁动作不触发 POI、Route 或 Critic 调用。重复同一 request ID 返回原 Action Receipt；expected revision 过期返回 409。

### 8.3 自然语言编辑与澄清

```text
await_change_request
  ↓ resume(edit_text)
validate_resume
  → parse_edit_patch                 # LLM structured output
  → ground_edit_entities             # deterministic
       ├─ unique → validate_edit_patch
       ├─ ambiguous/missing
       │    → prepare_edit_clarification
       │    → await_edit_clarification
       │         ↓ interrupt
       │    → apply_grounding_answer
       │    → ground_edit_entities
       └─ unsupported → requires_new_plan
```

编辑澄清最多 2 轮。澄清只补充项目引用、目标日期、顺序锚点或 POI 查询，不允许改写已解析的其他操作。结构化编辑动作跳过 LLM Parser，但仍执行相同的 Grounding、Impact 和安全守卫。

### 8.4 影响分析、局部重规划与审批

```text
validate_edit_patch
  → analyze_change_impact
       ├─ scope > 2 days / TripSpec change → mark_requires_new_plan
       └─ local
            → enforce_lock_guard
                 ├─ conflict → reject_before_tool_use
                 └─ allowed
                      → apply_change_to_local_draft
                      → build_delta_tool_plan
                           ├─ need POI → load_edit_pois
                           └─ no POI ──────────────┐
                      → collect_delta_routes       │
                           ├─ missing → load_delta_routes
                           └─ reuse ───────────────┤
                      → materialize_preview ◀──────┘
                      → validate_global_constraints
                           ├─ invalid → reject_preview
                           └─ valid
                                → prepare_critic_context
                                → soft_constraint_critic
                                → validate_critic_evidence
                                → quality_gate
                                → enforce_locality_guard
                                → build_plan_diff
                                → persist_pending_preview
                                → await_change_approval
                                     ↓ interrupt(kind=change_approval)
```

用户主动编辑不进入 v0.5 的多轮硬修复循环。第一版实现只允许一次确定性的局部排程物化；如果 Preview 硬非法，系统返回具体违规和可修改建议，但不自动删除其他项目或放宽硬约束。这样可以避免“用户要求移动 A，Agent 为了通过验证却悄悄删除 B”。

Soft Critic 只评价 Preview 与受影响日的标准化 Evidence。Critic 不可用时，可以把硬合法 Preview 标为 `critic_degraded` 并继续请求审批；POI/Route Tool 不可用则不能构造可信 Preview，返回 503 且 Active Version 不变。

### 8.5 批准、拒绝与继续交互

```text
await_change_approval
  ↓ resume(approve/reject)
validate_approval_resume
  ├─ reject
  │    → mark_preview_rejected
  │    → await_change_request
  └─ approve
       → verify_preview_base_and_revision
       → commit_new_version_cas
            ├─ conflict → 409, preview stale
            └─ success
                 → emit_version_committed
                 → await_change_request
```

批准提交时不重新调用 LLM 或地图工具，只验证 Token、Active Version、Session Revision、Preview 指纹和 Action Receipt。提交后的 V2 使用 Preview 的验证与 Critic 摘要，`PlanDiff.to_preview_id` 转换为正式 `to_version_id`。

### 8.6 循环预算

- 单次自然语言 Edit Parser：最多 2 次 Provider 尝试，由 Gateway 控制；
- 编辑实体澄清：默认 2 轮，最大 3 轮；
- 单次 Edit Patch：最多 3 个原子动作、2 个 affected days；
- Preview 局部物化：1 次，不进入隐式全量重规划；
- Grounding 重试与软修复：复用 v0.7 上限，v0.8 Preview 默认禁用自动软修复，只评价；
- 单会话版本数：默认 20；达到上限要求新建会话；
- Graph `transition_count` 和 recursion limit 提供最终兜底。

每次 Resume 只处理一个动作并再次暂停，因此用户可以长期交互，但任何单次执行都有明确预算。

## 9. Impact Analysis 与局部修改

### 9.1 确定性影响分析

`src/travel_agent/lifecycle/impact.py` 接收 Active Version、Grounded EditPatch 和 Lock Set，输出 `ImpactResult`。它不调用 LLM。

| 动作 | affected dates | 失效路线 | Tool Use |
|---|---|---|---|
| 同日 reorder | 当前日期 | 变化项目两侧邻接边 | 只查缺失 Route |
| 跨日 move | 来源日 + 目标日 | 两天变化邻接边 | 只查缺失 Route |
| remove | 来源日 | 被删项两侧邻接边 | 只查新连接 Route |
| add | 目标日 | 插入点两侧邻接边 | POI Search/Detail + Route |
| replace | 来源日 | 原项与替代项相关边 | POI Search/Detail + Route |

影响分析同时给出 `preserved_dates` 和 `required_tool_operations`。如果实体、日期或顺序锚点无法唯一解析，先进入澄清；不能让模糊引用扩大为整程重做。

### 9.2 复用 v0.5 Route Delta

继续复用 `planning.impact.collect_route_delta` 和 `day_fingerprint`：

1. 从 Active Version 复制受影响日期的 Candidate Draft；
2. 仅在局部 Draft 上执行白名单操作；
3. 根据修改前后邻接关系计算失效 Route Key；
4. 复用仍有效的 `RouteResult`，只加载缺失路线；
5. 物化完整 Preview 后检查 preserved day hash；
6. 任何未受影响日期变化都视为实现错误，不允许进入审批。

### 9.3 局部性不是质量偏好

局部性是安全边界而不是 Soft Critic 分数。即使 LLM 认为全量重排更顺畅，只要不在用户批准的 affected scope 内，就不能修改。Soft Critic 可以在 Diff 中说明修改造成的质量变化，但不能扩展 ImpactResult。

## 10. Edit Model Provider

新增独立 `EditModel` Protocol，不复用 Requirement Prompt，也不把 Critic Provider 直接当编辑器：

```text
EditModel.parse(EditModelInput) -> EditProviderOutput
```

Provider：

- `MockEditModel`：离线测试和 Benchmark；
- `DeepSeekEditModel`：通过 OpenAI-compatible client 接入；
- `OpenAIEditModel`：可选备用实现。

配置建议：

```text
EDIT_PROVIDER=mock|deepseek|openai
EDIT_MODEL=<model-name>
EDIT_TIMEOUT_SECONDS=20
EDIT_MAX_ATTEMPTS=2
EDIT_MAX_INPUT_CHARS=12000
EDIT_MAX_OUTPUT_TOKENS=1200
EDIT_CLARIFICATION_MAX_ROUNDS=2
PLAN_MAX_VERSIONS=20
PLAN_MAX_EDIT_OPERATIONS=3
PLAN_MAX_AFFECTED_DAYS=2
```

`EditModelInput` 只包含 TripSpec 摘要、当前版本的有界项目索引、锁摘要、允许动作和用户本轮编辑文本。计划内容使用明确的数据边界标签，防止 POI 名称或备注中的 Prompt Injection 被当成指令。Provider 输出必须 `extra="forbid"`，原始响应不进入 State 或日志。

若 `EDIT_PROVIDER` 与 `REQUIREMENT_PROVIDER` 都使用 DeepSeek，可以共享底层 `AsyncOpenAI` Client，但 Gateway、Prompt version、超时、重试、Token 统计和错误分类保持独立。

## 11. Hard Validator、Soft Critic 与版本提交边界

编辑 Preview 必须按以下顺序处理：

1. Edit Schema 和实体 Grounding；
2. Lock Guard 与 Impact Scope；
3. Tool 事实加载和局部物化；
4. 全局 Hard Validator；
5. Grounded Soft Critic 与 Evidence Guard；
6. Locality Guard；
7. Diff；
8. 用户 Approval；
9. CAS Commit。

关键语义：

- Hard Validator 返回 error：Preview 为 `invalid`，不创建版本，不伪装为 Tool 故障；
- Critic 不可用：硬合法 Preview 可以降级审批，但 Diff 明确 `critic_status=degraded`；
- Evidence Grounding 失败：沿用 v0.7 的有限重试/降级，不能让未验证评价进入说明；
- Locality Guard 失败：视为内部一致性错误，Preview 不可审批；
- Tool 失败：返回外部失败，Active Version 和 Pending Preview 都不被伪造成新版本；
- Commit 冲突：返回 409，并把旧 Preview 标为 stale。

## 12. API 设计

### 12.1 创建生命周期会话

```text
POST /api/v1/plan-sessions
POST /api/v1/plan-sessions/from-text
```

请求复用现有 `PlanningRequest` / `NaturalPlanningRequest`。响应统一为 `PlanSessionResponse`：

```json
{
  "session_id": "session-id",
  "status": "awaiting_candidate_selection",
  "active_version": null,
  "session_revision": 0,
  "candidate_summaries": [],
  "pending_preview": null,
  "allowed_actions": ["accept_recommendation", "select_candidate"],
  "interrupt": {
    "id": "interrupt-id",
    "payload": {"kind": "candidate_selection"}
  }
}
```

自然语言需求不完整时，`interrupt.payload.kind=requirement_clarification`，复用 v0.4 问题模型和 Patch 规则。规划完成后同一公开 session 自动转入候选选择，不要求客户端搬运完整 PlanningResponse。

### 12.2 恢复与动作

```text
POST /api/v1/plan-sessions/{session_id}/resume
```

请求使用 discriminated union：

```json
{
  "interrupt_id": "interrupt-id",
  "request_id": "uuid",
  "expected_active_version_id": "V1",
  "expected_session_revision": 3,
  "action": {
    "kind": "edit_text",
    "text": "把第二天下午的商场换成一个室内博物馆"
  }
}
```

审批请求额外包含 `preview_id` 和一次性 `approval_token`。Token 只返回给客户端，Repository 保存 Hash，不在日志中输出。

### 12.3 查询

```text
GET /api/v1/plan-sessions/{session_id}
GET /api/v1/plan-sessions/{session_id}/versions
GET /api/v1/plan-sessions/{session_id}/versions/{version_id}
GET /api/v1/plan-sessions/{session_id}/diff?from=V1&to=V2
```

查询接口只读取已提交版本。Pending Preview 仅在当前 Session 响应中返回，不能通过通用版本接口伪装成已提交计划。

### 12.4 HTTP 与领域语义

| 场景 | HTTP | 领域状态 |
|---|---:|---|
| 等待选择、澄清或审批 | 200 | 对应 `awaiting_*` / `needs_*` |
| Preview 硬非法 | 200 | `change_rejected` + violations |
| 编辑涉及 TripSpec 或范围过大 | 200 | `requires_new_plan` |
| 空文本、非法动作 Schema | 422 | request validation error |
| Session/Version 不存在 | 404 | not found |
| 旧 Interrupt、旧版本、旧 revision、旧 token | 409 | stale/conflict |
| LLM/Tool/Checkpoint/Repository 暂时不可用 | 503 | external unavailable |
| Locality Guard 或 Artifact 指纹异常 | 500 | internal consistency error |

`requires_new_plan` 是受支持范围外的业务结果；地图超时不能被包装成它。

## 13. 可观测性与日志

关键事件：

```text
lifecycle.session.created
lifecycle.node.started/completed
selection.interrupted/resumed/committed
lock.changed
edit.parse.started/completed/failed
edit.grounding.completed
edit.clarification.interrupted/resumed/exhausted
impact.analyzed
lock_guard.rejected
edit.local_draft.applied
edit.routes.invalidated/reused/loaded
preview.hard_validated
preview.soft_evaluated
locality_guard.completed/failed
diff.generated
approval.interrupted/resumed
preview.rejected/stale
version.committed
lifecycle.action.replayed
lifecycle.state.reconciled
```

公共关联字段：`session_id`、`lifecycle_thread_id`、`run_id`、`request_id`、`active_version_id`、`preview_id`、Node、状态和耗时。编辑事件记录动作类型、影响日期数量、Route reuse/load 数量和终止原因，不记录：

- 用户原始编辑文本；
- LLM 原始输入/输出；
- API Key、approval token；
- 完整计划正文或 Repository JSON。

Checkpoint 可检查完整控制轨迹，Repository 可检查不可变版本和结构化 Diff，两者通过 ID 关联。

## 14. 测试设计

### 14.1 单元测试

- Item ID、Day/Item Fingerprint 和 Plan Fingerprint 稳定性；
- 动作 Schema、最多 3 操作与最多 2 affected days；
- Entity Grounding 的唯一、缺失、同名歧义和日期限定；
- Lock Guard 与 Locality Guard；
- move/reorder/remove/add/replace 的 ImpactResult；
- Route Delta、保留日期和锁定项 Hash；
- PlanDiff 的新增、删除、移动、时间、路线和指标差异；
- Repository CAS、Action Receipt、Preview stale 和版本上限。

### 14.2 Provider Contract 测试

- Mock/DeepSeek/OpenAI 的 `EditProviderOutput` Schema 一致；
- `extra` 字段、未知动作、unlock、TripSpec 修改和不存在的 item ID 被拒绝；
- Timeout、429、5xx、非法 JSON、Schema 错误和有限重试；
- Prompt Injection 字符串只作为数据，不改变动作白名单；
- Token、模型、Prompt version 和 latency 摘要正确，日志无原文。

### 14.3 Graph 轨迹测试

至少覆盖：

1. 推荐候选 → 选择中断 → 接受 → V1；
2. 选择非推荐但硬合法候选 → V1；
3. 锁定 Day 2 → 修改 Day 1 → Day 2 Hash 不变；
4. 修改锁定项目 → Tool 调用前被拒绝；
5. 自然语言同名项目歧义 → 澄清 Interrupt → Resume → 唯一 Grounding；
6. move 跨两天 → 只查询新增 Route Key → Hard Validate → Soft Critic → Diff → Approval → V2；
7. reject Preview → Active Version 仍为 V1；
8. Hard Validator 拒绝 Preview → 不进入 Approval、不创建 V2；
9. Critic 不可用 → 硬合法 Preview 降级审批；
10. Route Tool 不可用 → 503，Active Version 不变；
11. 旧 Interrupt / 并发 Resume / 旧 revision → 409；
12. 重放同一 request ID → 相同结果且只有一个版本；
13. Commit 后、Checkpoint 前模拟崩溃 → Receipt 重放并完成状态对账；
14. SQLite 模式重启 → 恢复待选择、待澄清和待审批任务；
15. 超过 affected-day、操作数、版本数或澄清轮次 → 有界终止。

轨迹断言不仅检查最终 JSON，还检查节点顺序、Interrupt kind、工具调用次数、失效/复用 Route Key、Validator/Critic 执行、版本父子关系和 Active Pointer。

### 14.4 API 与安全测试

- 404/409/422/503 映射；
- 不能跨 Session 读取 Version、提交 Preview 或使用 Approval Token；
- Approval Token 只可使用一次；
- 客户端提交的 `locked=true`、validation、metrics 或 Diff 不被信任；
- 日志、异常和 OpenAPI 示例不泄露 Key、原始 Prompt 或 Token。

## 15. Benchmark 与消融实验

新增：

```text
evals/lifecycle/base_sessions.json
evals/lifecycle/edit_cases.jsonl
evals/lifecycle/expected_traces.jsonl
scripts/evaluate_plan_lifecycle.py
```

首批至少 15 条离线 Fixture，覆盖候选选择、锁定、同日重排、跨日移动、删除、替换、新增、歧义澄清、硬非法、锁冲突、范围过大、Tool/LLM 故障、审批拒绝、重复请求和重启恢复。

核心指标：

| 指标 | 定义 | v0.8 门禁 |
|---|---|---:|
| Edit Intent Exact Match | 原子动作、实体和目标均正确 | Mock 100% |
| Entity Grounding Accuracy | 可唯一解析 Fixture 的正确引用率 | 100% |
| Impact Exact Match | affected dates/items/routes 完全匹配 | 100% |
| Locked Artifact Preservation | 锁定日期/项目指纹保持率 | 100% |
| Unaffected Day Preservation | preserved day hash 保持率 | 100% |
| Hard Constraint Regression | 已提交版本新增 error 的比例 | 0% |
| Diff Exact Match | 结构化差异与预期完全匹配 | 100% |
| Commit Correctness | approve 提交、reject 不提交 | 100% |
| Idempotent Replay | 重复 request 不产生额外副作用 | 100% |
| Bounded Termination | 所有 Fixture 在预算内结束/暂停 | 100% |
| Route Reuse Rate | 复用路线 / 所需路线总数 | 报告实测，不硬编码 |

真实 DeepSeek/OpenAI 结果按 Provider、模型、Prompt version、数据集版本和日期单独报告，不能用 Mock 100% 宣称线上语义理解达到 100%。

消融至少比较：

1. `local_impact` vs `full_regeneration`：比较未受影响日期保持率、Route 调用数和延迟；
2. `with_lock_guard` vs `without_lock_guard`：仅在离线影子执行中比较锁定破坏风险，不能让无 Guard 路径进入产品运行时；
3. `structured_action` vs `natural_language_edit`：区分确定性执行误差和 LLM 解析误差；
4. `with_soft_critic` vs `without_soft_critic`：比较 Preview 软质量判断和 Token/延迟开销，Hard Gate 始终保留。

## 16. 计划代码结构

```text
src/travel_agent/
  domain/
    lifecycle_models.py
  lifecycle/
    state.py
    workflow.py
    service.py
    repository.py
    sqlite_repository.py
    actions.py
    grounding.py
    impact.py
    locks.py
    diff.py
    fingerprints.py
    errors.py
  edits/
    models.py
    protocols.py
    gateway.py
    prompts.py
    providers/
      mock.py
      deepseek.py
      openai.py
  api/
    lifecycle_routes.py

tests/
  test_lifecycle_models.py
  test_lifecycle_repository.py
  test_edit_grounding.py
  test_edit_gateway.py
  test_lifecycle_impact.py
  test_plan_diff.py
  test_lifecycle_workflow.py
  test_lifecycle_api.py
  test_lifecycle_restart.py
  test_lifecycle_benchmark.py

evals/lifecycle/
scripts/evaluate_plan_lifecycle.py
docs/v0.8/README.md
```

需要修改的现有文件：

- `domain/models.py`：增加向后兼容 `PlanItem.item_id`；
- `planning/impact.py`：提取可复用的邻接边与指纹纯函数；
- `graph/workflow.py`：暴露生成 Lifecycle Snapshot 所需的标准化输出，不加入生命周期循环；
- `requirements/models.py`：会话入口保存交互模式或关联 intake ID；
- `runtime.py`：装配 Edit Gateway、Plan Repository、Lifecycle Workflow 与关闭资源；
- `config.py`、`.env.example`：新增 Edit/Lifecycle 配置；
- `api/routes.py` 或新路由模块：注册 Session 接口和错误映射；
- Checkpoint serializer allowlist：加入生命周期强类型模型；
- `README.md` 和路线图：实现完成后更新当前版本、运行命令和实测报告。

## 17. 分阶段实施计划

### Phase A：版本与持久化骨架

- 实现 Item ID、Fingerprints、PlanSession/Version/Lock/Preview/Diff 模型；
- 实现 InMemory/SQLite Repository、CAS 和 Action Receipt；
- 完成 Repository 单元测试和重启测试；
- 此阶段不接 LLM，不修改规划算法。

验收：V1/V2 线性版本、revision、幂等重放和 stale preview 行为稳定。

### Phase B：结构化选择、锁定和审批 Graph

- 建立 Lifecycle State/Workflow/Service；
- 完成候选选择 Interrupt、V1、锁动作、Preview Approval、V2 Commit；
- 接入 API 和 Checkpointer；
- 先用结构化 `move/reorder/remove` 打通完整轨迹。

验收：服务重启可恢复待选择/待审批；锁定项和 Active Version 不被失败动作污染。

### Phase C：Impact、Tool Delta 与验证链

- 接入 add/replace 的 POI Tool 和全部动作的 Route Delta；
- 复用 Hard Validator、Soft Critic、Evidence Guard 与 Locality Guard；
- 实现 PlanDiff 和 Preview 响应；
- 完成 Tool/LLM 故障与局部性轨迹测试。

验收：局部 Tool 调用可证明，所有已提交版本硬合法，Diff 可审计。

### Phase D：自然语言编辑与澄清

- 实现 EditModel Protocol、Mock/DeepSeek/OpenAI、Gateway 和 Prompt；
- 实现确定性 Entity Grounding 与最多两轮编辑澄清；
- 加入 Prompt Injection、Schema 错误、Provider 超时和安全日志测试。

验收：自然语言与结构化动作走同一安全执行路径，LLM 不能绕过锁和 Validator。

### Phase E：Benchmark、文档与发布门禁

- 固化至少 15 条 Fixture、预期轨迹和消融脚本；
- 跑全量 pytest、coverage、compileall、pip check；
- 保存全 Mock 基线和可选 DeepSeek 实测报告；
- 更新 `docs/v0.8/README.md`、主 README、版本号和演示命令。

验收：达到第 15 节门禁，文档中的数字全部来自实际报告，不预填虚假结果。

## 18. Definition of Done

v0.8 只有同时满足以下条件才算完成：

1. Graph 中可见候选选择、编辑解析、影响分析、工具增量、验证、审批和版本提交节点。
2. 用户可选择候选形成 V1，并可通过批准过的局部修改形成 V2。
3. 锁定日期/项目和未受影响日期保持率在离线 Fixture 中为 100%。
4. 所有已提交版本通过 Hard Validator；无硬约束回归。
5. Tool/Provider 失败、硬不可行、范围外请求和并发冲突保持不同语义。
6. 重复 request ID 不产生重复版本，旧 Interrupt/Version/Revision 返回 409。
7. SQLite 模式在重启后可恢复待选择、待澄清和待审批任务。
8. PlanDiff 能准确说明新增、删除、移动、重排、时间、路线和质量变化。
9. 关键日志可通过 session/run/request/version/preview ID 串起完整轨迹，且不泄露敏感正文。
10. Benchmark、消融、命令和 README 与实际实现一致。

## 19. 面试演示脚本

主演示：

1. 用户以自然语言生成杭州三日游，Agent 经 v0.7 评价推荐 balanced 候选；
2. Graph 在候选选择处 Interrupt，用户接受推荐并形成 V1；
3. 用户锁定 Day 2，再说“把第一天下午的可选景点挪到第三天上午”；
4. Edit Model 只输出结构化意图，Impact Analyzer 判定只影响 Day 1/3；
5. Route Delta 复用原路线，只补查变化邻接边；
6. Preview 重新通过 Hard Validator 和 Soft Critic，Diff 展示移动、时间和路线变化；
7. 用户批准形成 V2；Checkpoint 与 Repository 证明 Day 2 Hash 完全不变。

失败恢复演示：

- 尝试修改锁定 Day 2：Lock Guard 在 Tool 调用前拒绝；
- 路线 Provider 超时：API 返回 503，V1 仍是 Active Version；
- 用户拒绝 Preview：不创建 V2；
- 两个客户端同时批准：第一个 CAS 成功，第二个因旧版本/revision 返回 409；
- 服务在待审批时重启：沿相同 session 恢复 Interrupt 并继续提交。

这组演示直接回答面试中的核心问题：LLM 在哪里参与、哪些边界由代码保证、用户反馈如何进入 Graph、为什么是局部重规划、失败为什么不会污染当前计划，以及这些结论如何通过 Trace 和 Benchmark 证明。

## 20. 对后续版本的接口承诺

v0.8 应为 v0.9 留下两个稳定接口：

- `ImpactAnalyzer.analyze(active_version, change_source, locks) -> ImpactResult`；
- `PlanLifecycleService.build_and_commit_preview(...)` 的受控本地变更入口。

v0.9 的天气 `ChangeEvent` 可以复用 ImpactResult、Lock Guard、Local Replan、Validator、Diff 和 PlanVersion，但必须增加事件 Fingerprint、事实新鲜度和用户锁冲突审批。v0.8 不提前实现天气分支，也不把未来事件字段塞入当前 Prompt。

## 21. 最终取舍

v0.8 仍采用“单编排 Graph + LLM 语义节点 + 确定性服务 + 真实工具”的结构。Selection、Impact、Validator、Diff 和 Repository 没有独立目标或推理循环，不应包装成多个 Agent。只有后续消融证明某个职责需要独立上下文、决策预算和闭环时，才考虑 Subgraph 或多 Agent。

本版本完成后，项目会从“能生成并评价旅行计划”前进到“能与用户围绕计划持续协作、解释影响、等待批准并安全地产生新版本”，这正是 v1.0 前最关键的 HITL Agent 能力。
