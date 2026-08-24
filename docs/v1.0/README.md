# v1.0 统一运行治理与评测发布

> 状态：已实现并通过本地 Mock 发布门禁  
> 版本：`1.0.0`  
> 实现日期：2026-08-24  
> 完整设计：[design.md](design.md)

## 1. 这个版本新增了什么

v1.0 没有继续横向增加旅行功能，而是把 Requirement、Planning、Lifecycle/HITL 和 Weather Replanning 纳入同一套 Agent 执行治理：

```text
Mutation API / Evaluator
  → RunCoordinator 创建独立 AgentRun
  → 绑定共享 ExecutionBudget + TraceRecorder + FaultInjector
  → Requirement / Planning / Lifecycle Graph
      → execution_budget_guard
      → instrumented Node / Conditional Route
      → observed Tool / LLM / Checkpoint / Repository
      → Hard Validate / Critic / Repair / Interrupt
  → 明确 terminal_reason + ExecutionUsage
  → Memory/SQLite RunRepository
  → Run/Trace API + Trajectory Assertion + Release Gate
```

核心新增能力：

- 每次创建、恢复、编辑、审批或天气刷新都有唯一 `run_id`，并与 `thread_id`、`session_id`、`request_id` 分离。
- 三个 Graph 都有显式 `execution_budget_guard`；Tool、Provider Attempt、LLM、修复、Interrupt、Checkpoint、Trace 和 Deadline 共享同一预算。
- Node、Route、Tool、Retry、Cache、LLM、Validator、Repair、Interrupt、Checkpoint、Repository 和终态通过强类型 `TraceEvent` 串成因果轨迹。
- Tool 故障、LLM 故障、Checkpoint/Repository 故障、预算耗尽和业务不可行使用不同 `RunTerminalReason`。
- Trace 只接收白名单标量字段，不保存原始需求、Prompt、Provider 响应、API Key 或 Approval Token。
- 提供内存与 SQLite Run Store、Run 摘要查询、Trace 分页以及 Thread/Session Run 链查询。
- 提供确定性故障注入、120 次实际工作流发布门禁、180 次实际工作流消融和单次 DeepSeek Baseline 接口。

## 2. 启动与配置

默认全 Mock，可离线运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:TRAVEL_PROVIDER = "mock"
$env:REQUIREMENT_PROVIDER = "mock"
$env:CRITIC_PROVIDER = "mock"
$env:EDIT_PROVIDER = "mock"
$env:WEATHER_PROVIDER = "mock"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

Run 治理默认配置见项目根目录 `.env.example`。常用字段：

```dotenv
RUN_STORE_BACKEND=memory
RUN_BUDGET_PROFILE=default-v1
RUN_MAX_GRAPH_STEPS=120
RUN_MAX_TOOL_CALLS=160
RUN_MAX_PROVIDER_ATTEMPTS=240
RUN_MAX_LLM_CALLS=8
RUN_MAX_REPAIR_ROUNDS=4
RUN_MAX_TRACE_EVENTS=512
RUN_DEADLINE_SECONDS=120
```

本地演示进程重启后查询 Run：

```powershell
$env:RUN_STORE_BACKEND = "sqlite"
$env:RUN_SQLITE_PATH = ".data/travel-agent-runs.sqlite3"
```

SQLite 只定位为单机演示存储，不代表已经实现多实例一致性。

## 3. 获取 Run ID 与查询 Trace

所有会改变 Agent 状态的成功响应均包含：

```text
X-Agent-Run-Id: <uuid>
X-Agent-Trace-Status: complete | degraded
```

PowerShell 示例：

```powershell
$body = @{
  text = "2026年10月2日到10月4日去杭州，3个人，预算1500元，2日10:30到杭州东站，4日19:00离开，灵隐寺必须去。"
  reference_date = "2026-08-23"
  timezone = "Asia/Shanghai"
} | ConvertTo-Json

$response = Invoke-WebRequest `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/plans/from-text" `
  -ContentType "application/json; charset=utf-8" `
  -Body $body

$runId = $response.Headers["X-Agent-Run-Id"]
$run = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/runs/$runId"
$trace = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/runs/$runId/trace?after_sequence=0&limit=100"
```

新增只读接口：

- `GET /api/v1/runs/{run_id}`：Run 状态、终止原因、预算、用量和 Trace 状态。
- `GET /api/v1/runs/{run_id}/trace`：按 `sequence` 分页查询事件。
- `GET /api/v1/plan-sessions/{session_id}/runs`：生命周期 Run 链。
- `GET /api/v1/requirement-threads/{thread_id}/runs`：澄清线程 Run 链。

预算或 Provider 错误会返回安全错误体；只要执行边界已经创建，错误响应头同样携带 `X-Agent-Run-Id`。

## 4. DeepSeek 与地图 API

v1.0 没有改变现有 Provider 选择方式。DeepSeek 可分别承担 Requirement、Soft Critic 和 Edit 任务：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm-deepseek]"
$env:DEEPSEEK_API_KEY = "仅保存在本机，不提交"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "显式支持的需求模型名"
$env:REQUIREMENT_PROVIDER = "deepseek"
$env:CRITIC_PROVIDER = "deepseek"
$env:CRITIC_MODEL = "显式支持的评审模型名"
$env:EDIT_PROVIDER = "deepseek"
$env:EDIT_MODEL = "显式支持的编辑模型名"
```

地图 POI/路线与天气可以独立选择 AMap：

```powershell
$env:AMAP_API_KEY = "仅保存在本机，不提交"
$env:TRAVEL_PROVIDER = "amap"
$env:WEATHER_PROVIDER = "amap"
```

系统不会在真实 Provider 失败后静默回退 Mock；失败会按有限重试和共享预算终止，并进入同一个 Run Trace。

## 5. 发布门禁与实际结果

默认离线发布命令：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_v1_release.py --profile mock --gate
```

2026-08-24 本机 Mock 结果：

| 指标 | 结果 |
|---|---:|
| Case / 实际工作流执行 | 120 / 120 |
| completed 已知硬约束满足率 | 100% |
| 有界终止率 | 100% |
| 故障分类准确率 | 100% |
| Trace 完整率 | 100% |
| 不安全交付 | 0 |
| 外部故障误判为 `business_infeasible` | 0 |
| Graph Step | 780 |
| Tool Call / Provider Attempt / Cache Hit | 806 / 49 / 757 |
| LLM Call | 140 |

Mock Provider 不返回真实 Token，用量与费用保持 `unknown/not_applicable`，不能把这些数字描述为 DeepSeek、OpenAI 或 AMap 线上效果。报告写入被 Git 忽略的 `reports/v1_0/`，包含 Git/dirty、Python/平台、Dataset/Config Hash、Seed、Provider/Prompt 和可复现指纹。

## 6. 消融与单次 LLM Baseline

实际工作流消融：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_v1_ablations.py --gate
```

当前 30 个源 Case × 6 个变体，共 180 次工作流执行：

| Variant | Hard rate | Unsafe | Provider Attempt | LLM Call |
|---|---:|---:|---:|---:|
| FULL | 100% | 0 | 49 | 40 |
| NO_VALIDATOR | 80% | 2 | 49 | 30 |
| NO_OPTIMIZER | 100% | 0 | 49 | 40 |
| NO_SOFT_CRITIC | 100% | 0 | 49 | 30 |
| FULL_REPLAN | 100% | 0 | 49 | 40 |
| CACHE_OFF | 100% | 0 | 403 | 40 |

这批统一 Requirement 数据没有触发局部修复，所以 `FULL_REPLAN` 与 FULL 的 Tool Call 相同；局部重规划收益仍由 `evals/repairs` 和 `evals/weather` 的专用工作流数据证明，不能从这张表虚构提升。

Mock 单次规划只验证 Runner Contract：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_v1_direct_baseline.py
```

显式调用 DeepSeek 单次 Baseline（会产生真实调用与费用）：

```powershell
$env:DEEPSEEK_API_KEY = "仅保存在本机"
$env:DEEPSEEK_MODEL = "显式模型名"
.\.venv\Scripts\python.exe scripts\evaluate_v1_direct_baseline.py `
  --provider deepseek `
  --allow-live
```

Direct Baseline 每个 Case 只调用一次模型；Evaluator 在调用结束后独立执行 Hard Validator，违规不会反馈给模型。仓库自带的单条 Mock 数据明确标记为 `annotated_contract`，不进入发布质量结论。

## 7. 验证结果与边界

最终本地验证：

```text
462 passed, 2 skipped
Branch Coverage: 90.19%
Release Gate: PASS（120 workflow executions）
Ablation Gate: PASS（180 workflow executions）
compileall: PASS
```

v1.0 仍不包含长期 Preference Memory、MCP、备用 Provider 自动切换、完整前端或生产级多实例部署；它们分别属于 v1.1 和 v1.2。多 Agent 也不是 v1.0 的目标，当前职责只有在确实需要独立推理上下文和闭环、且评测证明有收益时才会拆分。
