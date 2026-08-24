# Constraint-Aware Travel Agent

面向中国城市旅行场景的约束感知规划 Agent。v1.2 在 v1.0 的 Requirement、Planning、Lifecycle/HITL 与 Weather Replanning 基线上，加入长期 Preference Memory、上下文裁剪、进程内 Specialist Handoff、真实 Provider Failover、Travel MCP、异步 Run/SSE、核心演示前端与生产部署适配。核心仍是显式 `Plan → Tool Use → Validate/Critic → Replan` Agent Loop，而不是地图或前端工程堆叠。

## 当前进度：v1.2.0

```text
Natural Language
  → RunCoordinator / Shared ExecutionBudget / Safe Trace
  → Parse Requirement
  → Deterministic Validate
  → Interrupt ↔ Resume Clarification Patch
  → Anchor Tool Use / Cached Resolution Reuse
  → Assemble TripSpec
  → Build Driving/Walking Route Matrix
  → Build OptimizationProblem → Solve Style Variants
  → Materialize Candidate Plans → Hard Validate
      ├─ deliverable → Evidence Digest → LLM Soft Critic → Grounding Gate
      │                  → Deterministic Quality Gate
      │                    ├─ Select + Grounded Explanation
      │                    └─ One Soft Repair → Route Delta → Hard Validate → Re-evaluate
      └─ invalid → Select Target → Critic → RepairPlan
                   → Local Repair → Delta Route Tool Use → Revalidate
  → Candidate Selection Interrupt → Persist V1
  → Lock / Edit Intent → Grounding → Impact / Lock Guard
  → Local Preview → POI/Route Delta → Hard Validate / Soft Critic
  → Approval Interrupt
      ├─ approve → CAS Commit V2 → Continue
      └─ reject  → Keep V1 → Continue
  → Weather Refresh
      → Resolve Location → Fetch Snapshot → Deterministic Risk Policy
      → Derive / Deduplicate ChangeEvent → Exposure / Impact Analysis
          ├─ no change / recovered / no impact → Persist Outcome
          ├─ lock / unknown / no safe repair → HITL Attention
          └─ bounded repair → Indoor POI Search → Route Delta
                             → Hard Validate → Preview → Approval
  → RunTerminalReason / Usage / Trace API / Release Gate
```

已完成：

- 统一 `AgentRun`、Run/Thread/Session 边界和 Memory/SQLite Run Store；所有 Agent mutation 返回 `X-Agent-Run-Id` 与 Trace 状态。
- 三个 Graph 的显式 `execution_budget_guard`，以及 Node、Conditional Route、Tool、LLM、Retry、Cache、Validator、Repair、Interrupt、Checkpoint、Repository 和终态观测。
- Run 级 Graph Step、Tool Call、Provider Attempt、LLM、Token、Repair、Interrupt、Checkpoint、Trace 和 Deadline 共享预算；重复动作指纹也有统一上限。
- Tool/LLM/Checkpoint/Repository 故障、预算耗尽和业务不可行分开分类；错误响应可关联失败 Run。
- Trace 属性白名单、长度限制和秘密泄漏测试；原始请求、Prompt、Provider 响应、Key 与 Approval Token 不进入 Trace。
- 120 次实际 Mock 工作流发布门禁通过；硬约束、有界终止、故障分类和 Trace 完整率均为 100%，不安全交付为 0。
- 180 次实际工作流消融门禁通过；NO_VALIDATOR 暴露 2 次不安全交付，缓存将 Provider Attempt 从 403 降至 49 且领域结果一致。
- 单次 DeepSeek Direct Plan Baseline 接口；必须显式 `--allow-live`，Mock 只验证 Contract，不冒充真实模型质量。

- 显式 Requirement State、Node、Edge 与 `needs_clarification` 条件路由。
- 可替换 `RequirementModel` Protocol；离线 Mock、OpenAI Structured Outputs 与 DeepSeek JSON Output 适配器。
- 缺字段、日期/交通时间冲突的确定性校验，LLM 不承担硬约束判断。
- 抵达、离开和住宿地点通过现有 POI Tool Gateway 解析，结果标准化后再写入 State。
- 需求模型超时、有限重试、安全错误分类及 503 语义；不记录原始需求或 Provider 响应。
- 自然语言与结构化双 API；完整轨迹可通过日志和 Checkpoint 检查。
- 30 条离线 Requirement Benchmark，覆盖完整、缺抵达、缺离开、缺日期和冲突输入。
- 最多五轮、默认三轮的 `Interrupt → Resume → Patch → Validate` 澄清循环。
- 字段级 Patch 白名单和锚点失效规则，已确认字段不会被补充回答重写。
- 同一线程并发 Resume 串行化；旧 Interrupt 返回 409，未知线程返回 404。
- 默认内存 Checkpoint，并提供 SQLite 单机跨进程恢复模式。
- 6 条 Clarification Patch Benchmark，量化目标字段修复率和字段保持率。
- 显式 `CriticReport`、`RepairAction`、`RepairPlan` 与 `RepairAttempt`，修复轨迹可从 Checkpoint 读取。
- 预算、步行、活动时长、必去项遗漏和局部时间冲突的确定性修复策略，不放宽硬约束、不删除 `must_visit`。
- 按邻接变化计算 Route Delta，只补查缺失路线；未受影响日期通过 Hash 守卫保证不被改写。
- 重复违规、重复动作、无进展、无安全动作和修复轮次耗尽均有界终止；Tool 失败继续返回外部失败而非 `infeasible`。
- 9 条离线 Repair Benchmark：精确用例准确率、修复成功率、硬约束满足率、终止率和局部性均为 100%，路线复用率 80.77%。
- 显式 `OptimizationProblem`、`OptimizationResult` 与 `OptimizationBudget`，求解器只读取标准化领域对象。
- Graph 可见的 `build_route_matrix → build_optimization_problem → solve_candidate_variants → materialize_optimized_candidates` 路径。
- OR-Tools CP-SAT 在时间、搜索状态、候选数和变体数预算内生成 relaxed、balanced、exploration 候选，并输出目标分解。
- AMap v5 驾车与步行路线统一经过 Tool Gateway；缓存键区分 Provider、模式、策略和 6 位坐标版本。
- 近距离腿使用真实步行路线，远距离腿明确使用驾车，不再把驾车距离按比例伪装成最终步行事实。
- 求解超时和无可行解显式记录 `optimization.degraded`，继续使用确定性最近邻回退；外部 Tool 失败仍不会伪装成业务不可行。
- 4 条固定 Optimization Benchmark 同时比较优化器/启发式、单候选/三候选、真实步行/估算消融，并报告约束满足率、求解成功率、路线效率和延迟。
- 独立 `CriticModel` Protocol、Gateway 和 Mock、DeepSeek、OpenAI Provider；需求解析与软评审可以独立选型。
- 有界 `CandidateEvidenceDigest`、稳定 Evidence ID 和 Prompt Injection 数据边界，原始 Provider 响应不会进入 Graph State。
- 五维结构化 `SoftCritique` 与 Grounding Gate；跨候选引用、未知 Evidence、重复维度、非法实体和删除 `must_visit` 会被拒绝。
- 确定性 Quality Gate 计算软分数，Hard Validation 等级始终优先，LLM 无权返回最终排序分。
- 最多一次非必去 POI 移动、重排或移除；经 Route Delta、Hard Validator 和再评价后，提升不足会恢复 baseline。
- Critic 超时、认证、Schema 或 Grounding 失败均降级交付硬合法计划，不会返回业务 `infeasible`。
- API 返回 Critic 状态、执行摘要、有效 Critique、Grounded Explanation 和软修复轮次。
- 15 条离线 Soft Critic Fixture 和 with/without critic 消融脚本，覆盖 Grounding、动作安全、选择一致率和硬约束回归。
- 独立 `PlanLifecycleWorkflow`、`PlanVersion`、`PlanPreview`、稳定 `item_id`、日期/项目锁和结构化 V1/V2 Diff。
- Candidate Selection、Edit Grounding、Impact Analysis、Lock/Locality Guard、Preview 和 Approval 均在 Graph 中可观察。
- 独立 `EditModel` Protocol 与 Mock、DeepSeek、OpenAI Provider；模型只解析白名单动作，不写计划或判断硬约束。
- move/reorder/remove/add/replace 复用 POI Facts 和 Route Result，只为新增实体或变化邻接边调用工具。
- Preview/Commit 两阶段、Approval Token、Repository CAS、request ID 幂等和旧 Interrupt/Version/Revision 409。
- 内存/SQLite Plan Repository；SQLite 模式已覆盖候选选择 Interrupt 的服务重启恢复。
- 15 条离线 Lifecycle Fixture 及实际 API/轨迹测试，覆盖锁定保持、未影响日期保持、Diff、审批、幂等和有界终止。
- 独立 `WeatherProvider` Protocol、Mock/AMap Adapter 和带缓存、超时、重试、并发限制、结构化失败语义的 `WeatherToolGateway`。
- Graph 可见的 `resolve_weather_location → fetch_weather_snapshot → classify_weather_risks → derive_weather_event → deduplicate_weather_event → analyze_weather_impact → build_weather_repair_plan` 路径。
- 版本化 `weather-risk-v1` 确定性策略、稳定 Snapshot/Event Fingerprint 和 request/snapshot/event 三层幂等；供应商 `reporttime` 不会制造重复事件。
- 室内/户外/混合/未知暴露分类；锁或未知数据进入 HITL，`must_visit` 只允许移到低风险日期，不会被天气 Repair 删除。
- 天气 Preview 携带 `event_id`、`snapshot_id` 和策略版本；审批后证据进入 Plan Version，恢复事件不会自动回滚用户计划。
- 最近两个天气快照、最多 50 个事件及 Receipt 持久化；SQLite 重启后可继续查看事件并批准原 Preview。
- 刷新、天气状态和事件查询 API；Provider 失败返回 503 并保留 Active Version，不会被伪装成 `infeasible` 或天气良好。
- 30 条固定天气 Fixture；当前离线 Mock 基线 Event F1、Impact Exact Match、锁与未影响日保持率均为 100%，路线复用率为 66.41%。

当前边界：不实现 OTA 库存或交易，不把 Agent 拆成分布式自治服务；Specialist 是进程内强类型 Handoff，用于 Planner/Critic/Replanner 上下文隔离，Orchestrator 仍拥有唯一路由和终止权。真实 Provider Live Smoke、Docker 全栈演练与 LLM 软评测需要调用者自行提供凭证，默认测试不会产生外部费用。

本机最终门禁收集 565 项测试，563 项通过，2 项真实 Provider Live Smoke 因未显式启用而跳过；statement + branch 综合覆盖率为 90.0177%。前端 `npm run build` 通过。当前环境没有 Docker CLI，因此 Compose 仅完成代码、配置和 Repository/Queue Contract 验证，尚未在本机做容器级 Smoke。

## 学习入口

- 从 [v1.2 实现与使用指南](docs/v1.1-v1.2/implementation.md) 查看 Memory、Specialist、异步 API、MCP、Baidu/QWeather、前端、Worker 与生产部署的实际入口。
- 后续开发以 [v1.1 → v1.2 最终开发入口](docs/v1.1-v1.2/README.md) 为准；[统一设计报告](docs/v1.1-v1.2/design.md) 和 [需求追踪矩阵](docs/v1.1-v1.2/requirements-traceability.md) 已冻结一次连续实现、两个内部 Gate、最终发布 v1.2.0 的范围。
- 历史版本边界和共同守则见 [v0.9 → v1.2 权威迭代路线](docs/roadmap-to-v1.2.md)。
- 从 [v1.0 实现文档](docs/v1.0/README.md) 查看 Run/Trace API、预算、DeepSeek/地图配置、发布门禁和消融结果；完整取舍见 [设计报告](docs/v1.0/design.md)。
- 从 [v0.9 天气事件驱动局部重规划文档](docs/v0.9/README.md) 查看配置、API、Graph、失败语义和实际离线评测结果；完整取舍见 [设计报告](docs/v0.9/design.md)。
- 从 [v0.8 计划生命周期 HITL 文档](docs/v0.8/README.md) 查看会话 API、DeepSeek 编辑配置、Graph、失败语义和评测；完整取舍见 [设计报告](docs/v0.8/design.md)。
- 从 [v0.7 Grounded LLM Soft Critic 文档](docs/v0.7/README.md) 查看当前实现、配置、Graph、失败语义与评测；完整设计见 [设计报告](docs/v0.7/design.md)。
- 从 [v0.6 约束优化与真实路线文档](docs/v0.6/README.md) 查看当前 Graph、求解预算、降级语义与消融 Benchmark。
- [v0.5 局部自修复文档](docs/v0.5/README.md) 解释当前保留的 Critic/Repair 回退闭环。
- 从 [v0.4 HITL 澄清恢复文档](docs/v0.4/README.md) 开始。
- [v0.3 自然语言需求文档](docs/v0.3/README.md) 解释初始抽取与验证基线。
- [v0.2 Tool Use 文档](docs/v0.2/README.md) 解释规划子流程和可靠工具层。
- [v0.1 文档](docs/v0.1/README.md) 是纯确定性历史基线。
- 完整长期设计见 [项目架构文档](docs/travel-agent-architecture.md)；已实现行为以当前版本文档和代码为准。

## 本地运行：默认全 Mock

需要 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:TRAVEL_PROVIDER = "mock"
$env:REQUIREMENT_PROVIDER = "mock"
$env:CRITIC_PROVIDER = "mock"
$env:WEATHER_PROVIDER = "mock"
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

启动后访问 <http://127.0.0.1:8000/docs>。结构化入口仍为 `POST /api/v1/plans`；自然语言入口为 `POST /api/v1/plans/from-text`：

```powershell
$body = @{
  text = "2026年10月2日到10月4日去杭州，3个人，预算1500元，住西湖东侧，喜欢自然和美食，2日10:30到杭州东站，4日19:00从杭州东站离开，灵隐寺必须去，不想太累。"
  reference_date = "2026-08-23"
  timezone = "Asia/Shanghai"
} | ConvertTo-Json

$response = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/plans/from-text" `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

完整需求返回 `completed` 或规划层的 `infeasible`；信息不足或冲突返回 HTTP 200 + `needs_clarification` 和稳定问题列表；需求模型或地图服务不可用返回 HTTP 503。

返回 `needs_clarification` 时，使用响应中的 `thread_id` 和 `interrupt.id` 恢复：

```powershell
$resumeBody = @{
  interrupt_id = $response.interrupt.id
  request_id = [guid]::NewGuid().ToString()
  answer = "10月4日19:00从杭州东站离开。"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/plans/from-text/$($response.thread_id)/resume" `
  -ContentType "application/json; charset=utf-8" `
  -Body $resumeBody
```

默认 `CHECKPOINT_BACKEND=memory`。需要演示服务重启后恢复时：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,checkpoint-sqlite]"
$env:CHECKPOINT_BACKEND = "sqlite"
$env:CHECKPOINT_SQLITE_PATH = ".data/travel-agent-checkpoints.sqlite3"
```

SQLite 会保存原始需求和补充回答的短期上下文，仅应用于本地单进程演示；`.data/` 已被 Git 忽略。

## 真实 LLM 需求解析：显式启用，绝不 fallback

地图工具可继续使用 Mock，也可独立选择 AMap。OpenAI SDK 是可选依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm-openai]"
$env:REQUIREMENT_PROVIDER = "openai"
$env:OPENAI_API_KEY = "replace-with-your-own-key"
$env:REQUIREMENT_MODEL = "replace-with-an-explicit-supported-model"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

缺少 key 或显式模型名会在配置阶段失败；模型调用失败在有限重试后返回 503，不会回退 Mock。API key 不得写入 `.env.example`、请求文件、日志或提交。

DeepSeek 使用 OpenAI SDK 调用其 Chat Completions JSON Output，但由独立 Provider 负责 Pydantic 二次校验和失败分类：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm-deepseek]"
$env:REQUIREMENT_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "replace-with-your-own-key"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "deepseek-v4-flash"
$env:TRAVEL_PROVIDER = "mock"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

DeepSeek Provider 显式关闭 thinking mode：需求抽取是有界结构化任务，不需要额外推理 token。旧名称 `deepseek-chat`、`deepseek-reasoner` 已停止使用，配置阶段会拒绝。

软质量评审 Provider 与需求解析独立配置。使用 DeepSeek Soft Critic：

```powershell
$env:CRITIC_PROVIDER = "deepseek"
$env:CRITIC_MODEL = "replace-with-an-explicit-supported-model"
$env:DEEPSEEK_API_KEY = "replace-with-your-own-key"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
```

也可设置 `CRITIC_PROVIDER=openai` 并提供 `OPENAI_API_KEY` 与独立 `CRITIC_MODEL`。设置 `CRITIC_PROVIDER=disabled` 会完全跳过模型，但仍按硬约束和确定性指标交付。真实 Critic 失败不会回退 Mock。

AMap 模式沿用 v0.2 的显式配置：地图 POI/路线使用 `TRAVEL_PROVIDER=amap`，天气使用独立的 `WEATHER_PROVIDER=amap`，两者都复用仅保存在本机的 `AMAP_API_KEY`，可以分别启用。AMap 失败不会回退 Mock，也不会伪装成业务 `infeasible`。

v0.6 默认同时请求驾车路线和阈值内的步行路线。`MAX_WALKING_LEG_METERS` 控制单段允许步行的最大距离；`USE_REAL_WALKING_ROUTES=false` 仅用于消融实验，会恢复历史估算语义，不建议用于最终计划。

## Benchmark 与验证

默认 Benchmark 评估 Mock Fixture 的可重复回归基线，不是线上模型效果声明：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_requirement_parser.py
.\.venv\Scripts\python.exe scripts\evaluate_clarification_parser.py
.\.venv\Scripts\python.exe scripts\evaluate_local_repair.py
.\.venv\Scripts\python.exe scripts\evaluate_optimization.py
.\.venv\Scripts\python.exe scripts\evaluate_soft_critic.py
.\.venv\Scripts\python.exe scripts\evaluate_plan_lifecycle.py
.\.venv\Scripts\python.exe scripts\evaluate_weather_replanning.py
.\.venv\Scripts\python.exe scripts\evaluate_v1_release.py --profile mock --gate
.\.venv\Scripts\python.exe scripts\evaluate_v1_ablations.py --gate
.\.venv\Scripts\python.exe scripts\evaluate_v1_direct_baseline.py
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
```

统一 Mock 发布门禁实际执行 120 次工作流；消融执行 180 次隔离工作流。显式配置真实 Provider 或运行 Direct DeepSeek Baseline 会产生实际 API 调用和费用，仓库不会自动运行，Direct Baseline 还要求 `--allow-live`。
