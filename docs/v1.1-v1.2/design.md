# v1.1 → v1.2 最终形态统一设计报告

> 文档状态：已冻结，可进入实现  
> 设计日期：2026-08-24  
> 当前基线：v1.0.0  
> 最终发布目标：v1.2.0  
> 配套文档：[需求追踪矩阵](requirements-traceability.md)

## 1. 设计结论

本轮采用“一次连续开发、两个内部能力门禁、一次最终发布”的方式：

1. v1.1 Gate 完成 Preference Memory、上下文裁剪、跨会话个性化，以及进程内 Specialist Subagent 的上下文隔离实验。
2. v1.2 Gate 完成 Travel MCP Server、真实备用 Provider、完整 Agent 交互前端和生产化部署。
3. 两个 Gate 都通过后，将项目统一发布为 v1.2.0；不要求在中途对外发布 v1.1.0，但必须保留独立迁移、测试报告和可回滚提交点。

最终系统仍然是一个 Travel Planning Agent。它由一个确定性的 Orchestrator 持有全局状态和终止权，Planner、Critic、Replanner 作为进程内 Specialist Subagent 获得职责专属上下文。Subagent 之间不自由聊天，只通过强类型 Handoff 交换结构化结果。

多 Agent 的目标不是并行，也不是标签，而是：

- 隔离 Planner、Critic、Replanner 的模型上下文；
- 限制每个 Specialist 可读取的 Memory 和可调用的工具；
- 减少完整 State、原始历史和无关证据进入 Prompt；
- 分别计量调用、Token、延迟和失败；
- 通过 single_graph 与 specialist_subagents 消融证明收益。

如果消融实验不能证明质量、上下文成本或故障隔离收益，生产默认值保持 single_graph；Context Projection、强类型 Handoff 和专属预算仍保留，因为这些机制本身有价值。

## 2. 版本目标与边界

### 2.1 v1.1 必须完成

- 结构化 Preference Memory，而不是保存完整聊天记录。
- 用户命名空间、租户隔离、来源、置信度、确认、过期、撤销和版本审计。
- 当前显式约束优先于历史偏好，未确认推断不得成为硬约束。
- Token 感知的 Context Projection 和裁剪。
- 跨会话检索、冲突检测、用户确认、修正、删除、导出和关闭个性化。
- Orchestrator + Planner/Critic/Replanner Specialist 的进程内实验路径。
- Memory 与 Subagent 两组消融及可复现报告。

### 2.2 v1.2 必须完成

- API、MCP、后台任务共用同一 Application Service。
- Travel MCP Server 同时覆盖旅行用例工具和受限的数据工具。
- 地图与天气均有真实备用 Provider，Mock 只用于测试。
- 完整前端覆盖输入、澄清、候选、地图、审批、Diff、天气、Memory 和 Trace。
- PostgreSQL、Redis、多实例幂等、身份认证、租户隔离和生产 Checkpoint。
- 异步 Run、SSE 事件、后台天气刷新和可取消任务。
- OpenTelemetry、结构化日志、Metrics、告警、容器、迁移、备份、恢复和回滚。
- REST/MCP/Worker/Frontend 的端到端一致性验证。

### 2.3 必须保持的 v1.0 不变量

- Hard Validator 和 Grounding Gate 不可绕过。
- 显式用户锁、affected_days 和局部性守卫不可削弱。
- Tool 失败、数据不足和业务不可行继续使用不同终止语义。
- 所有 Loop 继续受统一 ExecutionBudget 和 Deadline 限制。
- Trace 继续使用安全属性白名单，不保存 Secret、完整 Prompt 或 Memory 正文。
- Plan Version 只追加，审批前不得提交有副作用的新版本。
- 全 Mock 模式和 v1.0 的 120-case 发布工作流必须继续可复现。

### 2.4 非目标

- OTA 库存、机票酒店下单和支付。
- 分布式自主 Agent、Agent 间自由文本协商和独立微服务部署。
- 为每个算法或工具机械创建 Agent。
- 运营后台、营销页面和与 Agent 演示无关的复杂前端。
- 用 LLM 替代预算、时间窗、路线、权限、幂等和硬约束计算。

## 3. 最终总体架构

~~~mermaid
flowchart TB
    Web["React Web / AMap JS"] --> REST["FastAPI REST Adapter"]
    Client["MCP Client"] --> MCP["Travel MCP Adapter"]
    Scheduler["Weather/Event Worker"] --> Worker["Background Adapter"]

    REST --> App["TravelApplicationService"]
    MCP --> App
    Worker --> App

    App --> Runs["RunCoordinator"]
    Runs --> Orch["Travel Orchestrator Graph"]

    Orch --> Memory["Memory Context Subgraph"]
    Orch --> Planner["Planner Specialist"]
    Orch --> Hard["Deterministic Optimizer + Hard Validator"]
    Orch --> Critic["Critic Specialist"]
    Orch --> Replan["Replanner Specialist"]
    Orch --> Life["Lifecycle / Weather Subgraph"]

    Planner --> Data["Tool Reliability Gateway"]
    Hard --> Data
    Replan --> Data
    Life --> Data

    Data --> MapChain["Map Provider Chain: AMap → Baidu"]
    Data --> WeatherChain["Weather Provider Chain: AMap → QWeather"]

    App --> PG["PostgreSQL: Domain + Checkpoint + Memory + Run"]
    App --> Redis["Redis: Cache + Rate Limit + Lock + Event Coordination"]
    Runs --> OTEL["Trace / Metrics / Logs / OpenTelemetry"]
~~~

架构形态保持模块化单体：

- API、MCP、Worker 可以是不同进程，但复用同一 Python application/domain 包。
- Graph、领域规则、Provider Gateway 和 Repository 不在适配层复制。
- MCP 不是 Graph 内部的网络跳转；内部节点直接调用 Tool Gateway。
- PostgreSQL 是持久事实源，Redis 只承担可丢失的缓存和短期协调。

## 4. Orchestrator 与 Specialist Subagent

### 4.1 职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Orchestrator | 全局 State、路由、预算、Checkpoint、HITL、终止 | 生成长文本计划 |
| Planner Specialist | 高层骨架、候选风格、偏好映射 | 硬约束裁决、版本提交 |
| Critic Specialist | 软约束评价、体验风险、结构化建议 | 判断硬约束、调用写工具 |
| Replanner Specialist | 根据影响范围产生 PlanPatch | 修改 locked 内容、直接提交 |
| Deterministic Services | 优化、预算、时间窗、Validator、Diff、权限 | 语义偏好推断 |

### 4.2 Context Projection

单一 TravelState 不再直接作为 LLM 输入。每个 Specialist 只能收到白名单上下文：

~~~python
class PlannerContext(BaseModel):
    trip_spec: TripSpec
    confirmed_preferences: list[PreferenceSummary]
    current_request_overrides: list[Constraint]
    poi_candidates: list[POISummary]
    context_manifest: ContextManifest

class CriticContext(BaseModel):
    trip_spec: TripSpec
    candidate: PlanCandidateSummary
    evidence: EvidenceBundle
    relevant_preferences: list[PreferenceSummary]
    context_manifest: ContextManifest

class ReplannerContext(BaseModel):
    candidate: PlanCandidateSummary
    violations: list[Violation]
    critique: CritiqueResult | None
    affected_days: set[int]
    locked_days: set[int]
    locked_items: set[str]
    allowed_tools: list[str]
    context_manifest: ContextManifest
~~~

ContextManifest 至少记录：

- context_id、agent_role 和 policy_version；
- 进入上下文的 Memory ID、证据 ID 和摘要哈希；
- 被排除条目的数量和原因；
- 估算 Token、字符数和预算上限；
- 当前请求覆盖了哪些历史偏好；
- 不记录完整 Memory 正文和完整 Prompt。

### 4.3 强类型 Handoff

~~~python
class AgentHandoff(BaseModel):
    handoff_id: str
    parent_run_id: str
    from_role: AgentRole
    to_role: AgentRole
    reason: HandoffReason
    input_ref: str
    expected_output_schema: str
    budget_slice: AgentBudget
    idempotency_key: str

class SpecialistResult(BaseModel):
    handoff_id: str
    status: SpecialistStatus
    output_ref: str | None
    decision_summary: str
    evidence_ids: list[str]
    usage: AgentUsage
    error: SpecialistError | None
~~~

规则：

- Specialist 不能直接修改主 State，只返回候选、CritiqueResult 或 PlanPatch。
- Orchestrator 在 Schema、权限和不变量校验后才把结果归并回 State。
- Handoff 不包含完整聊天历史。
- Specialist 失败返回结构化错误，由 Orchestrator 决定重试、降级到单 Graph 节点或终止。
- 每个 Handoff 都有独立 Deadline、LLM/Tool/Token 上限，但总消耗仍计入全局 ExecutionBudget。

### 4.4 Graph 路径

~~~mermaid
flowchart TD
    Start --> Identity["resolve_identity"]
    Identity --> Parse["requirement_subgraph"]
    Parse --> Missing{"missing or conflict?"}
    Missing -->|yes| Clarify["interrupt: clarification"]
    Clarify --> Parse
    Missing -->|no| Retrieve["retrieve_relevant_preferences"]
    Retrieve --> Compose["compose_context_manifest"]
    Compose --> Mode{"agent mode"}

    Mode -->|single_graph| PlanNode["planning nodes"]
    Mode -->|specialist_subagents| PlanAgent["invoke_planner_specialist"]

    PlanNode --> Optimize["deterministic optimization"]
    PlanAgent --> Optimize
    Optimize --> Hard["hard_validator"]
    Hard --> HardPass{"hard pass?"}
    HardPass -->|no| ReplanMode{"replan mode"}
    ReplanMode -->|node| RepairNode["local repair nodes"]
    ReplanMode -->|subagent| RepairAgent["invoke_replanner_specialist"]
    RepairNode --> Optimize
    RepairAgent --> Merge["validate PlanPatch"]
    Merge --> Optimize

    HardPass -->|yes| CriticMode{"critic mode"}
    CriticMode -->|node| CriticNode["critic node"]
    CriticMode -->|subagent| CriticAgent["invoke_critic_specialist"]
    CriticNode --> SoftGate["quality and grounding gate"]
    CriticAgent --> SoftGate
    SoftGate -->|repair| ReplanMode
    SoftGate -->|pass/degraded| Select["interrupt: candidate selection"]
    Select --> Explain["grounded explanation"]
    Explain --> Propose["propose_memory_updates"]
    Propose --> Confirm{"confirmation needed?"}
    Confirm -->|yes| MemoryInterrupt["interrupt: memory confirmation"]
    MemoryInterrupt --> Persist["persist plan and confirmed memory"]
    Confirm -->|no| Persist
    Persist --> End
~~~

### 4.5 AgentMode 与降级

~~~text
AGENT_MODE=single_graph
AGENT_MODE=specialist_subagents
AGENT_MODE=shadow_subagents
~~~

- single_graph：正式 Baseline，使用 Context Projection，但不创建 Specialist Handoff。
- specialist_subagents：Specialist 结果进入正式决策链。
- shadow_subagents：主链仍使用 single_graph，同时运行 Specialist 并记录对比，不影响用户结果。
- Subagent 故障不会自动切换到 Mock LLM；可恢复故障才允许按策略回到对应单 Graph 节点。
- 任何模式都不能绕过 Hard Validator、Grounding Gate 或审批。

### 4.6 Subagent 晋级门禁

specialist_subagents 成为生产默认值必须同时满足：

1. 硬约束满足率、Grounding 和局部性不低于 single_graph。
2. 错误个性化率不增加。
3. 至少满足以下一项：
   - Prompt 输入 Token 中位数下降不低于 20%；
   - Preference Match 或软质量通过率提高不低于 5 个百分点；
   - Specialist 故障被局部隔离，完整 Run 恢复率提高不低于 10 个百分点。
4. P95 延迟和估算成本增幅均不超过 20%，否则需要 ADR 解释收益。
5. Handoff Schema 合法率和越权阻断率均为 100%。

未满足时，最终产品仍发布 v1.2.0，但默认 AGENT_MODE=single_graph。

## 5. v1.1：Preference Memory 与上下文治理

### 5.1 Memory 模型

~~~python
class PreferenceMemory(BaseModel):
    memory_id: str
    tenant_id: str
    user_id: str
    category: PreferenceCategory
    value: PreferenceValue
    scope: PreferenceScope
    source: MemorySource
    source_run_id: str | None
    confidence: float
    confirmation_status: ConfirmationStatus
    revision: int
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    content_hash: str
~~~

首批类别：

- pace；
- preferred_categories；
- avoided_categories；
- walking_tolerance；
- preferred_transport；
- food_preferences；
- schedule_preferences；
- accessibility_needs；
- budget_style。

精确住宿地址、证件、联系方式和支付信息不得成为 Preference Memory。

### 5.2 优先级

优先级固定为：

~~~text
当前请求显式约束
  > 当前计划中用户已确认的选择
  > 已确认且未撤销、未过期的长期偏好
  > 系统默认值
~~~

补充规则：

- 当前请求覆盖历史，但不默认删除历史；记录 MemoryConflict。
- 用户显式要求“以后都这样”时可以生成待确认更新。
- 模型推断和单次操作只能进入 MemoryProposal。
- accessibility_needs 等敏感约束只能由用户明确确认。
- 低置信度候选只能用于澄清问题，不能用于硬过滤。

### 5.3 Memory 生命周期

~~~text
retrieve
  → rank
  → compose context
  → record usage
  → propose update
  → policy validate
  → user confirm / reject
  → persist revision
  → inspect / correct / revoke / delete / export
~~~

MemoryPolicy 使用确定性代码负责：

- Namespace 和所有权校验；
- Schema、范围、TTL 和敏感类别校验；
- content_hash 去重；
- 乐观锁 revision；
- 冲突检测；
- 可审计的覆盖和撤销；
- 删除任务与相关缓存失效。

LLM 只生成结构化 MemoryProposal，不能直接调用 Repository 写入。

### 5.4 检索与裁剪

候选排序使用确定性特征：

~~~text
score =
  explicit_relevance
  + confirmation_weight
  + freshness_weight
  + confidence_weight
  + category_priority
  - conflict_penalty
  - sensitivity_penalty
~~~

检索流程：

1. 使用 tenant_id + user_id 强制过滤。
2. 排除 revoked、expired 和未获授权类别。
3. 按当前 TripSpec 和目标 Specialist 计算相关性。
4. 先保留当前显式约束，再加入确认 Memory。
5. 达到 Token/字符/条目预算后停止。
6. 将 selected IDs、excluded counts、原因和估算 Token 写入 ContextManifest。

State 只保存：

~~~python
class MemoryStateSlice(TypedDict):
    namespace_ref: str
    selected_memory_ids: list[str]
    preference_summaries: list[PreferenceSummary]
    conflicts: list[MemoryConflict]
    context_manifest_id: str
    pending_proposal_ids: list[str]
~~~

完整 Memory、Embedding、原始会话和大型历史保存在 State 外。首个版本使用结构化过滤和字段匹配，不为展示而强行引入向量数据库；只有数据规模和检索评测证明需要时再增加 pgvector。

### 5.5 跨会话行为

标准演示链：

1. Session A：用户明确表示偏好轻松节奏、每天步行不超过 6 公里，并授权保存。
2. Session B：新建另一城市旅行，系统自动应用已确认偏好并减少重复澄清。
3. Session C：用户本次明确要求特种兵行程，当前请求覆盖长期轻松偏好。
4. Session D：用户撤销轻松偏好，之后的 Run 不再读取。
5. 另一个 user_id 的 Run 不能看到任何上述 Memory ID 或内容。

### 5.6 Memory API

~~~text
GET    /api/v1/preferences
POST   /api/v1/preferences/proposals/{proposal_id}/confirm
POST   /api/v1/preferences/proposals/{proposal_id}/reject
PATCH  /api/v1/preferences/{memory_id}
DELETE /api/v1/preferences/{memory_id}
POST   /api/v1/preferences/clear
GET    /api/v1/preferences/export
PATCH  /api/v1/profile/personalization
~~~

user_id 和 tenant_id 只能从认证主体取得，禁止相信请求体中的所有权字段。

### 5.7 Memory Trace

新增事件：

~~~text
memory.namespace_resolved
memory.retrieve_started
memory.retrieve_completed
memory.conflict_detected
context.composed
agent.handoff_started
agent.handoff_completed
agent.handoff_rejected
memory.proposal_created
memory.confirmation_required
memory.persisted
memory.revoked
~~~

Trace 只记录 Memory ID、类别、数量、原因码、策略版本和摘要哈希。

### 5.8 最终领域契约补全

“实现此前文档的全部功能点”还要求补齐当前 v1.0 为简化演示而没有完整表达的输入输出字段。它们必须在 Application Service 和前端冻结前完成。

TripSpec 增加：

- traveler_groups：成人、儿童、老人、关系和数量，只保存规划所需信息；
- transport_preferences：地铁、公交、步行、驾车和打车偏好；
- accessibility：轮椅、台阶、休息频率和其他明确可执行限制；
- daily_availability：每天不同的开始、结束和不可用时间窗；
- daily_budget：可选的每日预算和总预算关系；
- accommodation_status：confirmed、area_only 或 unknown。

Plan 输出补齐：

- 活动、用餐、休息和交通四类 PlanItem；
- 每日主题、区域、已知费用、未知费用、交通时间、距离和体力指标；
- 营业时间、天气、预算和无障碍检查结果；
- 每个关键事实的来源、获取时间、新鲜度、置信度、缓存和降级状态；
- 候选评分分解、风险、推荐理由和结构化 Evidence ID；
- 不可行时返回 ConstraintConflict 和可选择的 RelaxationOption，不生成伪合法计划；
- accommodation_status=unknown 时可以推荐住宿区域，但不声明库存、价格或可预订性。

用户选择、删除、替换、反复拒绝和显式反馈进入 FeedbackEvent。FeedbackEvent 可以触发 MemoryProposal，但仍必须经过 MemoryPolicy 和用户确认。

## 6. Application Service 重构

当前 PlanningRuntime 同时承担依赖装配、用例入口和资源生命周期。v1.2 前将其拆为：

~~~text
RuntimeContainer
  ├─ DependencyFactory
  ├─ TravelApplicationService
  ├─ PreferenceApplicationService
  ├─ RunQueryService
  ├─ ProviderHealthService
  └─ BackgroundEventService
~~~

核心用例：

~~~python
class TravelApplicationService(Protocol):
    async def create_trip(...)
    async def start_run(...)
    async def resume_run(...)
    async def cancel_run(...)
    async def select_candidate(...)
    async def propose_plan_change(...)
    async def approve_plan_change(...)
    async def refresh_weather(...)
    async def get_plan(...)
    async def get_plan_diff(...)
    async def replay_trace(...)
~~~

适配层只完成：

- 身份和权限解析；
- 请求/响应 Schema 转换；
- Idempotency-Key 和 Correlation ID；
- Application Error 到 REST/MCP 错误的映射；
- 不包含 Graph 路由、Provider 选择和领域规则。

## 7. v1.2：Travel MCP Server

### 7.1 协议与运输

- 固定并记录 MCP 协议版本，升级必须经过 Contract Test。
- 本地开发和桌面客户端支持 stdio。
- 远程部署使用 Streamable HTTP 的单一 MCP Endpoint。
- 应用状态使用显式 run_id、thread_id、plan_id 和 memory_id，不依赖隐藏的传输 Session。
- HTTP 部署校验 Origin、认证主体、租户、Mcp-Method 和 Mcp-Name。

### 7.2 两类 Tool

为统一早期与后期文档，Travel MCP Server 暴露两类能力，但都不复制业务逻辑。

旅行用例工具：

~~~text
create_travel_plan
resume_travel_run
cancel_travel_run
select_plan_candidate
apply_plan_change
approve_plan_change
get_plan_diff
replay_execution_trace
get_or_update_preferences
~~~

受限的数据工具：

~~~text
search_poi
get_poi_detail
geocode
reverse_geocode
get_route
get_travel_time_matrix
get_weather
get_weather_warning
~~~

数据工具调用同一 Tool Gateway，只授予 read:data scope；正式公网环境可以关闭低层数据工具，避免绕开 Agent 用例预算。

### 7.3 MCP Resource

~~~text
travel://plans/{plan_id}
travel://plans/{plan_id}/versions/{version}
travel://plans/{plan_id}/diff
travel://runs/{run_id}
travel://runs/{run_id}/trace
travel://users/me/preferences
~~~

Resource 由 Query Service 生成只读结构化快照，支持 ETag/版本哈希和 TTL；不直接暴露数据库对象、原始 Provider 响应或完整 Prompt。

### 7.4 HITL 与长任务

- create_travel_plan 返回显式 RunHandle。
- running 状态由客户端通过 Resource 或任务查询获取。
- awaiting_input 返回 interrupt_id、input_schema、公开问题和 expires_at。
- resume_travel_run 必须携带 run_id、interrupt_id、输入和 Idempotency-Key。
- MCP Client 断开不会取消业务 Run；显式 cancel 才取消。
- 重复 resume、approve 和 apply 操作返回第一次提交的领域结果。

### 7.5 错误语义

~~~python
class ApplicationError(BaseModel):
    code: ErrorCode
    category: ErrorCategory
    retryable: bool
    message: str
    details: dict[str, JsonValue]
    run_id: str | None
    trace_id: str
~~~

至少区分：

- invalid_input；
- authentication_failed；
- forbidden；
- not_found；
- conflict；
- input_required；
- budget_exhausted；
- provider_unavailable；
- data_insufficient；
- infeasible；
- internal_error。

REST 与 MCP 必须对相同 ApplicationError 保持同一 code、retryable 和领域 details。

## 8. Provider Chain 与真实 Failover

### 8.1 Provider 组合

| 能力 | 首选 | 备用 | Mock |
|---|---|---|---|
| POI、地理编码、路线、矩阵 | AMap | Baidu Map Web API | 仅离线测试 |
| 天气预报、预警 | AMap Weather | QWeather | 仅离线测试 |

选择依据：

- 项目主要面向中国城市；
- 百度官方 Web API 覆盖地点检索、地理编码、逆地理编码、路线和批量算路；
- 和风天气可以提供按经纬度的多日预报及预警能力；
- 两类备用都能映射到现有领域模型，而不要求上层感知供应商字段。

### 8.2 Provider Protocol

~~~python
class MapProvider(Protocol):
    async def search_pois(...)
    async def get_poi_detail(...)
    async def geocode(...)
    async def reverse_geocode(...)
    async def get_route(...)
    async def get_travel_time_matrix(...)

class WeatherProvider(Protocol):
    async def resolve_location(...)
    async def get_forecast(...)
    async def get_warnings(...)
~~~

Provider 返回统一 Domain Result：

- data；
- provider；
- source_timestamp；
- fetched_at；
- freshness；
- confidence；
- coordinate_reference；
- attribution；
- cost_units；
- degradation；
- raw_response_ref。

原始响应保存到短期脱敏 Blob 或 Fixture，State 只保存标准化结果和引用。

### 8.3 坐标与归一化

- 领域坐标显式携带 coordinate_reference。
- AMap 的 GCJ-02 作为中国境内规划和前端展示的标准坐标。
- Baidu 返回值进入领域前由确定性转换器转成 GCJ-02，同时保留原始坐标哈希和转换版本。
- Cache Key 包含 Provider、坐标系、转换版本、交通方式、时间段和请求参数。
- 不允许混用 BD-09、GCJ-02 后直接计算路线或距离。

### 8.4 故障分类与切换

| 结果 | 是否重试当前 Provider | 是否切备用 |
|---|---:|---:|
| timeout、连接失败、5xx | 是，有界 | 是 |
| 429、暂时限流 | 按 Retry-After | 是 |
| Schema 漂移、缺关键字段 | 否 | 是并告警 |
| 401/403、Key 配置错误 | 否 | 否，立即失败 |
| 参数错误 | 否 | 否 |
| 合法空结果 | 否 | 仅当操作策略允许 coverage fallback |
| 业务不可行 | 否 | 否 |

coverage fallback 即使在备用源找到数据，也要记录首选源为 no_data，不能伪造成首选调用成功。

### 8.5 Provider Trace

~~~text
provider.attempt_started
provider.attempt_completed
provider.retry_scheduled
provider.fallback_selected
provider.fallback_completed
provider.degraded_to_cache
provider.degraded_to_estimate
provider.chain_exhausted
~~~

记录 Provider 名称、操作、原因码、延迟、缓存、新鲜度、成本单位和结果来源；参数只记录白名单摘要。

## 9. 完整 Agent 前端

### 9.1 技术选择

- React + TypeScript + Vite。
- TanStack Query 管理服务端状态。
- AMap JS API 展示 POI、路线和日程。
- SSE 展示 Run 公开事件，普通 REST 完成命令和查询。
- Playwright 完成关键 E2E。

前端不得包含服务端 AMap Web Service Key、Baidu AK、QWeather 凭证或 LLM Key。AMap JS 使用独立、域名受限的浏览器凭证，与服务端 Key 完全分离。

### 9.2 页面与能力

| 页面 | 必须能力 |
|---|---|
| 新建旅行 | 自然语言输入、结构化约束预览、缺失项提示 |
| Run 工作台 | Node/Tool/Agent 时间线、预算、Interrupt、错误和降级 |
| 候选对比 | Relaxed/Balanced/Exploration 指标与地图对比 |
| 计划详情 | 分日日程、路线、来源、置信度和天气 |
| 编辑审批 | 锁定、局部修改、Plan Diff、批准、拒绝、版本回退 |
| Weather | 事件、影响日期、局部变化高亮和刷新 |
| Preferences | 查看、确认、修正、撤销、删除、清空和关闭个性化 |
| Trace Replay | 按 Node/Tool/Agent/Decision 过滤和回放 |

前端只展示公开决策摘要，不展示 Chain-of-Thought、完整 Prompt 或内部原始响应。

### 9.3 异步 Run 与 SSE

~~~text
POST /api/v1/trips
POST /api/v1/trips/{trip_id}/runs       → 202 + RunHandle
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events       → SSE
POST /api/v1/runs/{run_id}/resume
POST /api/v1/runs/{run_id}/cancel
~~~

SSE 事件来自持久化 Trace/Event Store，而不是进程内队列，因此断线后可以使用 Last-Event-ID 续传。

## 10. 生产化数据与运行模型

### 10.1 PostgreSQL

至少新增或迁移：

~~~text
users
tenants
trips
agent_runs
trace_events
checkpoints
plan_sessions
plan_versions
plan_items
preference_memories
memory_proposals
memory_conflicts
memory_usage
idempotency_records
provider_attempts
external_events
outbox_events
audit_logs
~~~

要求：

- Alembic 管理 Schema；
- tenant_id + user_id 进入所有用户数据唯一约束和索引；
- Plan Version、Memory Revision、Trace 和 Audit 只追加；
- Resume、Approve、Memory Confirm 使用事务和乐观锁；
- Outbox 保证数据库提交与后台事件投递一致。

### 10.2 Redis

Redis 用于：

- Provider Cache；
- 分布式限流；
- Run/Plan/Memory 短期锁；
- Worker 协调和去重；
- SSE 热事件加速。

Redis 故障时：

- 持久事实不丢失；
- 查询退回 PostgreSQL；
- 缓存和实时推送降级；
- 需要分布式互斥的写命令 fail closed，不在不安全状态继续。

### 10.3 身份与租户

- 本地开发可使用显式 Dev Identity，但必须由配置开启。
- 生产使用 OIDC/OAuth2 JWT 验证。
- user_id、tenant_id、scope 从验证后的 Principal 获得。
- API 和 MCP 共用 Authorizer。
- 资源读取、Run Resume、Plan Approve 和 Memory 更新都验证所有权。
- 精确位置、Memory 和 Trace 按最小必要原则保存，并支持导出和删除。

### 10.4 Worker

后台 Worker 只处理确定的 Application Command：

- 执行异步 Run；
- 对活跃计划按策略刷新天气；
- 规范化外部事件；
- 触发 Impact Analysis；
- 生成待审批 Plan Change；
- 执行 TTL、删除和审计清理任务。

Worker 不能绕过 Application Service 直接改 Plan 或 Memory。

### 10.5 部署单元

~~~text
web
api
worker
mcp
postgres
redis
otel-collector
reverse-proxy
~~~

本地和演示环境使用 Docker Compose。生产说明提供：

- 健康检查与 readiness；
- 数据库迁移顺序；
- Secret 注入；
- 灰度和回滚；
- PostgreSQL 备份恢复演练；
- Redis 丢失演练；
- Provider/LLM 故障演练；
- 多实例 Resume、审批和 Memory 幂等演练。

## 11. 可观测性

### 11.1 统一关联

所有日志、Trace 和 Metrics 使用：

~~~text
trace_id
run_id
thread_id
trip_id
plan_id
tenant_id_hash
user_id_hash
agent_role
handoff_id
node_name
tool_name
provider
~~~

不得记录原始 Token、Secret、完整住址、完整 Memory、完整 Prompt 和未经脱敏的外部响应。

### 11.2 Metrics

- Run completed/degraded/failed/awaiting_input；
- Node、Specialist、Tool 和 Provider P50/P95；
- LLM/Tool/Provider 调用与 Token/费用；
- Memory hit、override、conflict、revoke 和错误个性化；
- Context Token 与裁剪率；
- Handoff 成功、Schema 拒绝和越权阻断；
- Provider retry、fallback、cache 和 chain exhausted；
- SSE 连接、续传和丢事件；
- 幂等命中、锁冲突和版本冲突。

## 12. 代码结构目标

~~~text
src/travel_agent/
├─ application/
│  ├─ travel_service.py
│  ├─ preference_service.py
│  ├─ run_query_service.py
│  ├─ provider_health_service.py
│  └─ errors.py
├─ agents/
│  ├─ contracts.py
│  ├─ context.py
│  ├─ orchestrator.py
│  ├─ planner.py
│  ├─ critic.py
│  └─ replanner.py
├─ memory/
│  ├─ models.py
│  ├─ policy.py
│  ├─ retrieval.py
│  ├─ context.py
│  ├─ repository.py
│  └─ workflow.py
├─ integrations/
│  ├─ map/amap.py
│  ├─ map/baidu.py
│  ├─ weather/amap.py
│  ├─ weather/qweather.py
│  └─ mcp/
├─ infrastructure/
│  ├─ database/
│  ├─ cache/
│  ├─ checkpoint/
│  ├─ identity/
│  ├─ events/
│  └─ observability/
├─ api/
├─ execution/
├─ graph/
├─ lifecycle/
├─ planning/
├─ requirements/
├─ tools/
└─ weather/

frontend/
mcp_server/
migrations/
deploy/
evals/v1_1/
evals/v1_2/
tests/unit/
tests/integration/
tests/contract/
tests/trajectory/
tests/e2e/
~~~

现有模块不做一次性大搬家。先抽 Protocol 和 Application Service，再逐步移动实现，并保留兼容 import，避免重构与功能开发同时造成大范围回归。

## 13. 一次开发的实施顺序

### Phase A：v1.0 基线冻结

- 记录当前全量测试、覆盖率和 v1.0 报告。
- 为 RunCoordinator、Trace、Plan Repository 和 Graph State 建立兼容测试。
- 冻结 ApplicationError、TraceEvent 和领域结果的版本。

完成门禁：v1.0 的 462 个已通过测试和 120-case 发布工作流不回退。

### Phase B：Identity、PostgreSQL 与 Repository

- 引入 Principal、tenant_id、user_id 和 Authorizer。
- 建立 Alembic、PostgreSQL Repository 与 Checkpointer。
- 为 Run、Plan、Memory、Idempotency 和 Trace 建表。
- 完成 TripSpec、Plan、Provenance、ConstraintConflict 和 RelaxationOption 的兼容迁移。
- Memory/SQLite 继续作为 dev/test backend。

完成门禁：同一 Repository Contract 在 Memory、SQLite、PostgreSQL 后端通过。

### Phase C：v1.1 Memory Domain

- 实现 PreferenceMemory、Proposal、Conflict、Policy 和 Repository。
- 实现确认、拒绝、修正、撤销、删除、导出和关闭个性化。
- 加入所有权、TTL、去重和乐观锁。

完成门禁：跨用户隔离、当前请求覆盖、撤销和重复确认全部通过。

### Phase D：Context Projection 与 Memory Graph

- 实现 Planner/Critic/Replanner Context。
- 实现 Memory 检索、排序、裁剪和 ContextManifest。
- 将完整 State 从 LLM Gateway 输入中移除。
- 接入 Memory Trace 和 Token 计量。

完成门禁：bounded_context 消融相对 full_history 显著降低上下文，且安全指标不回退。

### Phase E：Specialist Subagent

- 实现 Handoff、AgentBudget、Planner/Critic/Replanner Specialist。
- 加入 single_graph、shadow_subagents、specialist_subagents 三种模式。
- 先 Shadow 运行，再按晋级门禁决定默认值。

完成门禁：Schema、权限、预算和故障隔离轨迹全部可验证；是否默认启用由消融结果决定。

### Phase F：Application Service 与异步 Run

- 从 PlanningRuntime 抽取用例服务和依赖容器。
- 增加异步 Run、取消、SSE 持久事件和 Last-Event-ID。
- API 旧端点保留一个兼容周期。

完成门禁：同步旧接口和异步新接口产生一致领域结果。

### Phase G：Provider Chain

- 扩展地图和天气 Protocol。
- 实现 Baidu Map 与 QWeather。
- 实现 ProviderPolicy、Circuit Breaker、Fallback、坐标转换和归因。
- 完成真实/Fixture Contract Test。

完成门禁：故障分类准确、合法空结果不伪装、Mock 不进入生产 Failover。

### Phase H：MCP

- 实现 stdio 和 Streamable HTTP Adapter。
- 暴露旅行用例工具、数据工具和 Resource。
- 加入认证、权限、幂等、错误映射和 Contract Test。

完成门禁：REST 与 MCP 对相同用例的领域结果和错误语义一致。

### Phase I：完整前端与 Worker

- 完成八类页面和 Agent 时间线。
- 接入 AMap JS、SSE、Interrupt/Resume、Diff、Memory。
- 实现天气刷新 Worker、Outbox 和待审批变化。

完成门禁：关键用户旅程的 Playwright E2E 全部通过。

### Phase J：生产化与最终发布

- 完成 Compose、反向代理、OTEL、限流、健康检查和 Secret。
- 演练迁移、备份恢复、Redis 丢失、多实例并发和回滚。
- 运行 v1.0 回归、v1.1 消融和 v1.2 E2E。
- 更新 README、使用指南、部署指南、学习指南和面试材料。

完成门禁：本报告第 15 节全部满足，发布 v1.2.0。

## 14. 测试与评测

### 14.1 测试矩阵

| 层级 | 核心内容 |
|---|---|
| Unit | Memory Policy、排序、TTL、冲突、Context Projection、坐标转换、错误分类 |
| Property | 用户隔离、序列化、幂等、锁不变量、Context 上限、Plan Patch 不修改锁定项 |
| Contract | Repository、Provider、LLM、REST/MCP Schema、SSE Event |
| Trajectory | Memory、Handoff、Tool、Failover、Interrupt、Resume、终止路径 |
| Integration | PostgreSQL、Redis、Checkpointer、Outbox、Worker、多实例 |
| E2E | Web + API + Worker + MCP + Provider Fixture |
| Benchmark | v1.0 回归、Memory 消融、Subagent 消融、Provider 故障、软评测 |

### 14.2 v1.1 数据集

至少 60 条多会话 Scenario Chain，每条包含 2 至 4 个 Session，覆盖：

- 明确保存；
- 未确认推断；
- 重复行为；
- 当前请求覆盖；
- 冲突偏好；
- 过期和撤销；
- 禁用个性化；
- 跨用户和跨租户；
- 敏感偏好；
- Prompt Injection 型外部数据；
- Planner/Critic/Replanner 不同上下文；
- Context Budget 边界。

强制消融：

~~~text
with_memory vs without_memory
bounded_context vs full_history
confirmed_memory_only vs inferred_memory
single_graph vs specialist_subagents
~~~

### 14.3 v1.1 指标门禁

- 跨用户、跨租户隔离：100%。
- 当前显式约束覆盖历史：100%。
- 撤销、删除和关闭个性化生效：100%。
- 未确认推断作为硬约束：0。
- 错误个性化率：不高于 without_memory。
- with_memory 偏好命中率相对 without_memory 提升至少 15 个百分点。
- 重复澄清次数相对 without_memory 下降至少 30%。
- bounded_context 的输入 Token 中位数相对 full_history 下降至少 40%。
- Hard Constraint、Grounding 和局部性指标不得回退。
- Subagent 默认值使用第 4.6 节的独立晋级门禁。

### 14.4 v1.2 门禁

- 所有 Provider 操作覆盖 success、empty、timeout、429、5xx、auth、schema drift。
- Failover 分类准确率 100%，外部失败误判为 infeasible 为 0。
- API/MCP ApplicationError code、retryable 和领域结果一致率 100%。
- Run Resume、Plan Approve、Memory Confirm 在重复调用和多实例下幂等。
- SSE 断线续传不丢已持久化事件。
- 前端完成创建、澄清、候选、审批、局部编辑、天气、Memory、Trace 八条 E2E。
- PostgreSQL 备份恢复、迁移回滚和 Redis 丢失演练有可复现记录。
- 全量 Branch Coverage 不低于 90%。
- v1.0 的 120-case 实际工作流门禁继续通过。

### 14.5 LLM 软评测

LLM-as-Judge 只评估：

- 兴趣匹配；
- 节奏合理性；
- 解释清晰度；
- 偏好应用是否自然；
- 方案可理解性。

规则：

- 硬约束、安全、权限、幂等和 Grounding 仍由确定性 Evaluator 裁决。
- Judge 输入使用脱敏结构化计划，不提供被测系统的内部实现标签。
- 固定 Judge Provider、模型、Prompt 版本、温度、日期和数据集版本。
- 报告 Judge 失败率、一致性样本和人工复核样本。
- Mock Judge 只验证评测管道，不能声明真实软质量收益。

## 15. 最终 Definition of Done

只有同时满足以下条件才能称为“项目完整形态”：

1. v1.0 所有 Agent 核心能力和发布门禁无回退。
2. Memory 可控、可解释、可撤销、跨会话有效且跨用户隔离。
3. 每个 Specialist 只读取专属 Context，Handoff、预算和 Trace 可审计。
4. Subagent 是否默认启用由消融数据决定，而不是设计偏好决定。
5. API、MCP 和 Worker 复用同一 Application Service。
6. 地图和天气都有真实备用 Provider，来源和降级完整可追溯。
7. 完整前端能够演示从需求到计划、修改、天气、Memory 和 Trace 的闭环。
8. PostgreSQL、Redis、Checkpoint、幂等、身份和多实例路径通过测试。
9. 生产部署、迁移、备份恢复和回滚经过演练。
10. Benchmark、消融和 LLM 软评测可离线或按显式 Live 配置复现。
11. README、配置示例、API/MCP 使用、部署、学习和面试文档与实现一致。
12. 日志覆盖关键方法、Node、Edge、Handoff、Tool、Provider、Retry、Interrupt、Resume 和终止原因。

## 16. 关键风险与缓解

| 风险 | 缓解 |
|---|---|
| Memory 误用导致错误个性化 | 只自动应用已确认偏好，当前请求优先，提供撤销与消融 |
| Subagent 只增加复杂度 | Shadow 模式和晋级门禁，single_graph 始终可用 |
| TravelState 继续膨胀 | State Slice、Context Projection、大对象外置和引用化 |
| Provider 坐标不一致 | 坐标系显式建模、版本化转换和 Contract Test |
| 合法空结果被伪装成故障恢复 | NO_DATA 独立语义，coverage fallback 单独记录 |
| MCP 形成第二套业务逻辑 | Adapter 只调用 Application Service，做一致性测试 |
| 前端变成项目主角 | 页面只服务 Agent 闭环和可观察性 |
| 多实例重复审批或写 Memory | Idempotency、唯一约束、事务、乐观锁和 fail closed |
| Trace 泄露隐私 | 属性白名单、哈希身份、正文外置和保留期 |
| 一次开发范围过大 | 两个内部 Gate、十个 Phase、每阶段可回滚 |

## 17. ADR 结论

### ADR-01：使用进程内 Specialist，而不是分布式多 Agent

接受。原因是上下文隔离和职责权限有明确收益，而独立服务、消息协商和分布式状态没有当前业务必要性。

### ADR-02：Orchestrator 保持唯一终止权

接受。Specialist 不能提交 Plan、Memory 或改变锁定项，避免多写入者破坏状态一致性。

### ADR-03：Context Projection 是必选，Subagent 默认值是实验结论

接受。即使 Subagent 未晋级，专属上下文也能减少污染并改善可测性。

### ADR-04：MCP 同时提供用例工具和受限数据工具

接受。用例工具满足最终产品集成，数据工具覆盖早期架构目标；二者分别授权并复用 Application Service/Tool Gateway。

### ADR-05：最终仍采用模块化单体

接受。API、MCP、Worker 可独立进程扩缩容，但共享同一代码和数据库契约，不拆业务微服务。

## 18. 最终演示脚本

1. 用户登录后输入带父母、预算和到离站时间的杭州三日需求。
2. 系统读取已确认的轻松节奏与步行偏好，并展示使用原因。
3. Requirement Graph 因住宿区域缺失而 Interrupt，用户补充后 Resume。
4. Planner 使用专属 Context 生成三个候选，展示 ContextManifest 和 Token。
5. 地图首选 Provider 注入超时，切换备用 Provider，Trace 展示来源和坐标转换。
6. Hard Validator 拒绝营业时间冲突，Replanner 只修复受影响日期。
7. Critic 使用不含完整历史的证据上下文完成软评价。
8. 用户选择 Balanced 方案并保存 V1。
9. 用户删除 Day 2 博物馆、锁定其他天，审批 V2 Diff。
10. Worker 模拟暴雨事件，生成只影响 Day 2 的待审批 V3。
11. 用户确认“以后减少博物馆”，系统创建并确认 Memory Proposal。
12. 新 Session 展示跨会话个性化、Memory 管理和 Trace Replay。
13. 使用 MCP Client 读取同一计划和 Trace，证明 REST/MCP 结果一致。
14. 展示 Memory、Subagent、Provider 和完整系统的 Benchmark/消融报告。

## 19. 规范与 Provider 参考

- [MCP 传输规范](https://modelcontextprotocol.io/specification/draft/basic/transports)：本地 stdio、远程 Streamable HTTP。
- [MCP 2026-07-28 变更说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)：无隐藏协议 Session、显式请求元数据和长任务相关变化。
- [高德路径规划 API](https://lbs.amap.com/api/webservice/guide/api/direction/)。
- [百度 Web 服务 API 能力目录](https://lbsyun.baidu.com/faq/api?title=webapi)。
- [和风天气每日预报 API](https://dev.qweather.com/docs/api/weather/weather-daily-forecast/)。
- [项目总体架构](../travel-agent-architecture.md)。
- [v0.6 → v1.2 路线](../roadmap-to-v1.2.md)。
- [v1.0 设计](../v1.0/design.md)。
