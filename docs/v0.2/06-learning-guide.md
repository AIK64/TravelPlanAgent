# 06. 学习、练习与面试复盘

## 60 秒讲清 v0.2

“我把旅行规划的地图事实获取做成显式 LangGraph Tool Use。Graph 先由确定性代码形成检索意图，异步 Gateway 在限流、缓存和有界重试下调用唯一选中的 Provider，把带来源信息的 POI/路线事实写回 typed State；候选经确定性 Validator 后由条件边选择方案、重规划或无解。工具不可用走 503，而非伪装成无解。`thread_id` 串起 API、日志和 Checkpoint，轨迹测试验证中间事件。”

## 建议练习

1. 在 `tests/test_agent_trajectory.py` 为一个工具失败场景先写事件顺序断言，再观察 `ToolUnavailableError` 前 State 停在哪个节点。
2. 把 `UNKNOWN_FACT_POLICY` 从默认值改为 `strict`，阅读 `resolve_poi_facts` 的差异，解释为何 unknown 与 default 不能混用。
3. 用 10 元预算请求观察 `pending_replan_round`、`iterations` 和 `replan.completed`；将最大轮数设为 5，确认业务边界先于 recursion guard。
4. 为缓存命中编写断言：Provider 调用次数不增加，但 `ToolExecutionSummary.cache_hit` 改为 true。
5. 运行 AMap fixture 契约测试，故意破坏一项外部字段，验证原始响应不会穿透到 State。

## 下一项 Agent 能力

下一步应是受结构化输出约束的自然语言 Requirement Parser，或 Human-in-the-loop 的 interrupt/resume 与局部重规划；二者都必须新增 State、Node、Conditional Edge、轨迹测试和评测证据。不要先扩展 OTA、前端或更多城市：它们不能直接证明 Agent 的状态、决策或恢复能力。
