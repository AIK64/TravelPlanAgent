# 02. Provider 契约：事实、来源与模式隔离

`POIProvider.search_pois(POISearchQuery)` 和 `RouteProvider.get_driving_route(RouteQuery)` 是 Protocol；Mock 与 AMap 是各自实现；Gateway 是调用这些实现的统一可靠性边界。Protocol 定义“Agent 可以相信的输出形状”，Provider 负责协议差异，Gateway 不知道 AMap 的原始字段。

## 标准化输出与 provenance

- `POIFacts`：ID、坐标、类别、可用营业时间、均价、建议时长、`provider`、`fetched_at`、置信度和 `field_sources`。
- `RouteResult`：正距离、正分钟数、驾车模式、Provider、置信度、抓取时间。
- `ValueSource` 可为 `provider`、`derived`、`default`、`user_confirmed`。`unknown` 不是伪造数值：费用、时长或营业信息不可可靠解析时保留 `None`；默认补齐的值标为 `default`，Validator 会转成 warning。当前步行接驳距离按 `min(round(driving_distance_meters * 0.12), 2000)` 从驾车距离估算，但它的 provenance **实际标为 `default`，不是 `derived`**，因此同样会产生“步行距离为估算”的 warning；这避免把尚未调用真实步行路线的值误称为 Provider 事实。

`UNKNOWN_FACT_POLICY=assume_with_warning` 允许默认值进入候选并留下假设；`strict` 不把未知事实伪装为已验证数据。`ValidationStatus` 区分 `valid`、`valid_with_warnings`、`invalid`：前两种可交付，选择时完全 `valid` 优先于分数更高的 warning 方案。

## Mock 与 AMap 的严格隔离

`TRAVEL_PROVIDER=mock` 是默认离线模式，运行稳定且不访问网络。`TRAVEL_PROVIDER=amap` 必须同时提供 `AMAP_API_KEY`；运行时只组装选中的 AMap Provider，**没有** Mock fallback。选中 AMap 后超时、连接或可重试上游错误会按预算重试，耗尽后仍是工具不可用，而不是换成 Mock 或返回业务无解。

AMap key 只进入 HTTP client 的私有字段和请求参数，不能进入 State、Checkpoint、API detail 或项目日志。Provider 的错误转换只保留安全的分类、代码、操作名和消息。
