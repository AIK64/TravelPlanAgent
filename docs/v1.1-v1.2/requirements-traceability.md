# v1.1 → v1.2 需求追踪矩阵

> 目的：把项目总体架构、历史版本路线、v1.0 接口承诺和本轮最终设计映射到可实现、可测试的交付项。  
> 使用方式：开发过程中每个 PR 必须引用至少一个 Requirement ID；发布前逐行关闭。  
> 设计基线：[统一设计报告](design.md)

## 1. 状态定义

| 状态 | 含义 |
|---|---|
| DONE-V1.0 | 已在 v1.0 实现，本轮只做回归和生产适配 |
| V1.1 | v1.1 内部门禁前必须实现 |
| V1.2 | 最终 v1.2.0 发布前必须实现 |
| EXPERIMENT | 必须实现实验和报告，但是否默认启用由数据决定 |
| CONDITIONAL | 仅在证据达到门槛后进入正式路径 |
| OUT | 明确不属于最终版本 |

> 实现快照（2026-08-24）：本表“状态”表示冻结设计要求在哪个 Gate 交付，不等于当前已完成标记。当前已落地 Memory、Context Projection、Specialist Handoff、Application Service、异步 Run/SSE、Baidu/QWeather Failover、MCP、核心 React 界面、PostgreSQL Repository、Redis Queue/Worker 和 Compose 适配。尚未关闭的生产强化项见 [实现指南第 11 节](implementation.md#11-与冻结设计矩阵的剩余差距)，发布验收时仍需逐行核销。

## 2. Agent 核心能力

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| AG-001 | 中文自然语言解析为强类型 TripSpec | DONE-V1.0 | requirements/ + Requirement Graph | 既有单元、轨迹、Benchmark 回归 |
| AG-002 | 缺失或冲突信息时 Interrupt/Resume | DONE-V1.0 | Requirement Checkpoint | 重启恢复、重复 Resume 幂等 |
| AG-003 | 生成 Relaxed/Balanced/Exploration 多候选 | DONE-V1.0 | planning/ + graph/ | 三候选、差异性和排序回归 |
| AG-004 | 路线优化、时间窗和预算计算由确定性代码负责 | DONE-V1.0 | optimization.py、validator.py | Hard Constraint 100% 阻断 |
| AG-005 | Soft Critic 只评价软约束 | DONE-V1.0 | critique/ | 软评测与 Grounding 回归 |
| AG-006 | Plan → Validate/Critic → Replan 有界循环 | DONE-V1.0 | Planning Graph | 循环、预算、终止原因轨迹 |
| AG-007 | 局部重规划保持 locked_days/locked_items | DONE-V1.0 | planning/repair + lifecycle/ | Replanning Locality 和 Hash 守卫 |
| AG-008 | 基于结构化证据生成解释 | DONE-V1.0 | critique/grounding | Data Grounding 与拒绝路径 |
| AG-009 | 用户选择、编辑、审批、版本与 Diff | DONE-V1.0 | lifecycle/ | Plan V1/V2、Diff、幂等 |
| AG-010 | 天气事件影响分析与局部变化 | DONE-V1.0 | weather/ + lifecycle/ | Weather Benchmark 与轨迹 |
| AG-011 | 全局 ExecutionBudget、Deadline 和有界终止 | DONE-V1.0 | execution/ | 预算耗尽和故障注入 |
| AG-012 | Node/Edge/Tool/Decision Trace 可回放 | DONE-V1.0 | execution/tracing | Trace 完整性门禁 |
| AG-013 | Planner/Critic/Replanner 使用专属 Context Projection | V1.1 | agents/context.py | ContextManifest 与输入白名单测试 |
| AG-014 | Orchestrator 是唯一状态归并和终止主体 | V1.1 | agents/orchestrator.py | 越权 Handoff 100% 拒绝 |
| AG-015 | Planner Specialist 进程内隔离运行 | EXPERIMENT | agents/planner.py | single/subagent/shadow 消融 |
| AG-016 | Critic Specialist 进程内隔离运行 | EXPERIMENT | agents/critic.py | Context Token、质量、故障隔离 |
| AG-017 | Replanner Specialist 只返回 PlanPatch | EXPERIMENT | agents/replanner.py | locked item 不变量、Patch Schema |
| AG-018 | Specialist 使用强类型 Handoff 和子预算 | V1.1 | agents/contracts.py、execution/ | Schema、权限、预算轨迹 |
| AG-019 | 多 Agent 是否默认由数据门禁决定 | CONDITIONAL | AGENT_MODE 配置 | 晋级报告和 ADR |
| AG-020 | Hard Validator 与 Soft Critic 的投机并行实验 | CONDITIONAL | Graph 并行分支 | 仅在延迟收益大于无效 Critic 成本时启用 |
| AG-021 | 互不依赖的 Provider 查询有界并发 | V1.2 | Tool Gateway | 并发上限、Deadline 和顺序无关性 |

### 2.1 最终输入输出契约

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| DOM-001 | TripSpec 表达成人、儿童、老人、关系和人数 | V1.2 | traveler_groups | Schema 与解析 Benchmark |
| DOM-002 | TripSpec 表达到达、离开和住宿状态 | DONE-V1.0 | TransportAnchor/LocationAnchor | 回归并增加 accommodation_status |
| DOM-003 | TripSpec 表达每天不同的可用时间窗 | V1.2 | daily_availability | 跨日时间窗属性测试 |
| DOM-004 | TripSpec 表达总预算、每日预算和已知/未知费用 | V1.2 | BudgetSpec | 预算不变量测试 |
| DOM-005 | TripSpec 表达交通偏好和无障碍限制 | V1.2 | transport_preferences/accessibility | 解析、规划和 Validator 测试 |
| DOM-006 | 输出活动、用餐、休息和交通 PlanItem | DONE-V1.0 | ItemType | 最终 E2E 覆盖四类 |
| DOM-007 | 输出每日主题、区域、时刻、距离、预算和体力指标 | DONE-V1.0 | DayPlan/PlanMetrics | Schema 回归 |
| DOM-008 | 输出营业时间、天气、预算和无障碍检查 | V1.2 | ValidationSummary | 前端/API/MCP 一致性 |
| DOM-009 | 输出来源、获取时间、新鲜度、置信度、缓存和降级 | V1.2 | Provenance | Provider 与 Plan Contract |
| DOM-010 | 输出候选评分分解、风险和证据化推荐理由 | V1.2 | CandidateScoreBreakdown | Grounding 与前端 E2E |
| DOM-011 | 无解时输出 ConstraintConflict 和 RelaxationOption | V1.2 | InfeasibleResult | 不安全交付为 0 |
| DOM-012 | 未确定住宿时只推荐区域，不声明库存或成交价 | V1.2 | AccommodationSuggestion | Grounding 与声明边界测试 |
| DOM-013 | 用户选择、编辑和显式反馈形成 FeedbackEvent | V1.1 | FeedbackEvent | Lifecycle/Memory 轨迹 |
| DOM-014 | Feedback 只能触发 Proposal，不能直接写 Memory | V1.1 | MemoryPolicy | 写权限测试 |

## 3. Preference Memory 与上下文

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| MEM-001 | PreferenceMemory 使用结构化 Schema | V1.1 | memory/models.py | Schema、序列化和迁移测试 |
| MEM-002 | 包含租户、用户、类别、值、来源、置信度和确认 | V1.1 | preference_memories 表 | Repository Contract |
| MEM-003 | 支持 TTL、撤销、revision 和审计 | V1.1 | memory/policy.py | 时间、乐观锁、审计测试 |
| MEM-004 | 当前显式请求优先于历史偏好 | V1.1 | ContextComposer | 覆盖率 100% |
| MEM-005 | 未确认推断不能作为硬约束 | V1.1 | MemoryPolicy | 错误硬应用为 0 |
| MEM-006 | LLM 只能提出 Proposal，不能直接写 Memory | V1.1 | memory/workflow.py | Repository 写权限测试 |
| MEM-007 | 冲突不静默合并，生成 MemoryConflict | V1.1 | memory/policy.py | 冲突轨迹和用户可见结果 |
| MEM-008 | 支持确认、拒绝、修正、撤销和删除 | V1.1 | PreferenceApplicationService | API、MCP 和 Repository 测试 |
| MEM-009 | 支持清空、导出和关闭个性化 | V1.1 | Preference API | E2E 和删除任务验证 |
| MEM-010 | 跨会话检索并减少重复澄清 | V1.1 | Memory Retrieval Node | 60 条多会话 Scenario Chain |
| MEM-011 | 跨用户与跨租户严格隔离 | V1.1 | Authorizer + Repository | 隔离率 100% |
| MEM-012 | 大型历史、Embedding 和正文不进入 State | V1.1 | MemoryStateSlice | State 快照 Schema 测试 |
| MEM-013 | 按相关性、确认、新鲜度、置信度和预算裁剪 | V1.1 | memory/retrieval.py | bounded/full_history 消融 |
| MEM-014 | 记录 Memory 进入上下文的原因和影响决策 | V1.1 | ContextManifest | Trace 轨迹断言 |
| MEM-015 | 不保存精确住宿、证件、支付等敏感信息 | V1.1 | SensitiveMemoryPolicy | 拒绝和脱敏测试 |
| MEM-016 | with_memory vs without_memory 消融 | V1.1 | evals/v1_1 | 偏好命中和错误个性化报告 |
| MEM-017 | bounded_context vs full_history 消融 | V1.1 | evals/v1_1 | Token、延迟和质量报告 |
| MEM-018 | confirmed_only vs inferred_memory 消融 | V1.1 | evals/v1_1 | 安全和质量报告 |
| MEM-019 | single_graph vs specialist_subagents 消融 | V1.1 | evals/v1_1 | 晋级门禁报告 |
| MEM-020 | 向量检索仅在结构化检索不足时引入 | CONDITIONAL | ADR + pgvector | 检索 Benchmark 证明必要性 |

## 4. Tool、Provider 与数据可信

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| TOOL-001 | search_poi | DONE-V1.0 | AMap/Mock Provider | Contract 回归 |
| TOOL-002 | get_route，支持步行与驾车 | DONE-V1.0 | RouteProvider | Contract 回归 |
| TOOL-003 | get_weather | DONE-V1.0 | AMapWeatherProvider | Contract 回归 |
| TOOL-004 | get_poi_detail | V1.2 | MapProvider 扩展 | AMap/Baidu Contract |
| TOOL-005 | geocode | V1.2 | MapProvider 扩展 | AMap/Baidu Contract |
| TOOL-006 | reverse_geocode | V1.2 | MapProvider 扩展 | AMap/Baidu Contract |
| TOOL-007 | get_travel_time_matrix | V1.2 | 原生批量或有界路线聚合 | 精度、预算和部分失败测试 |
| TOOL-008 | get_weather_warning | V1.2 | WeatherProvider 扩展 | AMap/QWeather Contract |
| TOOL-009 | Tool 输入输出 Schema、单位和范围 | DONE-V1.0 | domain/tool_models | Schema 回归并扩展 |
| TOOL-010 | Timeout、Retry、Backoff、Cache 和 Trace | DONE-V1.0 | ToolGateway | 故障注入回归 |
| TOOL-011 | Circuit Breaker | V1.2 | ProviderChain | 打开、半开、恢复测试 |
| TOOL-012 | 地图真实备用 Provider | V1.2 | BaiduMapProvider | Live Smoke + Fixture Contract |
| TOOL-013 | 天气真实备用 Provider | V1.2 | QWeatherProvider | Live Smoke + Fixture Contract |
| TOOL-014 | Mock 不得作为生产 Fallback | V1.2 | 配置校验 | Production Config 拒绝测试 |
| TOOL-015 | Failover 仅针对允许恢复的技术故障 | V1.2 | ProviderPolicy | 分类准确率 100% |
| TOOL-016 | 合法空结果不伪装为首选 Provider 成功 | V1.2 | NO_DATA 语义 | 轨迹和结果来源测试 |
| TOOL-017 | 原始 Provider 响应不进入 State/Prompt | V1.2 | Normalizer + raw_response_ref | State 和 Prompt 快照测试 |
| TOOL-018 | Provider、时间、新鲜度、归因和成本可追踪 | V1.2 | ProviderResult | Trace Contract |
| TOOL-019 | BD-09/GCJ-02 显式转换 | V1.2 | CoordinateNormalizer | 已知坐标 Fixture 与属性测试 |
| TOOL-020 | 缓存 Key 包含影响结果的全部维度 | V1.2 | Redis Cache | Key 冲突和失效测试 |
| TOOL-021 | 数据状态区分已确认、估算、低置信度和待确认 | V1.2 | Provenance View | API/MCP/Web 一致性 |

## 5. MCP 与 Application Service

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| MCP-001 | MCP 是适配层，不复制 Graph 或领域逻辑 | V1.2 | mcp_server/ | Architecture Test |
| MCP-002 | API、MCP、Worker 共用 TravelApplicationService | V1.2 | application/ | 同用例 Spy/Contract |
| MCP-003 | 支持本地 stdio | V1.2 | MCP Transport | Inspector/Contract |
| MCP-004 | 支持远程 Streamable HTTP | V1.2 | /mcp | Origin/Auth/并发测试 |
| MCP-005 | 应用状态使用显式 Run/Plan/Memory Handle | V1.2 | MCP Schema | 无隐藏 Session 的恢复测试 |
| MCP-006 | create_travel_plan | V1.2 | 用例 Tool | REST/MCP parity |
| MCP-007 | resume/cancel travel run | V1.2 | 用例 Tool | HITL、取消、幂等 |
| MCP-008 | select_plan_candidate | V1.2 | 用例 Tool | 选择和所有权测试 |
| MCP-009 | apply/approve plan change | V1.2 | 用例 Tool | Diff、审批、幂等 |
| MCP-010 | get_plan_diff/replay_execution_trace | V1.2 | 用例 Tool/Resource | 只读一致性 |
| MCP-011 | get_or_update_preferences | V1.2 | Preference Service | 权限和审计 |
| MCP-012 | 暴露受限地图/天气数据工具 | V1.2 | Tool Gateway Adapter | read:data scope |
| MCP-013 | 暴露 Plan/Run/Trace/Preference Resource | V1.2 | Query Service | ETag、TTL、权限 |
| MCP-014 | 输入输出 Schema、错误码、超时和幂等明确 | V1.2 | MCP contracts | 全工具 Contract |
| MCP-015 | REST/MCP 错误语义一致 | V1.2 | ApplicationError Mapper | 一致率 100% |
| MCP-016 | MCP 日志不能写 stdout 非协议内容 | V1.2 | stdio logging | 进程集成测试 |
| MCP-017 | HTTP 校验 Origin 和认证主体 | V1.2 | MCP Middleware | 安全测试 |

## 6. API、前端和交互闭环

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| WEB-001 | 自然语言输入和结构化约束确认 | V1.2 | New Trip 页面 | Playwright |
| WEB-002 | Interrupt/Resume 澄清和审批 | V1.2 | Run Workspace | E2E |
| WEB-003 | Node/Tool/Agent 执行时间线 | V1.2 | Trace Timeline | 轨迹渲染快照 |
| WEB-004 | 多候选对比和地图日程 | V1.2 | Candidate Compare | 地图与指标 E2E |
| WEB-005 | 锁定、编辑、Diff、审批和版本回退 | V1.2 | Plan Editor | V1/V2/V3 E2E |
| WEB-006 | 天气事件和局部变化高亮 | V1.2 | Weather View | Worker + Web E2E |
| WEB-007 | Memory 查看、确认、修正和删除 | V1.2 | Preferences View | E2E |
| WEB-008 | 错误、降级、来源和新鲜度展示 | V1.2 | Run/Plan UI | Provider 故障 E2E |
| WEB-009 | 不展示 Chain-of-Thought 或完整 Prompt | V1.2 | Public Event DTO | Snapshot 安全测试 |
| WEB-010 | 服务端 Secret 不进入前端 Bundle | V1.2 | Build Config | Bundle Scan |
| WEB-011 | AMap JS 使用独立域名受限浏览器凭证 | V1.2 | Frontend Config | 配置与部署检查 |
| API-001 | 创建 Trip 和异步 RunHandle | V1.2 | REST v1 | 202 Contract |
| API-002 | Run 查询、取消和 Resume | V1.2 | REST v1 | 幂等和所有权 |
| API-003 | SSE 公开事件 | V1.2 | Run Event Store | 顺序和 Schema |
| API-004 | Last-Event-ID 断线续传 | V1.2 | SSE Adapter | 不丢持久事件 |
| API-005 | 旧同步端点保留一个兼容周期 | V1.2 | Compatibility Adapter | 结果一致性 |

## 7. 持久化、身份与生产化

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| OPS-001 | PostgreSQL Checkpointer | V1.2 | infrastructure/checkpoint | 重启和多实例 Resume |
| OPS-002 | PostgreSQL Plan/Run/Trace/Memory Repository | V1.2 | infrastructure/database | Repository Contract |
| OPS-003 | Alembic 迁移 | V1.2 | migrations/ | 空库升级、旧库升级、降级 |
| OPS-004 | Redis Provider Cache | V1.2 | infrastructure/cache | TTL、命中和失效 |
| OPS-005 | Redis 限流和短期协调 | V1.2 | infrastructure/cache | 多实例限流 |
| OPS-006 | 多实例 Run/Plan/Memory 锁和幂等 | V1.2 | Lock + idempotency_records | 并发测试 |
| OPS-007 | 身份认证和租户隔离 | V1.2 | OIDC/JWT Authorizer | 越权矩阵 |
| OPS-008 | 本地 Dev Identity 必须显式开启 | V1.2 | Settings | Production 拒绝测试 |
| OPS-009 | Secret Manager/环境注入和日志脱敏 | V1.2 | deploy/ + logging | Secret Scan |
| OPS-010 | 用户数据 TTL、导出和删除 | V1.2 | Cleanup Worker | 数据生命周期 E2E |
| OPS-011 | Outbox 和后台天气事件处理 | V1.2 | Worker | 提交一致性与重放 |
| OPS-012 | OpenTelemetry Trace/Metrics/Logs | V1.2 | observability/ | Collector Smoke |
| OPS-013 | Docker Compose 完整环境 | V1.2 | compose + deploy/ | 一键启动 Smoke |
| OPS-014 | API/MCP/Worker/Web 健康检查 | V1.2 | health/readiness | 依赖故障测试 |
| OPS-015 | 备份恢复演练 | V1.2 | deploy/runbooks | 恢复报告 |
| OPS-016 | 灰度、回滚和迁移顺序 | V1.2 | deploy/runbooks | 演练记录 |
| OPS-017 | Redis 丢失不破坏持久事实 | V1.2 | Degradation Policy | 故障注入 |
| OPS-018 | 多实例重复写 fail closed | V1.2 | Transaction + Lock | 并发冲突测试 |

## 8. 可观测性与评测

| ID | 需求 | 状态 | 目标实现 | 验收证据 |
|---|---|---|---|---|
| EVAL-001 | 保持 100+ 统一 Benchmark | DONE-V1.0 | evals/v1_0 | 120-case 回归 |
| EVAL-002 | 直接 LLM Baseline | DONE-V1.0 | evaluation/baselines | 显式 Live/Mock 证据等级 |
| EVAL-003 | Constraint、Grounding、Locality 指标 | DONE-V1.0 | evaluators | 发布门禁回归 |
| EVAL-004 | Memory 60 条多会话 Scenario Chain | V1.1 | evals/v1_1 | 数据集版本和报告 |
| EVAL-005 | Memory 三组强制消融 | V1.1 | evaluation/ablations | 报告 |
| EVAL-006 | Subagent 消融 | V1.1 | evaluation/ablations | 默认值晋级决策 |
| EVAL-007 | Provider Failover 故障矩阵 | V1.2 | evals/v1_2 | 分类和恢复率 |
| EVAL-008 | API/MCP parity Suite | V1.2 | tests/contract | 一致率 100% |
| EVAL-009 | 前端八条关键 E2E | V1.2 | tests/e2e | Playwright 报告 |
| EVAL-010 | LLM 软评测仅用于主观指标 | V1.2 | soft evaluators | Judge 版本和人工抽检 |
| EVAL-011 | Mock Judge 不声明真实质量 | V1.2 | Report Provenance | 报告校验 |
| EVAL-012 | 真实 Provider/LLM 记录型号、日期和数据集 | V1.2 | Report Metadata | 可复现性检查 |
| EVAL-013 | Branch Coverage 不低于 90% | V1.2 | CI | Coverage Gate |
| EVAL-014 | Trace 保护 Secret、PII 和 Memory 正文 | V1.2 | Safe Attribute Policy | 泄露扫描为 0 |

## 9. 历史文档冲突的最终裁决

### 9.1 MCP 定位

早期架构将 Travel MCP Server 描述为地图/天气工具边界，后期路线将其描述为 Application Service 适配层。

最终裁决：

- 旅行用例工具是主要公开能力；
- 地图/天气数据工具作为受限 read:data 能力保留；
- Graph 内部不通过 MCP 调用本地 Tool Gateway；
- 两类工具都不复制 Provider 或领域逻辑。

### 9.2 多 Agent

历史路线规定多 Agent 不是 v1.1/v1.2 发布门禁，本轮又确认希望利用 Subagent 做上下文隔离。

最终裁决：

- Context Projection、Handoff、专属预算和 Specialist 实验必须实现；
- 分布式自主多 Agent 不实现；
- specialist_subagents 是否成为默认值由消融门禁决定；
- 即使未晋级，也不影响 v1.2.0 发布。

### 9.3 连续天气监控

早期版本只要求主动刷新或模拟事件，生产路线又要求后台事件处理器。

最终裁决：

- v1.2 增加有界后台天气刷新 Worker；
- Worker 只为活跃计划生成 ChangeEvent 和待审批变更；
- 不建设通用事件平台，也不自动提交破坏性 Plan Version。

### 9.4 完整前端

“完整”指完整覆盖 Agent 交互闭环，不指完整旅游商业产品。

最终裁决：

- 实现需求、Run、候选、地图、编辑、天气、Memory 和 Trace；
- OTA、支付、运营后台继续 OUT。

### 9.5 备用 Provider

Mock 不能满足备用 Provider 的最终需求。

最终裁决：

- 地图：AMap 主、Baidu 备；
- 天气：AMap 主、QWeather 备；
- Mock 仅用于离线测试和 Benchmark；
- 是否切换由确定性 ErrorCategory 决定。

## 10. 明确排除项

| ID | 排除项 | 状态 | 原因 |
|---|---|---|---|
| OUT-001 | OTA 实时库存和下单 | OUT | 与 Agent Engineering 主线无关且范围过大 |
| OUT-002 | 支付、退款和交易风控 | OUT | 非规划 Agent 核心 |
| OUT-003 | 分布式自主多 Agent | OUT | 没有独立目标和部署必要性 |
| OUT-004 | Agent 间自由文本辩论 | OUT | 不可控、难评测、污染上下文 |
| OUT-005 | 用 LLM 判定硬约束 | OUT | 确定性代码更可靠 |
| OUT-006 | Provider 原始响应直接进入 Prompt | OUT | 污染 State 并增加注入风险 |
| OUT-007 | 在生产使用 Mock 自动兜底 | OUT | 会伪造真实可用性 |
| OUT-008 | 完整运营后台 | OUT | 不强化 Agent 演示 |
| OUT-009 | 向量数据库作为先决条件 | OUT | 结构化偏好暂不需要 |
| OUT-010 | Graph 内部为展示 MCP 而绕行网络 | OUT | 增加故障面且复制边界 |

## 11. 发布关闭清单

- [ ] 所有 V1.1 项有实现 PR、测试和评测报告。
- [ ] EXPERIMENT 项有可复现结果和默认值 ADR。
- [ ] 所有 V1.2 项有实现 PR、测试或演练记录。
- [ ] DONE-V1.0 项全部通过回归。
- [ ] CONDITIONAL 项有明确“启用”或“不启用”结论。
- [ ] OUT 项没有被误纳入 Scope。
- [ ] API、MCP、Frontend、部署和配置文档与代码一致。
- [ ] README 中所有质量声明都附带证据等级、数据集版本和日期。
