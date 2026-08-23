# Requirement State 与 Graph

## 目标与职责边界

自然语言入口必须先回答“信息是否足以安全构造领域模型”，不能直接要求 LLM 输出完整行程。v0.3 因此把 Intake 拆成可观察节点，并复用 v0.2 已验证的规划子流程。

| Graph 节点 | 输入/输出 | 决策职责 |
|---|---|---|
| `parse_requirement` | 原文 → `RequirementDraft` | 只做语义抽取和日期规范化 |
| `validate_requirement` | Draft → `RequirementIssue[]` | 确定性检查缺失、冲突和非法值 |
| `resolve_anchors` | 地点名 → POI ToolResult | 通过 Tool Gateway 获取标准化事实 |
| `evaluate_anchors` | ToolResult → Resolution/Issue | 唯一匹配或转澄清 |
| `assemble_trip_spec` | Draft + Resolution → `TripSpec` | 应用显式默认值并建立严格领域对象 |
| `execute_planning` | `TripSpec` → `PlanningResponse` | 调用既有 Plan/Tool/Validate/Replan 流程 |
| `request_clarification` | Issue → 问题列表 | 稳定、去重地结束本轮 |

## State 最小化

`RequirementState` 保存原始请求、结构化 Draft、问题、锚点检索计划、标准化结果摘要、最终 `TripSpec` 和规划响应。Provider 原始响应、地图原始 JSON 和密钥不会进入 State。

大型原始数据由 Provider/Gateway 边界处理；Graph 只接收 `RequirementDraft`、`ToolResult`、`AnchorResolution` 等强类型对象。模型与工具轨迹分别保存 `RequirementExecutionSummary` 和 `ToolExecutionSummary`，避免为了观测把完整 payload 塞回 Prompt 或 Checkpoint。

当前 `InMemorySaver` 会在进程内保存 `NaturalPlanningRequest.text`，便于开发期检查短期上下文。生产化前必须增加保留期、访问控制、脱敏和持久化策略，不能把它误称为长期 Memory。

## 路由与终止条件

- `validate_requirement` 后只要存在 blocking issue，就进入 `request_clarification`。
- 锚点工具成功但没有唯一匹配时，同样进入澄清，而不是猜坐标。
- 模型/工具故障抛出 unavailable 错误，由 API 返回 503，不进入澄清，也不进入 `infeasible`。
- 只有成功构造 `TripSpec` 后才进入规划子流程；规划循环继续受 `max_replan_rounds` 和现有工具预算约束。

这使面试演示能够在 Graph 图、日志和 Checkpoint 中逐步解释“语义抽取—硬约束—工具事实—规划决策”的职责边界。
