# 评测、运行与演示

## 离线 Benchmark

`evals/requirements/cases.jsonl` 当前有 30 条中文案例，分为：

- 完整需求；
- 缺抵达信息；
- 缺离开信息；
- 缺日期；
- 日期或交通时间冲突。

运行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_requirement_parser.py
```

输出指标包括字段准确率、blocking field precision/recall、澄清路由准确率、整例准确率、Provider 失败数和平均耗时。报告只包含 case ID 与差异字段，不回显需求原文。

默认 `mock-requirement-v1` 在这组固定回归数据上应为 100%。这只能证明 Mock Fixture 和确定性校验没有回归，不能代表开放域自然语言效果。使用 OpenAI 或 DeepSeek 时，应在固定 Provider、模型名、Prompt version、数据集版本和运行日期下保存结果，并增加口语化、相对日期、否定、跨年、歧义地点与对抗输入；不要把不同模型或 Prompt 的结果混在一起。

DeepSeek 全量评测示例：

```powershell
$env:REQUIREMENT_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "replace-with-your-own-key"
$env:DEEPSEEK_MODEL = "deepseek-v4-flash"
.\.venv\Scripts\python.exe scripts\evaluate_requirement_parser.py
```

该命令会对 30 条案例发起真实调用并产生费用，普通测试不会自动执行。

## 轨迹测试证明什么

`tests/test_requirement_workflow.py` 检查：

- 完整输入依次出现 `requirement.parse.completed`、`requirement.validated`、`anchors.resolved`、`trip_spec.assembled`，随后进入 `search_plan.created`、`candidate.validated` 和 `plan.selected`；
- 缺字段不调用地图或路线工具，直接产生澄清；
- 地点工具成功但空结果产生 `not_found`；
- 地点工具失败抛出 unavailable，不进入规划；
- Graph 暴露所有 intake 和 anchor routing 节点。

API 测试同时验证自然语言完整/澄清响应、模型故障 503，以及 OpenAI Provider 必须显式选择且客户端在生命周期结束时关闭。

## 面试演示顺序

1. 展示 Requirement Graph 节点和两处分支路由。
2. 输入完整杭州需求，按同一 `thread_id` 串起模型、地点工具和规划日志。
3. 删除离开信息，证明 Graph 在工具调用前停止并返回明确问题。
4. 模拟地点 Provider 超时，解释为什么返回 503 而不是 `infeasible`。
5. 运行 30 条 Benchmark，说明 Mock 100% 的适用边界，再讨论线上模型应如何扩充数据和做 Prompt/模型消融。

下一迭代最自然的 Agent 能力是 Human-in-the-loop：用 LangGraph Interrupt 保存缺失字段上下文，接收用户补充答案后 Resume，并只局部重跑校验与相关锚点解析，而不是重新生成全部计划。
