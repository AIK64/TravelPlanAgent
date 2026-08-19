# 09. 链路日志与可观测性

## 1. 为什么需要链路日志

加入日志前，一次请求只能看到“输入 JSON → 等待 → 最终响应”，无法判断 Graph 正在执行哪个节点、生成了哪些候选、Validator 为什么通过，以及是否发生 Replan。

v0.1 现在会输出：

```text
planning.started
→ node.started: load_context
→ context.loaded
→ candidate.generated
→ candidate.validated
→ routing.decision
→ plan.selected 或 replan.started
→ planning.completed
```

## 2. 日志出现在哪个终端

如果使用两个 PowerShell 窗口：

```text
终端 A：运行 Uvicorn
终端 B：运行 Invoke-RestMethod
```

链路日志出现在 **终端 A，即运行 Uvicorn 的终端**。终端 B 主要显示 API 最终响应。

## 3. INFO 与 DEBUG

启动 INFO 日志：

```powershell
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

INFO 展示：

- 请求开始和结束
- 节点进入和完成
- 候选方案摘要
- Validator 结果
- 条件路由
- Replan 轮次
- 最终选择或无解

启动 DEBUG 日志：

```powershell
$env:APP_LOG_LEVEL = "DEBUG"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

DEBUG 在 INFO 基础上增加：

- 每个候选每天的 POI 名称
- 活动开始和结束时间
- 每日费用、交通、步行和疲劳度
- 每一条具体违规及修复建议

`APP_LOG_LEVEL` 必须在启动 Uvicorn 前设置。另一个终端中修改环境变量，不会影响已经运行的服务进程。

## 4. 日志格式

```text
时间 | 级别 | Logger 名称 | event | key=value key=value
```

示例：

```text
2026-08-19 10:20:31 | INFO | travel_agent.graph.workflow |
planning.started | thread_id=abc destination=杭州 days=3 travelers=3 max_replan_rounds=2
```

当前不是完整 JSON 日志，但稳定的事件名和键值字段已经便于阅读、搜索和测试。

## 5. thread_id

同一请求的所有关键日志都带相同 `thread_id`。多个请求并发、日志交错时，可以按它筛选一条完整链路。

它同时也是 LangGraph Checkpoint 的线程标识，因此连接了：

```text
API 请求 → 日志链路 → Graph State → Checkpoint
```

## 6. INFO 事件目录

| 事件 | 说明 | 关键字段 |
|---|---|---|
| `planning.started` | 规划开始 | destination、days、travelers |
| `node.started` | 节点开始 | node、iteration、status |
| `node.completed` | 节点完成 | node、status、结果摘要 |
| `context.loaded` | 加载上下文 | poi_count、source |
| `candidate.generated` | 候选生成 | phase、style、activities、cost、score |
| `candidate.validated` | 候选校验 | valid、violation_count、violation_types |
| `routing.decision` | 条件路由 | next、iteration、valid_candidates |
| `replan.started` | Replan 开始 | iteration、strategy |
| `replan.completed` | Replan 完成 | iteration、candidate_count |
| `plan.selected` | 选择方案 | candidate_id、style、score |
| `planning.infeasible` | 正常无解，WARNING | iterations、candidate_count |
| `planning.completed` | 规划结束 | status、selected、elapsed_ms |
| `planning.failed` | 未处理异常，ERROR | 异常堆栈 |

`phase=initial` 表示首次生成，`phase=replan` 表示重规划生成。

## 7. DEBUG 事件

### candidate.schedule

```text
candidate.schedule |
thread_id=abc candidate_id=relaxed-r0 style=relaxed day=2026-10-02
poi_names=西湖风景名胜区,河坊街
timeline=西湖风景名胜区[12:10-14:10],河坊街[14:31-16:01]
cost=60 travel_minutes=36 walking_meters=1200 fatigue=0.72
```

### candidate.violation

```text
candidate.violation |
candidate_id=balanced-r0 type=budget_exceeded severity=error
message=预计费用 225 元，超过预算 10 元
repair_hint=减少收费活动或提高预算
```

## 8. 正常链路示例

```text
planning.started
node.started node=load_context
context.loaded poi_count=8
node.completed node=load_context
node.started node=create_initial_candidates
candidate.generated style=relaxed
candidate.generated style=balanced
candidate.generated style=exploration
node.started node=validate_candidates
candidate.validated style=relaxed valid=true
routing.decision next=select_best
node.started node=select_best
plan.selected style=relaxed
planning.completed status=completed
```

## 9. Replan 和无解链路

```text
candidate.validated valid=false violation_types=budget_exceeded
routing.decision next=replan iteration=0
replan.started iteration=1
candidate.generated phase=replan
replan.completed iteration=1
candidate.validated valid=false violation_types=budget_exceeded
routing.decision next=mark_infeasible iteration=1
planning.infeasible
planning.completed status=infeasible
```

## 10. 为什么不用 print

`print` 无法按级别过滤，没有统一时间和模块名，不适合并发服务，也不便于测试和接入日志平台。

标准库 `logging` 支持：

- INFO、DEBUG、WARNING、ERROR
- 统一格式
- 按模块创建 Logger
- pytest `caplog` 捕获
- 后续替换 JSON Formatter 或接入 OpenTelemetry

## 11. 为什么不打印完整 State

完整 State 后续可能包含大量 POI、路线矩阵、用户旅行信息、模型消息和工具原始响应。全量打印会导致日志难读、成本升高和隐私风险。

因此只记录状态摘要：数量、ID、评分、违规类型、路由结果和耗时。

## 12. 代码位置

```text
src/travel_agent/logging_config.py  日志级别和格式
src/travel_agent/app.py             应用启动时配置 logging
src/travel_agent/graph/state.py     将 thread_id 放入 State
src/travel_agent/graph/workflow.py  节点、候选、校验和路由日志
tests/test_logging.py               日志行为回归测试
```

## 13. 后续升级

```text
v0.1 可读键值日志
→ JSON 结构化日志
→ request_id / trip_id / user_id
→ Tool 调用耗时和重试日志
→ LLM Token 与成本日志
→ OpenTelemetry Trace
→ LangSmith Agent 轨迹
```

当前实现足以观察 v0.1 Graph 的完整状态流转，不需要提前引入复杂可观测性依赖。
