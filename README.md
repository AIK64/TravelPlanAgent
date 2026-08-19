# Constraint-Aware Travel Agent

一个面向中国城市旅行场景的约束感知自适应规划 Agent。项目采用 LangGraph 构建有状态的 `Plan → Execute → Validate → Replan` 工作流，并将 LLM 语义职责、确定性约束验证和路线优化解耦。

## 项目定位与开发优先级

本项目首先是一个用于简历和面试展示的 **Agent Engineering 项目**。开发优先级是 Agent 的显式状态、Tool Use、条件路由、验证与 Replan Loop、上下文管理、Human-in-the-loop、执行轨迹和评测；地图 Provider、缓存、重试、API 与存储是支撑这些 Agent 能力可靠运行的工程层，不追求脱离 Agent 目标的全面业务覆盖。

每个版本都必须说明新增了什么 Agent 能力、它如何改变 Graph 轨迹，以及怎样通过日志、测试或 Benchmark 证明。长期约束见 [项目记忆与开发原则](AGENTS.md)。

完整设计见 [项目架构文档](docs/travel-agent-architecture.md)。

如果你正在跟随项目学习，请从 [v0.1 学习与实现文档](docs/v0.1/README.md) 开始。该目录只描述当前已经落地的代码，并包含请求生命周期、代码导读、LangGraph 原理、约束验证、运行测试和练习。

## 当前进度

当前完成的是第一条可运行主线：

- Pydantic 强类型旅行、POI、计划和违规模型
- 杭州 Mock POI 数据集
- 可替换的确定性路线估算器
- Relaxed、Balanced、Exploration 三种候选计划
- 时间、预算、营业时间、到离站缓冲、步行与必去地点校验
- LangGraph `Plan → Validate → Replan` 有界循环
- 基于 `InMemorySaver` 的开发期 Checkpoint
- 带 `thread_id` 关联的结构化链路日志（INFO / DEBUG）
- FastAPI 健康检查和同步规划接口
- 单元、工作流和 API 测试

尚未实现：

- LLM 需求解析与高层 Planner
- 高德地图和和风天气真实适配器
- OR-Tools 时间窗优化
- PostgreSQL Checkpointer 与计划版本管理
- Interrupt/Resume 和 Human-in-the-loop
- 用户长期偏好 Store
- Benchmark 与 LangSmith 评测

## 架构

```text
PlanningRequest
  ↓
load_context
  ↓
create_initial_candidates
  ↓
validate_candidates
  ├── 有合法方案 → select_best → END
  ├── 无合法方案且有预算 → replan ─┐
  │                                 └→ validate_candidates
  └── 重规划预算耗尽 → mark_infeasible → END
```

当前节点使用确定性实现，后续会将 Requirement Parser、High-level Planner、Critic 和 Explanation Generator 替换为有严格结构化输出的 LLM 适配器。Validator、路线算法和预算计算继续保持确定性。

## 本地运行

要求 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

启动后访问：

- OpenAPI：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

规划链路日志会输出到运行 Uvicorn 的终端。需要查看每天的候选行程和具体违规时，将 `APP_LOG_LEVEL` 改为 `DEBUG`。详细事件说明见 [v0.1 可观测性与链路日志](docs/v0.1/09-observability-and-logging.md)。

## 调用示例

请求体位于 [`examples/hangzhou_request.json`](examples/hangzhou_request.json)。

```powershell
$body = Get-Content `
  -LiteralPath .\examples\hangzhou_request.json `
  -Raw `
  -Encoding UTF8

$null = $body | ConvertFrom-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/plans `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

也可以直接运行兼容 Windows PowerShell 5.1 和 PowerShell 7 的脚本：

```powershell
.\scripts\invoke-hangzhou-example.ps1
```

接口返回：

- `status`：`completed` 或 `infeasible`
- `selected_plan`：得分最高的合法方案
- `candidates`：当前重规划轮次的三个候选方案
- `iterations`：实际重规划次数
- `message`：执行结果说明

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

查看覆盖率：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  --cov=travel_agent `
  --cov-report=term-missing
```

## 代码结构

```text
src/travel_agent/
├── api/                 # FastAPI 路由
├── domain/              # 强类型领域模型
├── graph/               # LangGraph State 与工作流
├── planning/            # Mock 数据、规划、路线和 Validator
└── app.py               # FastAPI 应用入口

tests/                   # 领域、工作流和 API 测试
examples/                # 可执行请求示例
docs/                    # 完整架构文档
```

## 下一步

下一阶段优先完成：

1. 抽象 `POIProvider`、`RouteProvider` 和 `WeatherProvider` 协议。
2. 将 Mock Provider 与规划领域解耦。
3. 接入高德 POI/路线 API，并增加缓存、超时和重试。
4. 增加自然语言 Requirement Parser，同时保留结构化 API 输入。
5. 将开发期内存 Checkpoint 替换为 PostgreSQL Checkpoint。
