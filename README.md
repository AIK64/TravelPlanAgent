# Constraint-Aware Travel Agent

面向中国城市旅行场景的约束感知规划 Agent。v0.4 在显式需求 Graph 上加入 Human-in-the-loop：缺失或歧义信息通过 LangGraph Interrupt 暂停，用户补充后沿同一 `thread_id` Resume；LLM 只生成字段 Patch，确定性代码负责白名单合并、硬约束和局部工具重跑。

## 当前进度：v0.4.0

```text
Natural Language
  → Parse Requirement
  → Deterministic Validate
  → Interrupt ↔ Resume Clarification Patch
  → Anchor Tool Use / Cached Resolution Reuse
  → Assemble TripSpec
  → Existing Plan → Tool Use → Validate → Replan Loop
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

当前边界：SQLite 只用于单机演示，不是多实例生产存储；中断只覆盖需求澄清，不支持完成计划后的编辑审批。Mock 解析器不代表开放域中文效果，尚未实现长期记忆、天气、真实步行路线、计划版本 Diff 和 OTA 下单。

## 学习入口

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

## Benchmark 与验证

默认 Benchmark 评估 Mock Fixture 的可重复回归基线，不是线上模型效果声明：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_requirement_parser.py
.\.venv\Scripts\python.exe scripts\evaluate_clarification_parser.py
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
```

显式配置 `REQUIREMENT_PROVIDER=openai` 或 `deepseek` 后，同一脚本可评测对应适配器；执行会产生实际 API 调用和费用，仓库不会自动运行。
