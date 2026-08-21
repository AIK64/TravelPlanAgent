# Travel Agent v0.2：可靠 Tool Use 学习文档

v0.2 的主题不是“接了一个地图 HTTP API”，而是让 Agent 的外部事实获取变成可观察、可验证、有失败边界的 Tool Use：搜索意图进入 Graph，Provider 结果被标准化后写入 State，Validator 再以这些事实决定选择、重规划或无解。

## 已实现与未实现

已实现：杭州离线 Mock 与显式 AMap 两种 Provider 模式；`POIProvider`/`RouteProvider` Protocol；异步 Gateway 的缓存、并发限制和有界重试；标准化的 `POIFacts`、`RouteResult`、provenance；包含 Tool 节点的 LangGraph；`InMemorySaver` Checkpoint；轨迹、契约和 API 测试。

尚未实现：自然语言 LLM Parser、OTA 交易/下单、真实步行路线、天气、持久化 Checkpointer、Human-in-the-loop、长期记忆和生产级评测平台。路线工具当前只请求驾车路线；计划中的步行距离是由驾车距离派生的估算，并会以 warning 告知。

## 推荐顺序

1. [架构与职责边界](01-architecture.md)
2. [Provider 契约与事实来源](02-provider-contracts.md)
3. [Tool Gateway 的可靠性](03-tool-gateway-reliability.md)
4. [异步 LangGraph 轨迹](04-async-langgraph-flow.md)
5. [运行、日志和测试](05-running-and-testing.md)
6. [新手学习与面试复盘](06-learning-guide.md)

学习时始终追问：这个值进入了哪个 State 字段？哪个 Node 读写它？失败时 Conditional Edge 是否仍会被执行？哪条测试或日志可以证明答案？
