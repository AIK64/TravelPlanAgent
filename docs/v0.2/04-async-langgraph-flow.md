# 04. 异步 LangGraph State、节点与 Loop

## State

`TravelState` 的字段依次表达输入、工具事实、候选和控制信息：`thread_id`、`trip`、`search_queries`、`poi_facts`、`planning_pois`、`poi_resolution_issues`、`candidate_drafts`、`route_queries`、`route_results`、`tool_summaries`、`candidates`、`selected_plan`、`iterations`、`pending_replan_round`、`max_replan_rounds`、`status`、`message`。其中工具字段全是标准化模型或摘要，不含 Provider 原始 payload。

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
