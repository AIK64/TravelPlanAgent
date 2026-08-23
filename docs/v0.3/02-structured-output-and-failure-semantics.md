# Structured Output 与失败语义

## Provider Contract

`RequirementModel` 暴露 `name`、`model`、`prompt_version` 和异步 `parse(request)`。Mock、OpenAI 与 DeepSeek 适配器都返回 `RequirementProviderOutput(draft, token usage)`，上层不依赖 SDK 类型。

OpenAI 适配器使用 Responses API 的 Pydantic Structured Outputs，将输出约束为 `RequirementDraft`；Prompt 明确要求：

- 只抽取用户明确表达或能依据 `reference_date`、`timezone` 直接规范化的字段；
- 未知值返回 `null` 或空列表；
- 不猜测坐标、车站、酒店、预算、人数或偏好；
- 不判断可行性，不生成计划。

这不是把硬约束交给模型。日期顺序、抵达/离开边界、人数、预算、每日时间窗仍由 `validation.py` 的确定性规则判断。

DeepSeek 当前兼容的是 Chat Completions JSON Output，而不是 OpenAI Responses Pydantic parse。独立 `DeepSeekRequirementModel` 使用 `response_format={"type":"json_object"}`，Prompt 明确包含 JSON Schema，并再次用 `RequirementDraft.model_validate` 检查字段。合法 JSON 不等于合法业务结构，因此二次校验不能省略。

DeepSeek Provider 使用 `extra_body={"thinking":{"type":"disabled"}}`。需求抽取是边界明确的结构化任务，关闭思考模式可减少无关推理 token 和延迟；模型故障、输出截断、空内容、非法 JSON 与 schema mismatch 都有独立失败代码。

## Gateway 可靠性

Requirement Gateway 与 Tool Gateway 的预算独立：模型调用默认超时 20 秒、最多 2 次；OpenAI SDK 内置重试被关闭，避免双重重试。只对 timeout、connection、rate limit、上游 5xx、incomplete 或无效结构等可恢复错误重试；认证、权限、拒绝和无效请求直接失败。

安全日志只包含 `thread_id`、provider、model、prompt version、字符数、尝试次数、耗时和 token 数。原始需求、Prompt 全文、模型输出、API key 与 Provider 异常正文不会写日志。

## 对外失败语义

| 场景 | HTTP/状态 | 含义 |
|---|---|---|
| 缺必要字段或字段冲突 | 200 / `needs_clarification` | 用户需求尚不完整 |
| 地图调用成功但地点未找到/有歧义 | 200 / `needs_clarification` | 需要更具体地点 |
| Requirement Provider 超时、限流、无效响应 | 503 | 模型基础设施不可用 |
| 地图 Provider 失败 | 503 | 工具基础设施不可用 |
| 所有规划候选违反业务硬约束 | 200 / `infeasible` | 需求完整，但当前候选不可行 |

禁止将 Provider 故障伪装成 `needs_clarification` 或 `infeasible`，也禁止 OpenAI、DeepSeek 或 AMap 模式静默回退 Mock。这样日志和指标才能区分用户问题、模型问题、工具问题与规划问题。
