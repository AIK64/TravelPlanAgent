# 04. 异步 LangGraph State、节点与 Loop

## State

`initial_state()` 为每次运行建立下面的完整 `TravelState`。节点只返回自己更新的字段，LangGraph 将更新合并进 Checkpoint；工具字段全是标准化模型或摘要，不含 Provider 原始 payload。

| 字段 | 初始值 / 来源 | 写入节点 | 消费、清理或输出时机 |
|---|---|---|---|
| `thread_id` | API 生成或调用方传入 | 初始值不再改写 | Gateway、日志和 Checkpoint 全程关联 |
| `trip` | `request.trip` | 初始值不再改写 | 检索、默认补全、草案、路线、物化与 Validator 读取 |
| `search_queries` | `[]` | `build_search_plan` | `load_pois` 调用 POI 工具；本轮结束保留于 Checkpoint |
| `poi_facts` | `[]` | `load_pois` | `resolve_poi_facts` 读取；保存标准化 `POIFacts` |
| `planning_pois` | `[]` | `resolve_poi_facts` | 草案、路线收集、物化、校验读取；跨 replan 保留 |
| `poi_resolution_issues` | `[]` | `resolve_poi_facts` | 诊断事实缺失；跨 replan 保留于 Checkpoint |
| `candidate_drafts` | `[]` | `prepare_candidate_drafts` | `load_routes`、`materialize_candidates` 读取；`replan` 清空，再由下一轮重建 |
| `route_queries` | `[]` | `load_routes` | 与结果对应供诊断；`replan` 清空 |
| `route_results` | `{}` | `load_routes` | `materialize_candidates` 读取；`replan` 清空，防止沿用旧草案的路线 |
| `tool_summaries` | `[]` | `load_pois`、`load_routes` 追加 | 记录 Provider、操作、缓存命中和尝试次数；全程保留 |
| `candidates` | `[]` | `materialize_candidates`，`validate_candidates` 覆盖为带校验结果的候选 | 条件路由和最终响应读取；`replan` 清空 |
| `selected_plan` | `None` | `select_best`，`mark_infeasible` | 最终响应读取；`replan` 清空旧选择 |
| `iterations` | `0` | `validate_candidates` 在 pending 轮成功校验后提交 | 条件路由比较业务上限，并进入响应 |
| `pending_replan_round` | `None` | `replan` 写入下一轮；`validate_candidates` 成功后清回 `None` | 让失败时的 Checkpoint 表示未提交事务 |
| `max_replan_rounds` | `request.max_replan_rounds`（0–5） | 初始值不再改写 | `route_after_validation` 读取，限制业务回环 |
| `status` | `"started"` | 各节点更新当前阶段 | 日志、终态和 API 响应读取 |
| `message` | `None` | `replan`、`select_best`、`mark_infeasible` | 最终响应展示可读说明 |

`pending_replan_round` 解决“下一轮尚未完整验证”的中间态：`replan` 先写 pending round、清空过期草案/路线/候选；只有新一轮 `validate_candidates` 成功完成后才把 `iterations` 提交并清空 pending。若该轮工具失败，Checkpoint 仍能准确展示未提交的 pending 状态。

## 节点、边与终止

固定边为 `START → build_search_plan → load_pois → resolve_poi_facts → prepare_candidate_drafts → load_routes → materialize_candidates → validate_candidates`；条件函数 `route_after_validation` 选择 `select_best`、`replan` 或 `mark_infeasible`。`replan → prepare_candidate_drafts`，两个终点节点都到 `END`。

业务预算来自请求的 `max_replan_rounds`，模型限制最大 **5** 轮；`iterations < max_replan_rounds` 才能回环。LangGraph 还以 `recursion_limit=35` 作为独立防护。前者是可解释业务终止条件，后者防止实现错误造成无限图执行。

## 两条真实事件轨迹

成功路径（事件前缀）：

```text
planning.started → node.started → search_plan.created → tool.started
→ poi_context.loaded → candidate_drafts.prepared → routes.loaded
→ candidate.generated → candidate.validated → routing.decision next=select_best
→ plan.selected → planning.completed
```

低预算重规划路径：

```text
candidate.validated → routing.decision next=replan → replan.round_started
→ candidate_drafts.prepared → tool.started → routes.loaded
→ candidate.generated → candidate.validated → replan.completed
→ routing.decision next=mark_infeasible → planning.infeasible → planning.completed
```

`test_validation_feedback_drives_one_bounded_replan` 断言该顺序；`test_maximum_business_replan_budget_stops_before_recursion_guard` 断言 5 次 replan 后停止。`thread_id` 同时关联 API、工具事件、State 和 `InMemorySaver` Checkpoint；它不是用户身份，也不能携带密钥。
