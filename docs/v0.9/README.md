# v0.9 天气事件驱动局部重规划

v0.9 让已经进入生命周期的计划能够响应外部天气变化。天气不是一个独立 Agent，也不直接重写行程；它先经过标准化、确定性风险分类、事件去重和影响分析，再复用 v0.8 的局部编辑、Route Delta、Hard Validator、Soft Critic、Preview 与审批提交链路。

完整边界和技术取舍见 [设计报告](design.md)。

## 1. 已实现 Graph

```text
Active Plan Version + refresh_weather
  → resolve_weather_location
  → fetch_weather_snapshot
  → classify_weather_risks
  → derive_weather_event
  → deduplicate_weather_event
      ├─ no_change / duplicate / recovered → persist outcome → Interrupt
      └─ new risk event
           → analyze_weather_impact
               ├─ no_plan_impact → persist outcome → Interrupt
               ├─ lock / unknown / budget exceeded → needs_user_attention
               └─ repairable
                    → build_weather_repair_plan
                    → analyze_change_impact
                    → build_local_preview
                    → Approval Interrupt
                         ├─ approve → CAS Commit Vn+1
                         └─ reject  → Keep Vn
```

核心状态对象包括 `WeatherSnapshot`、`DailyWeatherRisk`、`ChangeEvent`、`WeatherImpactResult`、`WeatherRepairPlan` 和 `WeatherEventReceipt`。State 只保存标准化领域对象，不保存高德原始 JSON。

## 2. 关键行为

- `weather-risk-v1` 用确定性规则处理降雨、雷暴、雪冰、高低温、强风、低能见度和 unknown；LLM 不判断硬风险阈值。
- Snapshot Fingerprint 不包含抓取时间和 Provider `reporttime`；同一事实重复刷新不会制造 Event 或 Preview。
- Event Fingerprint 绑定 Session、Base Version、风险前后指纹和受影响日期；同一 Event 只处理一次。
- 活动按室内、户外、混合和未知分类。未知数据不会被当作安全，而是进入 HITL。
- 日期锁或项目锁冲突时不生成越权 Repair；`must_visit` 只能移到有覆盖且风险正常的日期，不能被删除。
- 单次天气 Repair 最多三个动作，沿用最多影响两个日期的局部预算；室内替代 POI 查询次数和候选数也有配置上限。
- Weather Preview 不自动提交。批准后 `event_id`、`snapshot_id` 和策略版本进入新 Plan Version。
- 天气恢复产生 `weather_recovered`，但不会自动反向恢复旧活动，避免覆盖用户后续意图。

## 3. 运行配置

默认 Mock 不需要任何 API Key：

```powershell
$env:TRAVEL_PROVIDER = "mock"
$env:WEATHER_PROVIDER = "mock"
$env:REQUIREMENT_PROVIDER = "mock"
$env:CRITIC_PROVIDER = "mock"
$env:EDIT_PROVIDER = "mock"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

天气配置：

```text
WEATHER_PROVIDER=mock
WEATHER_CACHE_TTL_SECONDS=1800
WEATHER_STALE_MAX_SECONDS=21600
WEATHER_MAX_EVENTS=50
WEATHER_MAX_POI_SEARCHES=4
WEATHER_MAX_ALTERNATIVES=6
WEATHER_EXPOSURE_MIN_CONFIDENCE=0.8
```

`WEATHER_CACHE_TTL_SECONDS` 是天气工具缓存时间；最近一次成功结果在刷新失败后最多按 `WEATHER_STALE_MAX_SECONDS` 作为 `stale` 展示，旧结果不会产生新事件。每个 Session 只保留最近两个标准化快照。

### 3.1 高德地图与天气 API

POI/路线和天气 Provider 独立选择，但都复用同一个本机 `AMAP_API_KEY`：

```powershell
$env:TRAVEL_PROVIDER = "amap"   # 可选：真实 POI 与路线
$env:WEATHER_PROVIDER = "amap"  # 可选：真实地点解析与日级天气
$env:AMAP_API_KEY = "replace-with-your-own-key"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

天气适配器使用高德行政区域查询 `/v3/config/district` 把目的地唯一解析为六位 `adcode`，再调用 `/v3/weather/weatherInfo` 获取日级预报。地点多解、无解、鉴权失败、超时或非法响应都会结构化失败；真实模式不会回退 Mock。API Key、完整请求 URL和 Provider 原始响应不会进入日志、State 或 Repository。

只想验证真实天气时可以保留 `TRAVEL_PROVIDER=mock`，仅设置 `WEATHER_PROVIDER=amap`；反之也可以只使用真实 POI/路线。

### 3.2 LLM API

天气事件识别、风险阈值、影响范围、锁检查和提交均不调用新 LLM。需求解析、计划编辑和 Soft Critic 仍可分别配置 DeepSeek：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm-deepseek]"
$env:DEEPSEEK_API_KEY = "replace-with-your-own-key"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "<当前控制台可用的明确模型名>"
$env:REQUIREMENT_PROVIDER = "deepseek"
$env:EDIT_PROVIDER = "deepseek"
$env:EDIT_MODEL = "<当前控制台可用的明确模型名>"
$env:CRITIC_PROVIDER = "deepseek"
$env:CRITIC_MODEL = "<当前控制台可用的明确模型名>"
```

三个角色使用独立 Prompt、Schema、超时和重试；任一真实模型失败都不会自动回退 Mock。天气 Repair 生成候选后，配置的 Soft Critic 只评价体验质量，不能覆盖 Hard Validator。

## 4. API 使用

先按 [v0.8 文档](../v0.8/README.md) 创建 Plan Session 并选择候选形成 V1。只有 Active Version 存在时才能刷新天气。

### 4.1 刷新天气

```powershell
$refreshBody = @{
  request_id = [guid]::NewGuid().ToString()
  expected_active_version_id = "V1"
  expected_session_revision = 1
} | ConvertTo-Json

$result = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/plan-sessions/$sessionId/weather/refresh" `
  -ContentType "application/json; charset=utf-8" `
  -Body $refreshBody
```

可能结果：

| 结果 | 含义 | 是否修改 Active Version |
|---|---|---:|
| `no_change` / `duplicate` | 风险未变或事件已处理 | 否 |
| `recovered` | 风险恢复，但不自动回滚 | 否 |
| `no_plan_impact` | 风险日期没有可信受影响活动 | 否 |
| `needs_user_attention` | 锁、unknown、预算或无安全 Repair | 否 |
| `preview_created` | 已生成局部 Preview，等待审批 | 否 |
| HTTP 503 | 天气 Provider 不可用 | 否 |

同一个 `request_id` 可以安全重试，会返回已保存结果，不会重复创建 Event、Preview 或 Version。已有待审批 Preview 时刷新返回 409，避免覆盖正在审批的变更。

### 4.2 批准天气 Preview

`preview_created` 返回 `awaiting_change_approval`。使用响应中的 Interrupt 和一次性 Token：

```powershell
$approveBody = @{
  interrupt_id = $result.interrupt.id
  request_id = [guid]::NewGuid().ToString()
  expected_active_version_id = "V1"
  expected_session_revision = $result.session_revision
  action = @{
    kind = "approve_preview"
    preview_id = $result.pending_preview.preview_id
    approval_token = $result.interrupt.payload.approval_token
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/plan-sessions/$sessionId/resume" `
  -ContentType "application/json; charset=utf-8" `
  -Body $approveBody
```

拒绝使用 `reject_preview`。Approval Token、Base Version 和 Session Revision 任一过期都会返回 409。

### 4.3 查询与处理 Attention

```text
GET /api/v1/plan-sessions/{session_id}/weather
GET /api/v1/plan-sessions/{session_id}/weather/events
GET /api/v1/plan-sessions/{session_id}/weather/events/{event_id}
```

对 `needs_user_attention` 事件，用户可以保留当前计划并关闭提醒：

```json
{
  "interrupt_id": "...",
  "request_id": "uuid",
  "expected_active_version_id": "V1",
  "expected_session_revision": 3,
  "action": {
    "kind": "dismiss_weather_event",
    "event_id": "we_..."
  }
}
```

## 5. 失败语义与持久化

| 场景 | HTTP / Outcome | 说明 |
|---|---|---|
| 地点无法唯一解析 | 422 `weather_location_unresolved` | 输入问题，不重试 |
| 超时、限流、上游不可用 | 503 | 有界重试后返回安全错误 |
| 日期未被 Provider 覆盖 | 200 + `uncovered_dates` | 不把缺数据解释为晴天 |
| 锁或未知暴露 | 200 + `needs_user_attention` | 不越权修复 |
| 无安全替代 | 200 + `needs_user_attention` | 不伪装成普通 `infeasible` |
| Preview 硬非法 | `change_rejected` | Active Version 不变 |

SQLite 模式会同时保存 Checkpoint 和 Plan Session 聚合：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,checkpoint-sqlite]"
$env:CHECKPOINT_BACKEND = "sqlite"
$env:CHECKPOINT_SQLITE_PATH = ".data/travel-agent-checkpoints.sqlite3"
$env:PLAN_SQLITE_PATH = ".data/travel-agent-plans.sqlite3"
```

已覆盖“生成天气 Preview → 进程关闭 → 重启 → 恢复同一 Event/Interrupt → 批准 V2”的集成测试。

## 6. Trace

代表性日志事件：

```text
weather.tool.started/completed/retry_scheduled/failed
weather.location.resolved
weather.snapshot.fetched
weather.risk.classified
weather.event.derived
weather.impact.analyzed
weather.alternatives.searched
weather.repair_plan.built
weather.refresh.completed
weather.event.dismissed
preview.hard_validated
locality_guard.completed/failed
version.committed
```

日志使用 Session、Thread、Snapshot、Event、Version、Provider、Operation 和终止原因关联轨迹，不记录完整行程、原始天气 payload、API Key、Prompt 或 Approval Token。

## 7. 测试与离线 Benchmark

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing --cov-fail-under=90
.\.venv\Scripts\python.exe scripts\evaluate_weather_replanning.py
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
.\.venv\Scripts\python.exe -m pip check
```

当前 `weather-fixture-v1` 包含 30 条固定 Fixture，覆盖降雨、高低温、风雪和能见度、恢复、重复事件、锁、`must_visit`、Provider 失败以及 unknown/uncovered。风险准确率会重新执行 `weather-risk-v1`；Graph 行为指标来自固定离线轨迹标注，用于代码与策略回归，不代表真实高德预报准确率或线上 Agent 成功率。

当前实测离线报告：

| 指标 | 结果 |
|---|---:|
| Weather Risk Accuracy | 100% |
| Event Detection F1 | 100% |
| Event Deduplication | 100% |
| Impact Exact Match | 100% |
| 锁定对象 / 未影响日期保持率 | 100% / 100% |
| Hard Constraint Regression | 0% |
| False Replan | 0% |
| Route Reuse | 66.41% |
| Failure Classification | 100% |
| Bounded Termination | 100% |

真实 AMap Smoke 默认不进入 CI；运行真实 Provider 时会产生 API 调用和配额消耗。
