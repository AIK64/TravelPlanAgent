# 05. 运行、调用与验证

Python 需要 3.11+。首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 默认 Mock 模式

```powershell
$env:TRAVEL_PROVIDER = "mock"
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

另开 PowerShell 执行：

```powershell
.\scripts\invoke-hangzhou-example.ps1
```

或读取 UTF-8 JSON（在 Windows PowerShell 5.1/7 均可用）：

```powershell
$body = Get-Content -LiteralPath .\examples\hangzhou_request.json -Raw -Encoding UTF8
$null = $body | ConvertFrom-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/plans -ContentType "application/json; charset=utf-8" -Body $body
```

服务终端应依次出现 `search_plan.created`、POI `tool.started`、路线 `tool.started`、`candidate.validated`、`routing.decision` 和 `plan.selected`；响应中的 `metrics.total_travel_minutes` 来自 `RouteResult`，`reason_facts` 会显示路线 provider、路线置信度和 Mock 本地估算/Provider 标准化结果，`assumptions` 则显式说明数据假设。

与 Graph 行为直接相关的公开设置如下；非法值会在启动期失败，而不是运行后静默忽略：

| 环境变量 | 默认值 | 有效范围 / 作用 |
|---|---:|---|
| `POI_QUERY_LIMIT` | 10 | 1–25；每条 POI query 的返回上限 |
| `POI_CANDIDATE_LIMIT` | 12 | 1–100；按优先级合并后的候选事实上限 |
| `POI_MAX_QUERIES` | 12 | 1–100；单请求 POI query/coroutine/首次 Provider 调用总预算 |
| `AMAP_DRIVING_STRATEGY` | 32 | 非负整数；路线收集与物化共用的 strategy |

## AMap 显式模式与安全

不要把 key 写进 `.env.example`、示例 JSON、提交记录或日志。仅在当前终端临时设置：

```powershell
$env:TRAVEL_PROVIDER = "amap"
$env:AMAP_API_KEY = "replace-with-your-own-key"
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

缺少 key 时应用配置会失败；AMap 失败时没有 Mock fallback，重试耗尽后 API 返回 503。真实网络只在可选 smoke 中触发，不属于常规测试。

## 离线测试和可选 smoke

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
```

这些测试覆盖 Mock、AMap fixture 契约、Gateway、轨迹与 API，不需联网。若你明确愿意使用自己的 key 进行一次真实高德检查：

```powershell
$env:RUN_AMAP_LIVE = "1"
$env:AMAP_API_KEY = "replace-with-your-own-key"
.\.venv\Scripts\python.exe -m pytest tests/test_amap_live_smoke.py
```

未同时设置两个变量时该文件会被 skip，普通 `pytest` 不会发出网络请求。

如果本机的既有虚拟环境启用了系统 site-packages，`pip check` 可能报告与本项目无关的全局包冲突。发布前应在不继承系统包的干净项目环境复验（此目录位于已忽略的 `.venv/` 下）：

```powershell
.\.venv\Scripts\python.exe -m venv .\.venv\pip-check
.\.venv\pip-check\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\pip-check\Scripts\python.exe -m pip check
```

检查 `.\.venv\pip-check\pyvenv.cfg` 中的 `include-system-site-packages = false`；预期输出是 `No broken requirements found`。该环境仅用于验证，不应提交。
