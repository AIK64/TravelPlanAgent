# Constraint-Aware Travel Agent

面向中国城市旅行场景的约束感知规划 Agent。v0.2 的重点是 **Agent Tool Use**：LangGraph 中的检索意图、POI/路线工具节点、typed State、确定性校验和条件 Replan Loop 都可从日志、Checkpoint 与轨迹测试观察；地图 API、缓存和 FastAPI 只是让工具调用可靠的支撑层。

## 当前进度：v0.2.0

```text
Search Intent → POI Tool Use → 标准化 State → Route Tool Use
→ 候选物化 → Validate → Select / Replan / Infeasible
```

已完成：

- 显式 LangGraph State、Node、Edge 与有界 `Plan → Tool Use → Validate → Replan` Loop。
- Mock/AMap Provider Protocol，`POIFacts` 与 `RouteResult` 的标准化及数据来源标记。
- 异步 Gateway 的 TTL cache、并发限制、重试、工具事件与安全错误语义。
- `thread_id` 关联 API、工具日志、Graph State 与开发期 `InMemorySaver` Checkpoint。
- 离线契约、Gateway、轨迹和 API 测试；AMap 可选 live smoke 默认跳过。

尚未实现：LLM 自然语言需求解析、真实步行路线、天气、OTA 交易/下单、长期记忆、持久化 Checkpoint、Human-in-the-loop 与生产评测平台。不要将当前项目表述为已经具备这些能力。

## 学习入口

- 从 [v0.2 Tool Use 学习文档](docs/v0.2/README.md) 开始，按其中推荐顺序阅读。
- [v0.1 文档](docs/v0.1/README.md) 是历史基线，描述的是尚未接入 Provider/Gateway 的版本。
- 完整长期设计见 [项目架构文档](docs/travel-agent-architecture.md)；实际行为以 v0.2 文档和代码为准。

## 本地运行：默认 Mock（离线、确定性）

需要 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:TRAVEL_PROVIDER = "mock"
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

启动后访问 <http://127.0.0.1:8000/docs>，另开 PowerShell 调用：

```powershell
.\scripts\invoke-hangzhou-example.ps1
```

该脚本使用 [`examples/hangzhou_request.json`](examples/hangzhou_request.json)；该 JSON 为 UTF-8，可通过 `Get-Content -Raw -Encoding UTF8 | Invoke-RestMethod` 使用。服务终端会出现 `search_plan.created`、`tool.started`、`candidate.validated`、`routing.decision` 与 `plan.selected` 等关联事件。

## AMap 模式：显式启用，绝不 fallback

仅在本机终端临时设置自己的 key，绝不写入 `.env.example`、JSON、日志或提交：

```powershell
$env:TRAVEL_PROVIDER = "amap"
$env:AMAP_API_KEY = "replace-with-your-own-key"
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

AMap 模式缺少 key 会在配置阶段失败；选中 AMap 后，真实错误在有限重试后返回 HTTP 503，**不会**静默回退 Mock，也不会伪装成业务 `infeasible`。详见 [运行与测试](docs/v0.2/05-running-and-testing.md)。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
```

可选的真实 AMap smoke 需要同时显式设置 `RUN_AMAP_LIVE=1` 和 `AMAP_API_KEY`：

```powershell
$env:RUN_AMAP_LIVE = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_amap_live_smoke.py
```
