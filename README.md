# Constraint-Aware Travel Agent

面向中国城市旅行场景的约束感知规划 Agent。v0.6 在局部自修复闭环前加入显式约束优化：Agent 将标准化 POI、时间窗、预算、移动能力和真实驾车/步行路线矩阵组装为 `OptimizationProblem`，由有界 OR-Tools CP-SAT 求解三种风格候选；超时会留下可观察的降级结果并回退到 v0.5 确定性启发式。

## 当前进度：v0.6.0

```text
Natural Language
  → Parse Requirement
  → Deterministic Validate
  → Interrupt ↔ Resume Clarification Patch
  → Anchor Tool Use / Cached Resolution Reuse
  → Assemble TripSpec
  → Build Driving/Walking Route Matrix
  → Build OptimizationProblem → Solve Style Variants
  → Materialize Candidate Plans → Hard Validate
      ├─ deliverable → Select Best
      └─ invalid → Select Target → Critic → RepairPlan
                   → Local Repair → Delta Route Tool Use → Revalidate
```

已完成：

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

当前边界：v0.6 的 CP-SAT 负责硬约束和可计算目标，尚未加入 LLM 软质量评价；Grounded LLM Soft Critic 在 v0.7。当前路线矩阵覆盖 POI 候选的必要有向边，不是 OTA 级全量交通规划。SQLite 只用于单机演示；中断尚不支持完成计划后的编辑审批。长期 Memory、天气事件、计划版本 Diff、MCP 和生产化分别属于后续版本。

## 学习入口

- 后续开发以 [v0.5 → v1.2 权威迭代路线](docs/roadmap-to-v1.2.md) 为准；v1.0 完成核心 Agent，v1.1 增加 Preference Memory，v1.2 完成 MCP 与平台化。
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

AMap 模式沿用 v0.2 的显式配置：设置 `TRAVEL_PROVIDER=amap` 与本机 `AMAP_API_KEY`。AMap 失败同样不会回退 Mock，也不会伪装成业务 `infeasible`。

v0.6 默认同时请求驾车路线和阈值内的步行路线。`MAX_WALKING_LEG_METERS` 控制单段允许步行的最大距离；`USE_REAL_WALKING_ROUTES=false` 仅用于消融实验，会恢复历史估算语义，不建议用于最终计划。

## Benchmark 与验证

默认 Benchmark 评估 Mock Fixture 的可重复回归基线，不是线上模型效果声明：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_requirement_parser.py
.\.venv\Scripts\python.exe scripts\evaluate_clarification_parser.py
.\.venv\Scripts\python.exe scripts\evaluate_local_repair.py
.\.venv\Scripts\python.exe scripts\evaluate_optimization.py
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
```

显式配置 `REQUIREMENT_PROVIDER=openai` 或 `deepseek` 后，同一脚本可评测对应适配器；执行会产生实际 API 调用和费用，仓库不会自动运行。
