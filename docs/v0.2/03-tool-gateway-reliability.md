# 03. Tool Gateway：可靠性是 Tool Use 的一部分

Gateway 对每次 POI/路线调用记录相同 `thread_id` 的 `tool.started`、`tool.retry_scheduled`、`tool.completed`、`tool.cache_hit` 或 `tool.failed` 事件，并返回 `ToolResult`。未命中缓存且成功时发出 `tool.completed`（`cache_hit=false`）；缓存命中时发出 `tool.cache_hit`（`cache_hit=true`），不会再额外发出 `tool.completed`。成功结果包含 Provider、缓存命中、尝试次数和耗时；失败结果包含安全 `ToolErrorInfo`。

| 情形 | Gateway 行为 | Graph / HTTP 语义 |
|---|---|---|
| 缓存仍有效 | 返回缓存结果，标记 `cache_hit=true` | 可继续规划 |
| 并发相同 key | 合并在途加载 | 避免重复 Provider 请求 |
| 可重试 timeout/connection/upstream | 指数退避加 jitter；合法 `Retry-After` 作为恢复窗口；最终等待不超过 `TOOL_MAX_BACKOFF_SECONDS`，至多 `TOOL_MAX_ATTEMPTS` | 成功则继续 |
| 认证、权限、非法响应 | 不重试或立即耗尽 | `ToolUnavailableError` → 503 |
| Provider 成功但没有 POI | 空事实是业务数据 | 可由规划/验证给出无解 |
| 约束无法满足 | 不调用 Gateway 解决 | `mark_infeasible`，HTTP 200 |

缓存容量由 `TOOL_CACHE_MAX_ENTRIES` 约束；POI 与路线 TTL 分别由 `POI_CACHE_TTL_SECONDS`、`ROUTE_CACHE_TTL_SECONDS` 控制；`TOOL_MAX_CONCURRENCY` 的 semaphore 限制同时在飞的实际请求。POI 总调用还由 `POI_MAX_QUERIES` 独立约束：SearchPlan 先按 must-visit、再按 interests 稳定去重，裁剪后才创建 coroutine，因此并发限制和总量预算职责不同。失败结果不缓存，避免短暂故障被固定。

AMap 仅从 HTTP 429/502/503/504 的 `Retry-After` 读取非负有限秒数；空值、负数、NaN、Inf 或畸形值会被忽略。Header、完整 response、请求 URL 和 key 都不会进入日志、typed error 或异常链。

关键边界：`ToolUnavailableError` 表示外部事实不可获得，API 返回 503；`infeasible` 表示在已获得的事实下没有满足硬约束的计划，返回 200。混淆二者会让 Agent 在不可信事实下假装做出了决策。测试 `test_amap_failure_retries_selected_provider_without_mock_fallback` 与 `test_tool_failure_returns_503_with_only_safe_detail` 覆盖这一点。
