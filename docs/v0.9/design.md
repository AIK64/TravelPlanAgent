# v0.9 天气事件驱动局部重规划设计报告

> 状态：设计完成，待实现  
> 基线版本：v0.8.0  
> 目标版本：v0.9.0  
> 设计日期：2026-08-24  
> 核心能力：`Weather Fact → ChangeEvent → Impact Analysis → Local Replan → Preview → HITL Commit`

## 1. 设计结论

v0.9 只增加一项核心 Agent 能力：**当已激活计划所依赖的天气事实发生有意义的变化时，Agent 能识别变化、确定受影响日期和活动、生成受约束的局部修复 Preview，并在用户批准后提交新版本。**

本版本不新建一个独立“天气 Agent”，也不让 LLM 判断温度、降雨、锁冲突或版本幂等。实现复用 v0.8 的生命周期 Graph，在其中加入显式天气节点：

```text
Active Plan Version
  → Resolve Weather Location
  → Fetch Normalized Forecast
  → Classify Deterministic Risk
  → Derive + Deduplicate ChangeEvent
  → Analyze Weather Impact
  → Search Safe Alternatives
  → Build Weather Repair Plan
  → Apply Local Patch + Route Delta
  → Hard Validator
  → Grounded Soft Critic
  → Locality Guard
  → Plan Preview
  → User Approve / Reject
  → Plan V(n+1)
```

关键取舍：

1. 天气 Provider 提供事实，确定性策略把事实转换为风险和事件；LLM 不决定“是否下雨”“是否高温”。
2. 天气事件可以自动生成 Preview，但不能自动提交计划版本。
3. 日期锁和项目锁优先级高于天气修复；有冲突时进入 HITL，不静默解锁。
4. Provider 失败、预报过期和业务上没有安全替代方案是三种不同语义。
5. 修改范围最多两个日期；超出范围时转为 `requires_new_plan` 或请求用户处理。
6. v0.9 只提供显式刷新 API，不实现常驻 Scheduler、短信推送或生产级事件总线。

## 2. 当前基线与问题

v0.8 已具备：

- 候选选择和 `Plan V1`；
- 日期/项目锁；
- 自然语言和结构化编辑；
- 确定性 Impact Analysis；
- POI Search、Route Delta、Hard Validator、Grounded Soft Critic；
- Preview、Diff、批准/拒绝和 CAS 版本提交；
- request ID 幂等、Checkpoint 与 SQLite 重启恢复。

当前计划一旦生成，外部事实不会主动进入生命周期。即使未来某日由晴转为暴雨，Agent 仍然只会等待用户手工编辑。v0.9 需要解决四个问题：

1. 如何把供应商天气响应转换为稳定、最小、可测试的领域事实。
2. 如何区分“天气响应更新”和“对计划有意义的风险变化”。
3. 如何只修改暴露在风险中的活动及相邻路线。
4. 如何在重复刷新、Provider 故障、锁冲突和服务重启时保持行为一致。

## 3. 目标与非目标

### 3.1 必须完成

- `MockWeatherProvider` 与 `AMapWeatherProvider` 实现同一 Protocol。
- 高德城市/区县 `adcode` 解析和天气预报查询均经过 Tool Gateway。
- Provider 原始响应只存在于 Adapter 调用栈，不进入 Graph State、Repository 或 Prompt。
- 将天气事实确定性映射为 `WeatherRisk`。
- 比较前后风险并生成稳定 `ChangeEvent`，相同事件不重复创建 Preview 或版本。
- 初次观察到 warning/severe 风险时也能生成初始告警事件。
- 只对受影响日期和户外/混合活动生成 `WeatherImpactResult`。
- 对锁冲突、未知活动暴露类型、无安全替代方案进入 HITL。
- 通过 POI Search 获取室内替代项，并只补查变化邻接边的路线。
- 复用 v0.8 的 Hard Validator、Soft Critic、Locality Guard、Diff、Preview 和审批提交。
- 保存天气快照摘要、事件、处理状态和版本来源，支持 SQLite 重启恢复。
- 提供 API、结构化日志、轨迹测试、离线 Benchmark 和有限消融实验。

### 3.2 明确不做

- 不实现天气定时任务、Webhook、消息队列、短信或 App 推送。
- 不实现多城市、多时区和跨境气象供应商的完整覆盖。
- 不实现分钟级降水、雷达图、台风路径、空气质量和灾害预警。
- 不根据日级预报声称某个小时必然下雨。
- 不在天气恢复后自动回滚用户已经批准或后续编辑过的计划。
- 不自动解除用户锁，不自动修改 `must_visit`，不自动提交新版本。
- 不提前实现 v1.0 的统一执行预算和 100+ 综合 Benchmark。
- 不实现 v1.2 的备用天气 Provider、生产事件总线或多实例调度。

## 4. 外部天气能力与数据边界

### 4.1 v0.9 Provider 选择

中国城市场景继续以高德 Web 服务作为真实 Provider，Mock Provider 作为默认离线基线。高德官方天气接口使用：

```text
GET https://restapi.amap.com/v3/weather/weatherInfo
```

请求参数为 `key`、`city=<adcode>`、`extensions=all`、`output=JSON`。官方返回当前至未来数日的日级 `casts`，包含日期、白天/夜间天气、最高/最低温、风向和风力；以响应的 `reporttime` 作为供应商发布时间。

官方文档：[高德 Web 服务天气查询](https://lbs.amap.com/api/webservice/guide/api/weatherinfo)

### 4.2 能力限制

高德基础天气预报不是小时级预报，也不提供降水概率或降水量。因此：

- v0.9 的最小影响粒度是“日期”，不是小时；
- `dayweather` 只证明白天存在某类天气现象，不能精确移动到当天某个无雨小时；
- 超出 Provider 返回日期范围的计划日标记为 `uncovered`；
- 没有数据、未知天气文本或过期数据不能解释为天气良好；
- 更细粒度的预警与多 Provider 能力留给后续版本。

### 4.3 地点解析

天气接口要求 `adcode`，而当前 `TripSpec` 只保证有 `destination`。第一次刷新时执行：

```text
destination
  → weather.resolve_location
  → WeatherLocation(city_name, adcode, timezone)
  → weather.get_forecast
```

地点解析结果缓存在会话 Weather Monitor 中。无法唯一解析时不猜测，返回 `weather_location_unresolved`，计划本身保持不变。

AMap 实现使用行政区域查询的 `keywords=<destination>&subdistrict=0&extensions=base` 获得标准化名称、层级和 `adcode`；若同名结果不能根据目的地及现有行程锚点唯一消歧，则停止。官方文档：[高德行政区域查询](https://lbs.amap.com/api/webservice/guide/api/district/)。

## 5. 关键架构决策

### 5.1 扩展 Lifecycle Graph，不复制计划生命周期

天气变化最终仍然产生 `PlanPreview` 和 `PlanVersion`，所以它必须复用同一会话、锁、版本链和审批语义。天气节点在 `lifecycle/workflow.py` 注册，但实现放在独立 `weather/` 包中，避免工作流文件继续膨胀。

建议提供：

```python
register_weather_nodes(builder, dependencies)
```

父 Graph 中仍能看到每个天气 Node 和 Conditional Edge；不把整个循环隐藏在一个 `handle_weather()` 大函数里。

### 5.2 Snapshot、Risk 和 Event 分层

三层语义不可混用：

- `WeatherSnapshot`：某次成功查询得到的标准化事实。
- `WeatherRisk`：确定性策略基于事实计算出的风险。
- `ChangeEvent`：相邻有效观察之间，对计划有意义的风险变化。

供应商的 `reporttime` 变化不等于计划事件。只有风险签名变化、或第一次发现 warning/severe 风险时，才产生 `ChangeEvent`。

### 5.3 Event 只触发 Preview，不自动 Commit

自动提交会改变用户已经确认的行程，风险过高。天气路径最多自动完成以下工作：

- 分析受影响内容；
- 查找和筛选替代项；
- 生成经过验证的 Preview；
- 展示天气证据、修改理由和 PlanDiff。

只有携带正确 Approval Token 的用户批准动作才能创建 `Plan V(n+1)`。

### 5.4 确定性代码负责硬决策

以下内容不调用 LLM：

- 温度阈值、风力阈值和天气现象分类；
- Event Fingerprint；
- 活动暴露类型规则；
- 日期/项目锁检查；
- affected-days 计算；
- POI 硬过滤、路线失效计算、Hard Validation 和版本 CAS。

LLM 仍只在已有 Grounded Soft Critic 中评价替代计划的体验质量，不获得覆盖硬验证结果的权力。

### 5.5 恢复事件默认不自动还原

天气从风险状态恢复为正常时生成 `weather_recovered` 事件并通知用户，但 v0.9 不自动恢复旧版本中的户外活动。原因是用户可能已经批准替代方案，之后还可能继续编辑；直接反向应用旧 Patch 会破坏最新意图。

用户可以查看历史 Diff，选择保留当前计划或通过现有编辑入口恢复活动。自动安全回滚只有在未来引入更完整变更因果链后再考虑。

## 6. 领域模型

新增 `src/travel_agent/domain/weather_models.py`。

### 6.1 标准化天气事实

```python
class WeatherAvailability(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"

class WeatherPhenomenon(StrEnum):
    CLEAR = "clear"
    CLOUDY = "cloudy"
    RAIN = "rain"
    THUNDERSTORM = "thunderstorm"
    SNOW = "snow"
    ICE = "ice"
    FOG = "fog"
    DUST = "dust"
    UNKNOWN = "unknown"

class WeatherLocation(BaseModel):
    city_name: str
    adcode: str
    timezone: str = "Asia/Shanghai"
    provider: str

class DailyWeather(BaseModel):
    date: date
    day_phenomenon: WeatherPhenomenon
    night_phenomenon: WeatherPhenomenon
    high_celsius: int | None
    low_celsius: int | None
    day_wind_level: int | None
    night_wind_level: int | None

class WeatherSnapshot(BaseModel):
    snapshot_id: str
    location: WeatherLocation
    provider: str
    provider_reported_at: datetime | None
    fetched_at: datetime
    expires_at: datetime
    days: tuple[DailyWeather, ...]
    snapshot_fingerprint: str
```

`WeatherSnapshot` 只在 Provider 成功且 Schema 校验通过时创建。失败信息存入 Monitor 状态，不创建伪快照。

### 6.2 风险模型

```python
class WeatherRiskKind(StrEnum):
    PRECIPITATION = "precipitation"
    THUNDERSTORM = "thunderstorm"
    SNOW_OR_ICE = "snow_or_ice"
    EXTREME_HEAT = "extreme_heat"
    EXTREME_COLD = "extreme_cold"
    STRONG_WIND = "strong_wind"
    LOW_VISIBILITY = "low_visibility"
    UNKNOWN = "unknown"

class WeatherRiskLevel(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    SEVERE = "severe"
    UNKNOWN = "unknown"

class DailyWeatherRisk(BaseModel):
    date: date
    level: WeatherRiskLevel
    kinds: tuple[WeatherRiskKind, ...]
    evidence_codes: tuple[str, ...]
    policy_version: str
    risk_fingerprint: str
```

`evidence_codes` 使用内部稳定代码，例如 `day_rain`、`high_temp_ge_35`，不保存供应商整段响应。

### 6.3 事件模型

```python
class ChangeEventKind(StrEnum):
    WEATHER_ALERT = "weather_alert"
    WEATHER_RISK_CHANGED = "weather_risk_changed"
    WEATHER_RECOVERED = "weather_recovered"

class ChangeEvent(BaseModel):
    event_id: str
    event_fingerprint: str
    kind: ChangeEventKind
    session_id: str
    base_version_id: str
    previous_snapshot_id: str | None
    current_snapshot_id: str
    affected_dates: tuple[date, ...]
    before_risk_fingerprints: tuple[str, ...]
    after_risk_fingerprints: tuple[str, ...]
    created_at: datetime
```

事件 Fingerprint 不包含 `fetched_at` 和 `reporttime`，避免供应商更新时间变化造成重复事件。建议输入为：

```text
session_id | base_version_id | kind | affected_dates | before_risk | after_risk
```

### 6.4 活动暴露、影响和修复

```python
class ExposureKind(StrEnum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    MIXED = "mixed"
    UNKNOWN = "unknown"

class ActivityExposure(BaseModel):
    item_id: str
    exposure: ExposureKind
    rule_id: str | None
    confidence: float

class WeatherImpactResult(BaseModel):
    event_id: str
    affected_dates: tuple[date, ...]
    affected_item_ids: tuple[str, ...]
    affected_route_keys: tuple[str, ...]
    preserved_dates: tuple[date, ...]
    lock_conflicts: tuple[str, ...]
    unknown_exposure_item_ids: tuple[str, ...]
    requires_user_attention: bool
    reasons: tuple[str, ...]

class WeatherRepairActionKind(StrEnum):
    REPLACE_WITH_INDOOR = "replace_with_indoor"
    MOVE_TO_SAFE_DATE = "move_to_safe_date"
    REMOVE_OPTIONAL_ITEM = "remove_optional_item"

class WeatherRepairAction(BaseModel):
    kind: WeatherRepairActionKind
    item_id: str
    target_date: date | None
    replacement_poi_id: str | None
    evidence_codes: tuple[str, ...]

class WeatherRepairPlan(BaseModel):
    event_id: str
    base_version_id: str
    affected_dates: tuple[date, ...]
    actions: tuple[WeatherRepairAction, ...]
    required_tool_operations: tuple[str, ...]
    preserved_day_fingerprints: dict[date, str]
```

### 6.5 Monitor 和处理回执

```python
class WeatherEventStatus(StrEnum):
    OBSERVED = "observed"
    NO_PLAN_IMPACT = "no_plan_impact"
    NEEDS_USER_ATTENTION = "needs_user_attention"
    PREVIEW_CREATED = "preview_created"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISMISSED = "dismissed"

class WeatherEventReceipt(BaseModel):
    event_id: str
    event_fingerprint: str
    status: WeatherEventStatus
    resulting_preview_id: str | None
    resulting_version_id: str | None

class WeatherMonitorState(BaseModel):
    location: WeatherLocation | None
    availability: WeatherAvailability
    latest_snapshot_id: str | None
    previous_snapshot_id: str | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_safe_error_code: str | None
    event_receipts: dict[str, WeatherEventReceipt]
```

Monitor 只保留最近两个标准化快照和有界事件回执；历史事件与版本关联保留 ID，不把无限天气历史塞进 LangGraph State。

### 6.6 Preview 与 Version 来源

为避免把天气修改伪装成普通用户编辑，给 `PlanPreview` 和 `PlanVersion` 增加通用来源元数据：

```python
class ChangeSource(StrEnum):
    USER = "user"
    WEATHER = "weather"

class PlanChangeTrigger(BaseModel):
    source: ChangeSource
    request_id: str
    event_id: str | None = None
    snapshot_id: str | None = None
    policy_version: str | None = None
```

天气 Preview 必须携带 `event_id`、`snapshot_id` 和策略版本；批准后这些字段原样进入新 Plan Version。这样 Diff、Trace、恢复事件和后续 v1.0 评测都能解释“为什么产生这个版本”。用户编辑路径只携带 `source=user`，保持兼容。

## 7. 风险分类策略

新增 `weather/policy.py`，固定 `policy_version="weather-risk-v1"`。第一版建议规则：

| 条件 | 风险种类 | 等级 |
|---|---|---|
| 小雨/阵雨及普通降雨 | `precipitation` | `warning` |
| 暴雨、雷暴、冰雹 | `precipitation/thunderstorm` | `severe` |
| 雪、雨夹雪、冻雨 | `snow_or_ice` | `severe` |
| 日最高温 `>= 35°C` | `extreme_heat` | `warning` |
| 日最高温 `>= 38°C` | `extreme_heat` | `severe` |
| 日最低温 `<= 0°C` | `extreme_cold` | `warning` |
| 风力等级 `>= 6` | `strong_wind` | `warning` |
| 风力等级 `>= 8` | `strong_wind` | `severe` |
| 雾、沙尘等低能见度现象 | `low_visibility` | `warning` |
| 无法映射的天气文本或非法数值 | `unknown` | `unknown` |

这些阈值是产品策略，不冒充气象灾害预警标准。规则必须集中、版本化、有表驱动测试；修改策略版本后，Event Fingerprint 和 Benchmark 报告必须记录新版本。

同一日期存在多个风险时取最高等级，同时保留所有风险种类。`unknown` 不能把已知 severe 降级。

## 8. 活动暴露与影响分析

### 8.1 暴露类型

`ActivityExposureClassifier` 使用 POI 标准化分类和显式规则表判断：

- 博物馆、美术馆、室内展馆、商场等为 `indoor`；
- 公园、山岳、古道、露天广场、动物园等为 `outdoor`；
- 古镇、校园、大型景区等为 `mixed`；
- 无规则命中的项为 `unknown`。

分类结果需要 `rule_id` 和置信度，便于 Trace 和消融。名称匹配只能作为低置信度补充，不能覆盖明确 POI 分类。

### 8.2 影响矩阵

| 风险 | indoor | outdoor | mixed | unknown |
|---|---:|---:|---:|---:|
| 普通降雨 | 保留 | 影响 | 影响 | HITL |
| 雷暴/雪冰/强风 severe | 影响相邻路线 | 影响 | 影响 | HITL |
| 高温 warning | 保留 | 影响 | 影响 | HITL |
| 低能见度 | 保留 | 影响 | 影响 | HITL |
| unknown weather | 不改 | 不改 | 不改 | HITL/提示 |

日级天气无法证明某条实际道路中断。v0.9 对路线的处理只限于：当活动被移动、替换或删除后，失效其前后邻接边并重新查询；不伪造“道路封闭”。

### 8.3 锁和必去项

- 受影响日期被锁：不生成修改该日期的 Repair Plan，返回 `weather_day_lock_conflict`。
- 受影响项目被锁：该项目保持不变，返回 `weather_item_lock_conflict`。
- `must_visit`：不可删除；优先尝试移动到覆盖范围内的低风险日期。
- 移动 `must_visit` 会影响两个日期，必须同时通过锁检查和 `PLAN_MAX_AFFECTED_DAYS`。
- 无安全日期或移动会违反日期/时间锚点时进入 HITL，不把它标记为普通 `infeasible`。

### 8.4 局部性守卫

天气路径必须记录：

- 事件影响日期；
- Repair Plan 允许修改的 item；
- 修改前的 preserved-day fingerprint；
- 原版本和 Preview 的锁 fingerprint；
- 允许失效的 route keys。

Preview 创建前检查所有未影响日期 Hash 不变、锁定项 Hash 不变、未授权 route key 不变。任何越界都令 Preview `invalid`，不得进入批准节点。

## 9. 替代活动与修复策略

### 9.1 修复优先级

对每个受影响活动按以下顺序处理：

1. 如果同一 POI 有可信室内属性，则保留。
2. 对 `must_visit` 尝试移动到 Provider 覆盖范围内的低风险日期。
3. 在同城搜索与用户兴趣相容的室内替代 POI。
4. 对非必去项且没有替代项时，生成“移除可选活动”的 Preview 动作。
5. 任一步骤触发锁、超过两日影响预算或没有可验证结果时，停止并请求用户处理。

### 9.2 室内替代项硬过滤

候选必须同时满足：

- `ExposureKind.INDOOR` 且置信度达到策略阈值；
- 目标日期开放时间已知或按现有 Unknown Fact Policy 明确标注假设；
- 不命中 `TripSpec.avoid`；
- 不与现有项目重复；
- 预算、营业时间、每日时间窗和移动性硬约束可满足；
- 加入后只影响允许的日期；
- POI 和路线 Tool 调用均有标准化结果。

排序可使用确定性得分：兴趣匹配、距离、成本、数据置信度和路线增量。Grounded Soft Critic 只在成型 Preview 后评价整体体验，不负责绕过硬过滤。

### 9.3 Route Delta

替换或移动一个项目时，仅失效：

```text
previous → changed_item
changed_item → next
```

若跨日移动，再处理源日期删除后的新邻接边和目标日期插入后的两条边。所有其他 RouteResult 从 active version 复用。路线查询失败返回外部 Tool 失败，不能把活动误判为业务不可行。

### 9.4 复用现有 EditPatch

`WeatherRepairPlan` 是天气决策和审计对象，不再实现第二套计划修改器。通过确定性 `repair_plan_to_edit_patch()` 将其转换为 v0.8 已支持的 `MOVE_ITEM`、`REMOVE_ITEM` 或 `REPLACE_ITEM`，再复用现有 Patch 应用、Diff 和 Preview 代码。

转换函数必须保持 `event_id → repair action → edit operation` 的对应关系。天气路径不得调用 Edit LLM，也不得生成 v0.8 动作白名单之外的隐式修改。

## 10. Graph State 与节点

### 10.1 State 扩展

在 `PlanLifecycleState` 增加短生命周期控制字段：

```python
weather_snapshot: dict | None
weather_risks: list[dict] | None
weather_event: dict | None
weather_impact: dict | None
weather_repair_plan: dict | None
weather_tool_summary: dict | None
```

这些字段只保存当前执行所需的标准化对象。长期快照、事件回执和版本元数据保存在 Repository；Provider 原始 JSON 不进入 State。

### 10.2 动作扩展

```python
class RefreshWeatherAction(BaseModel):
    kind: Literal["refresh_weather"] = "refresh_weather"

class DismissWeatherEventAction(BaseModel):
    kind: Literal["dismiss_weather_event"] = "dismiss_weather_event"
    event_id: str
```

两者继续使用 `LifecycleResumeRequest.request_id` 和 `expected_session_revision`。刷新动作只允许在已有 active version 且没有 pending Preview 时执行。

### 10.3 完整路径

```text
await_user_action
  → dispatch_action
  → resolve_weather_location
  → fetch_weather_snapshot
  → classify_weather_risks
  → derive_weather_event
  → deduplicate_weather_event
      ├─ identical snapshot / no risk change
      │    → persist_weather_observation
      │    → await_user_action
      ├─ recovered
      │    → persist_weather_notice
      │    → await_user_action
      └─ alert / risk changed
           → analyze_weather_impact
               ├─ no plan impact
               │    → persist_event_receipt
               │    → await_user_action
               ├─ lock / unknown / over budget
               │    → persist_attention_required
               │    → await_user_action
               └─ actionable
                    → search_safe_alternatives
                    → build_weather_repair_plan
                    → apply_weather_patch
                    → resolve_route_delta
                    → materialize_preview_candidate
                    → hard_validate
                    → grounded_soft_critic
                    → weather_locality_guard
                    → build_plan_diff
                    → persist_weather_preview
                    → await_user_action
                         ├─ approve → CAS commit V(n+1)
                         └─ reject  → keep current version
```

### 10.4 Conditional Routing

每条分支使用结构化枚举，不解析日志文本或异常消息：

- `WeatherFetchOutcome.SUCCESS / STALE / UNAVAILABLE`
- `EventDecision.NO_CHANGE / DUPLICATE / RECOVERED / ACTIONABLE`
- `WeatherImpactDecision.NO_IMPACT / ATTENTION / LOCAL_REPLAN`
- `PreviewDecision.VALID / INVALID / TOOL_FAILED`

### 10.5 循环与调用预算

一次刷新最多：

- 1 次地点解析；已缓存则为 0；
- 1 次天气预报查询；
- 每个受影响项目最多 2 个 POI 搜索关键词；
- 总 POI Search 不超过 4 次；
- 替代候选最多 6 个；
- affected days 不超过 2；
- 只查询 Route Delta；
- Soft Critic 最多执行现有 1 轮；
- Graph 新增天气节点总 transition 不超过 20。

任何预算耗尽都产生明确终止原因，不进入无限 Replan。

## 11. Tool Gateway 与 Provider

### 11.1 Protocol

新增 `weather/protocols.py`：

```python
class WeatherProvider(Protocol):
    name: str

    async def resolve_location(self, destination: str) -> WeatherLocation: ...

    async def get_forecast(
        self,
        location: WeatherLocation,
    ) -> WeatherSnapshot: ...
```

Provider 方法返回供应商无关对象，抛出已有分类体系兼容的 `ToolProviderError`。

### 11.2 Gateway

`WeatherToolGateway` 复用现有缓存、Semaphore、Timeout、Retry 和 `ToolResult[T]` 语义。建议独立类而不是继续扩大地图 `ToolGateway`，但共享基础执行器或提取 `ReliableToolExecutor`，避免复制可靠性代码。

缓存键：

```text
weather-location:{provider}:{normalized_destination}
weather-forecast:{provider}:{adcode}:daily:v1
```

建议 TTL：

- 地点解析：24 小时；
- 日级天气：30 分钟；
- 可显示的旧成功快照最长保留 6 小时，但标记 `stale`，不可产生新事件。

### 11.3 AMap Adapter

实现要求：

- 复用现有 `AMapClient`，API Key 不进入对象 repr 或日志；
- 地点解析调用行政区域查询并输出唯一 `adcode`；多结果或无结果显式失败；
- `extensions=all`；
- 校验 `status=1`、`infocode=10000`、forecast/casts 结构；
- 中文天气文本只在 Adapter 内映射为 `WeatherPhenomenon`；
- 温度和风力转换失败时保留 `None` 并产生数据质量标记，不用 0 代替；
- `reporttime` 无时区信息时按 `Asia/Shanghai` 解释，再保存为 timezone-aware datetime；
- 不记录响应正文、请求 URL 中的 key 或原始 params。

### 11.4 Mock Provider

Mock Provider 支持 fixture 驱动：

- 晴 → 雨；
- 雨 → 暴雨；
- 晴 → 高温；
- 风险 → 恢复；
- 重复快照；
- 未知天气文本；
- 超时、限流、鉴权和非法响应。

Mock 默认可离线重复运行，不依赖系统日期；测试显式传入 clock。

## 12. 持久化、一致性与幂等

### 12.1 Repository 扩展

第一版继续使用 Plan Session 聚合保证单机原子性，给 `PlanSessionRecord` 增加：

```python
weather_monitor: WeatherMonitorState | None
weather_snapshots: dict[str, WeatherSnapshot]  # 最多两个
weather_events: dict[str, ChangeEvent]         # 有界
```

事件回执和 Preview/Version 仍通过一次 `PlanRepository.save(expected_revision=...)` CAS 保存。不要先写天气数据库、再写计划数据库形成跨库半提交。

### 12.2 三层幂等

1. `request_id`：同一 API 请求重放返回同一 ActionReceipt。
2. `snapshot_fingerprint`：完全相同的标准化天气不重复处理。
3. `event_fingerprint`：风险语义相同，即使 Provider reporttime 改变，也不重复创建 Preview/Version。

如果同一 Event 已有 pending Preview，刷新返回该 Preview；若已批准、拒绝或 dismiss，返回对应 receipt。

### 12.3 并发

- 同一 session 的刷新、编辑、锁操作和审批复用生命周期服务的 session lock。
- API 必须携带 `expected_session_revision`。
- 以旧版本为基础生成的天气 Preview 在审批时检查 `base_version_id` 和 revision；计划已被用户编辑则 Preview 标记 `stale`。
- 天气刷新时若已有 pending Preview，返回 409 `pending_preview_exists`，不覆盖用户正在审批的变更。

### 12.4 历史边界

- 标准化快照最多保存最近两个；
- Event 和 Receipt 默认最多保存 50 个；
- Plan Version 继续使用 v0.8 的 20 版本上限；
- 达到上限时不删除活跃版本，返回明确的 `plan_version_limit_reached`。

## 13. API 设计

### 13.1 刷新天气

```http
POST /api/v1/plan-sessions/{session_id}/weather/refresh
Content-Type: application/json

{
  "request_id": "weather-refresh-001",
  "expected_session_revision": 5
}
```

成功但没有变化：

```json
{
  "status": "active",
  "weather": {
    "availability": "fresh",
    "outcome": "no_change",
    "snapshot_id": "ws_...",
    "event": null
  },
  "active_version_id": "pv_...",
  "session_revision": 6
}
```

产生 Preview 时返回 `awaiting_change_approval`，复用 v0.8 的 `preview_id`、PlanDiff 和 Approval Interrupt。后续仍调用统一 Resume API：

```http
POST /api/v1/plan-sessions/{session_id}/resume
```

### 13.2 查询天气状态

```http
GET /api/v1/plan-sessions/{session_id}/weather
GET /api/v1/plan-sessions/{session_id}/weather/events
GET /api/v1/plan-sessions/{session_id}/weather/events/{event_id}
```

查询只返回标准化天气、风险、事件状态和关联 Preview/Version ID，不返回 Provider 原始响应。

### 13.3 Dismiss

用户明确接受天气风险、不需要修改时：

```http
POST /api/v1/plan-sessions/{session_id}/resume

{
  "request_id": "dismiss-weather-001",
  "expected_session_revision": 6,
  "action": {
    "kind": "dismiss_weather_event",
    "event_id": "we_..."
  }
}
```

Dismiss 只改变 Event Receipt，不改变 Plan Version，且必须出现在审计轨迹中。

### 13.4 HTTP 与领域错误

| HTTP | code | 含义 |
|---:|---|---|
| 404 | `plan_session_not_found` | 会话不存在 |
| 409 | `session_revision_conflict` | 并发版本冲突 |
| 409 | `pending_preview_exists` | 已有待审批 Preview |
| 409 | `weather_event_already_resolved` | 事件已处理 |
| 422 | `weather_location_unresolved` | 目的地不能唯一解析 |
| 422 | `weather_impact_requires_user_action` | 锁、未知暴露或影响范围过大 |
| 503 | `weather_provider_unavailable` | 无可用新鲜天气事实 |
| 503 | `weather_provider_authentication_failed` | Key 无效或权限不足 |

Provider 失败时保留旧计划和旧快照。旧快照可以展示为 stale，但不能被伪装成刷新成功或用于生成新事件。

## 14. 失败语义

### 14.1 Tool Failure

包括超时、连接失败、限流、鉴权、权限、非法响应和上游不可用。结果：

- 记录分类、attempt_count、elapsed_ms 和 safe code；
- 不创建新 WeatherSnapshot 或 ChangeEvent；
- 不修改 active version；
- 有旧快照时保持并标记 stale；
- 对显式刷新 API 返回 503。

### 14.2 Data Uncovered

行程日期超出预报范围不是 Provider Failure。对应日期标记 `uncovered`，不做天气重规划。API 可成功返回，但必须带 coverage warning。

### 14.3 No Plan Impact

天气有变化但计划当天只有可信室内活动，事件状态为 `no_plan_impact`。这是正常业务结果，不创建 Preview。

### 14.4 No Safe Alternative

有天气影响但没有满足开放时间、预算、兴趣和路线约束的室内替代项，状态为 `needs_user_attention`。它不是 Tool Failure，也不应直接把整个旅行标记为 `infeasible`。

### 14.5 Invalid Preview

局部 Patch 应用后 Hard Validator 或 Locality Guard 失败，Preview 标记 `invalid` 并保留验证证据；不进入批准路径，不执行未经用户授权的多轮删除式修复。

## 15. 可观测性与日志

新增事件：

```text
weather.refresh.started
weather.location.cache_hit
weather.location.resolved
weather.fetch.started
weather.fetch.completed
weather.fetch.failed
weather.snapshot.persisted
weather.risk.classified
weather.event.created
weather.event.duplicate
weather.event.no_change
weather.impact.analyzed
weather.impact.attention_required
weather.alternatives.searched
weather.repair_plan.built
weather.route_delta.resolved
weather.preview.created
weather.preview.invalid
weather.event.dismissed
weather.event.approved
weather.event.rejected
```

公共关联字段：

```text
session_id
lifecycle_thread_id
run_id
request_id
active_version_id
snapshot_id
event_id
event_fingerprint_prefix
preview_id
provider
policy_version
node
transition_count
```

关键指标字段：

- snapshot age、covered dates、风险等级；
- affected/preserved day count；
- affected/locked/unknown item count；
- POI 和 Route 调用数、缓存命中、重试和延迟；
- route reuse count/rate；
- Hard Validation 状态、Soft Critic 状态和 Locality Guard 状态；
- 终止原因。

禁止记录：API Key、完整请求 URL、供应商原始响应、完整用户行程、Approval Token、完整 Prompt 和模型原始响应。

## 16. 测试设计

### 16.1 单元测试

- 天气现象中文文本到枚举的映射；
- 温度、风力边界值；
- 多风险取最高等级；
- unknown 不降级已知 severe；
- Snapshot 和 Event Fingerprint 稳定性；
- reporttime 变化不产生重复事件；
- 初次 warning/severe 观察生成事件，初次 normal 不生成；
- 风险恢复生成 notice，不自动回滚计划；
- POI 暴露分类和规则版本；
- 锁、must_visit、affected-days 和 route delta 计算；
- 未影响日期、锁和未授权路线 fingerprint 守卫。

### 16.2 Provider Contract

对 Mock 和 AMap 适配器执行同一契约：

- 成功解析地点和预报；
- 空结果、多地点、非法 status/infocode；
- casts 缺失或类型错误；
- 非法温度、风力和 reporttime；
- Timeout、Connect、429、401/403、5xx；
- 响应和异常中不泄露 API Key；
- 返回对象不包含 Provider 原始 payload。

AMap 测试只使用 `httpx.MockTransport`，Live Smoke 默认跳过。

### 16.3 Graph 轨迹测试

至少覆盖：

1. 晴转雨 → 识别户外项 → 搜索室内项 → Route Delta → Preview。
2. 晴转高温 → 只修改受影响日期。
3. 风险只落在室内日 → `no_plan_impact`。
4. 同一快照重复刷新 → 不重复 Event。
5. reporttime 变化但风险不变 → 不创建 Preview。
6. 日期锁冲突 → attention，锁和计划不变。
7. `must_visit` 可安全跨日移动 → 两日局部 Preview。
8. `must_visit` 无安全日期 → attention，不删除。
9. 没有室内替代项 → attention，不伪装 Tool Failure。
10. POI Search/Route/Weather 超时 → 正确失败分类。
11. Weather stale → 不生成事件。
12. Preview 批准 → V2 source_event_id 正确。
13. Preview 拒绝 → active version 不变。
14. 用户编辑后批准旧天气 Preview → stale/409。
15. SQLite 重启 → Event、pending Preview 和 Interrupt 可恢复。

轨迹断言不仅检查最终文本，还检查节点顺序、Conditional Edge、Tool 参数、调用次数、缓存命中、State 回写和终止原因。

### 16.4 API、安全与并发

- refresh/get/events/dismiss Contract；
- 缺失会话、revision 冲突、pending Preview；
- 同 request ID 重放；
- 同 Event 不同 request ID 重放；
- refresh/edit/lock 并发串行化；
- 错误响应不含 Key、原始响应和 Approval Token；
- 配置校验和日志脱敏。

## 17. Benchmark 与消融

### 17.1 数据集

新增：

```text
evals/weather/cases.jsonl
scripts/evaluate_weather_replanning.py
```

至少 30 条固定 Fixture：

- 降雨 6 条；
- 高温/低温 5 条；
- 强风、雪冰、低能见度 4 条；
- 风险恢复 3 条；
- 重复事件 3 条；
- 锁和 must_visit 4 条；
- Provider/Tool 失败 3 条；
- unknown/uncovered 2 条。

每条标注：期望风险、事件类型、affected dates/items、锁冲突、预期动作、允许 Tool 调用、是否创建 Preview/Version 和终止原因。

### 17.2 指标

```text
weather_risk_accuracy
event_detection_precision / recall / f1
event_deduplication_rate
impact_exact_match_rate
locked_artifact_preservation_rate
unaffected_day_preservation_rate
hard_constraint_regression_rate
false_replan_rate
route_reuse_rate
preview_correctness_rate
commit_correctness_rate
failure_classification_accuracy
bounded_termination_rate
```

发布门槛：

- 锁定项保持率 100%；
- 未影响日期保持率 100%；
- completed Preview 已知硬约束满足率 100%；
- 重复 Event 创建新版本数为 0；
- normal/no-impact 用例错误重规划率为 0；
- Provider 失败误分类为天气良好或业务 infeasible 的数量为 0；
- 所有用例有界终止；
- Event Detection F1 和 Impact Exact Match 均至少 95%；
- 3 日及以上可评测用例的路线复用率目标至少 60%，并如实报告实际值。

### 17.3 有限消融

v0.9 只做与本能力直接相关的四组消融：

1. `event_dedup=on/off`：比较重复 Preview/Version 数。
2. `local_replan/full_replan`：比较未影响日期保持率、路线调用数和延迟。
3. `route_cache=on/off`：比较 Tool 调用数和延迟，不声称改变计划质量。
4. `soft_critic=on/off`：比较替代项软质量和成本，硬约束结果必须相同。

真实 AMap 报告必须记录 Provider、调用日期、策略版本和数据集版本；固定 Mock 结果不得表述为真实线上准确率。

## 18. 配置设计

`.env.example` 增加：

```dotenv
WEATHER_PROVIDER=mock
WEATHER_CACHE_TTL_SECONDS=1800
WEATHER_STALE_MAX_SECONDS=21600
WEATHER_MAX_EVENTS=50
WEATHER_MAX_POI_SEARCHES=4
WEATHER_MAX_ALTERNATIVES=6
WEATHER_EXPOSURE_MIN_CONFIDENCE=0.8
```

约束：

- `WEATHER_PROVIDER=amap` 时必须存在 `AMAP_API_KEY`；
- 超时、重试、并发和退避复用现有 Tool Gateway 设置；
- `WEATHER_STALE_MAX_SECONDS` 必须大于 cache TTL；
- 搜索次数和候选数必须为正且有安全上限；
- 默认 Mock 模式必须支持离线开发和 CI。

不新增天气 LLM 配置。DeepSeek 继续只用于需求解析、编辑解析和 Soft Critic，不承担天气风险规则。

## 19. 计划代码结构

```text
src/travel_agent/
  domain/
    weather_models.py
  weather/
    __init__.py
    protocols.py
    errors.py
    gateway.py
    policy.py
    events.py
    exposure.py
    impact.py
    repair.py
    workflow.py
    evaluation.py
    providers/
      __init__.py
      mock.py
      amap.py
  lifecycle/
    workflow.py              # 注册天气节点和路由
    service.py               # refresh/query/dismiss application service
    repository.py            # 持久化扩展
  domain/lifecycle_models.py # action、status、trigger 元数据扩展
  api/
    routes.py
    errors.py
  config.py
  runtime.py

evals/weather/
  cases.jsonl

scripts/
  evaluate_weather_replanning.py

tests/
  test_weather_policy.py
  test_weather_events.py
  test_weather_exposure.py
  test_weather_provider.py
  test_weather_gateway.py
  test_weather_impact.py
  test_weather_workflow.py
  test_weather_api.py
  test_weather_restart.py
  test_weather_benchmark.py
```

## 20. 分阶段实施计划

### Phase A：模型、策略与 Provider

- 新增天气领域模型和配置；
- 完成 Mock Provider；
- 完成 AMap 地点解析和天气 Adapter；
- 完成风险映射、Fingerprint 和 Contract 测试。

验收：不接 Graph 也能稳定得到标准化 Snapshot/Risk，失败语义完整。

### Phase B：事件、持久化与幂等

- 实现 Snapshot 对比和 ChangeEvent；
- 扩展 Plan Session Repository；
- 实现 request/snapshot/event 三层幂等；
- 完成 SQLite 重启测试。

验收：重复刷新不创建重复 Event，重启后状态一致。

### Phase C：Impact 与局部 Repair

- 实现活动暴露分类；
- 实现天气 Impact、锁和 must_visit 规则；
- 实现室内替代搜索与确定性排序；
- 复用 Route Delta、Materialize 和 Validator。

验收：只修改标注影响范围，锁和未影响日期 Hash 不变。

### Phase D：Lifecycle Graph 与 HITL

- 注册天气节点、Conditional Edge 和动作；
- 生成带天气证据的 PlanPreview；
- 复用 Approval/Reject/CAS Commit；
- 支持 attention 和 dismiss。

验收：完整轨迹可从日志和 Checkpoint 重放，任何事件都不会自动提交。

### Phase E：API、Benchmark 与发布门禁

- 增加 refresh/query/events API；
- 完成 API、安全、并发和故障测试；
- 增加 30+ Fixture 和消融脚本；
- 更新 README、使用方法和实际评测结果。

验收：全量覆盖率保持至少 90%，Benchmark 达到本设计门槛。

## 21. Definition of Done

v0.9 完成必须同时满足：

1. 真实 AMap 和 Mock 天气均经过统一 Protocol/Gateway。
2. 原始响应不进入 State、Repository、Prompt 或日志。
3. Weather Snapshot、Risk、ChangeEvent 职责清晰且强类型。
4. Graph 中能看到刷新、分类、事件、去重、Impact、Repair、验证、Preview 和审批路径。
5. 重复刷新、旧 request 和服务重启不会重复创建 Preview/Version。
6. 锁定项、must_visit 和未影响日期保持率达到 100%。
7. Provider Failure、uncovered、no impact、no safe alternative 和 invalid Preview 正确区分。
8. Tool/LLM 调用次数和所有循环有明确预算。
9. 关键节点、工具、路由、重试、缓存、审批和终止原因具有结构化日志。
10. 至少 30 条离线 Fixture 可重复运行，覆盖率门禁不低于 90%。
11. README 给出 Mock、AMap、刷新、审批和 Benchmark 的完整命令。
12. 文档只陈述实际测得的结果，不把 Mock Fixture 指标包装成真实 Provider 效果。

## 22. 面试演示脚本

推荐演示主线：

```text
Plan V1：杭州三日游，Day 2 包含西湖户外活动
  → Mock/AMap 天气从正常变为 Day 2 降雨 warning
  → Snapshot 标准化，Risk Policy 输出 precipitation
  → ChangeEvent Fingerprint 首次出现
  → Impact Analyzer 只标记 Day 2 和相邻路线
  → 搜索同城室内替代项
  → Route Delta 只补查改变的邻接边
  → Hard Validator + Grounded Soft Critic + Locality Guard
  → 展示 V1/Preview Diff、天气证据、路线复用率
  → 用户批准后提交 V2
  → 用同一天气再次刷新，证明不会产生 V3
```

失败恢复演示：锁定 Day 2 后重复同一场景，Graph 在 Impact 节点识别 lock conflict，保留 V1 并请求用户决定；再模拟 Provider timeout，展示 503、stale 标记和“外部失败不等于天气良好”。

面试重点解释：

- 为什么天气不是一个独立 Agent；
- 为什么事件不是每次 API 响应；
- 为什么硬风险规则不用 LLM；
- 为什么自动生成 Preview 但不自动 Commit；
- 如何用 Fingerprint、CAS 和 Checkpoint 保证幂等恢复；
- 如何用轨迹和 Benchmark 证明局部重规划没有破坏其他日期。

## 23. 对 v1.0 的接口承诺

v0.9 为 v1.0 统一评测与运行治理留下：

- 稳定的 `ChangeEvent`、risk/event fingerprint 和事件终止原因；
- 天气节点级 Tool/LLM/Transition 统计；
- 可计算的 affected-days、route reuse 和 locality 指标；
- Provider 故障注入 Fixture；
- 可统一纳入 `ExecutionBudget` 的刷新调用预算；
- 可与直接 LLM 全量重规划 Baseline 比较的事件轨迹。

v1.0 不需要重写天气循环，只需把它纳入统一 Run、Trace、Budget、Benchmark 和发布门禁。

## 24. 最终取舍

v0.9 的项目价值不在于“多接了一个天气 API”，而在于证明 Agent 能处理**运行后发生的外部事实变化**：它知道什么变化值得形成事件，知道哪些状态受影响，能有界调用工具生成局部修复，能保留用户锁和未影响内容，也能在证据不足或工具失败时停止并把决定交还用户。

这使项目从“会生成计划”继续演进为“会维护计划”，同时仍把提交权、硬约束和安全边界留在确定性系统与用户手中。
