# Travel Agent 项目记忆与开发原则

## 项目定位

这是一个准备写入个人简历的旅行规划 Agent 项目。项目的核心价值必须落在 Agent Engineering，而不是把主要精力变成地图 API、OTA 数据、通用后端或前端工程展示。

任何版本规划、技术选型和范围取舍都应优先回答：这项工作展示了什么 Agent 能力，它如何进入 Agent 的状态、决策、循环、工具轨迹或评测体系？

## 文档语言

- 项目内新建或更新的 README、`docs/` 文档、设计规格、实施计划、学习指南和面试材料默认使用中文。
- 面向项目维护者的 Agent 工作报告与审查摘要也默认使用中文；工具要求的固定状态标识可以保留英文。
- 代码标识符、文件名、命令、环境变量、API 字段、日志事件名和行业标准术语可以保留英文。
- 引用英文官方资料时，用中文解释其结论，不生成整篇英文说明文档。
- 只有用户明确要求英文或目标交付物必须使用英文时，才切换文档语言。

## 能力优先级

### P0：Agent 核心能力

- 显式、可解释的 LangGraph State、Node、Edge 和 Conditional Routing。
- `Plan → Tool Use → Validate/Critic → Replan` 有界循环。
- Tool Calling 的输入输出 Schema、失败语义、调用轨迹和结果回写 State。
- 计划生成、验证、修复和终止条件之间的清晰职责边界。
- 短期运行上下文、Checkpoint、后续的长期 Memory 与上下文裁剪。
- 后续的 Human-in-the-loop、Interrupt/Resume 和局部重规划。
- 能证明 Agent 行为的日志、Trace、轨迹测试、Benchmark 和消融实验。
- LLM 负责语义理解和软决策，确定性代码负责硬约束、计算和安全边界。

### P1：支撑 Agent 的工程能力

- Provider Protocol、Tool Gateway、缓存、超时、重试和错误分类。
- FastAPI、领域模型、配置、依赖注入和持久化。
- 这些能力做到足以让 Tool Use 真实、可靠、可测试、可观察即可。

### P2：非当前重点

- 完整 OTA 交易、机票酒店库存和下单。
- 穷尽所有地图字段、城市、交通方式和供应商。
- 复杂前端、运营后台和大规模分布式基础设施。
- 与 Agent 演示、可靠性或评测无直接关系的过度工程化。

## 版本设计规则

每个版本至少明确：

1. 新增或强化了哪一项 Agent 能力。
2. 该能力在 Graph 中对应哪些 State、Node、Edge 或 Loop。
3. Agent 如何调用工具、读取结果并据此改变后续决策。
4. 如何从日志或 Trace 观察完整轨迹。
5. 如何用测试或 Benchmark 证明行为正确。
6. 面试演示时如何解释技术取舍和失败恢复。

如果一项大型工作不能明显强化以上内容，应缩小范围、延后，或只实现支撑 Agent 所需的最小部分。

## 架构守则

- 不为了“多 Agent”标签机械拆分模块；只有需要独立推理、上下文或决策闭环的职责才成为 Agent/Subgraph。
- 不把 Agent Loop 隐藏在一个巨大函数中；计划、工具执行、验证、路由和重规划应在 Graph 中可见。
- 不让 LLM 判断可由代码准确计算的硬约束。
- 不让 Provider 原始响应直接污染 Graph State 或 Prompt 上下文。
- State 保持强类型和最小化，大型原始数据保存到 State 外，只保留标准化结果、ID 或摘要。
- Tool 失败与业务不可行必须分开；外部工具失败不能伪装成 `infeasible`。
- 所有 Loop 都有迭代、调用次数或时间预算，避免无限执行。
- 关键 Agent 行为优先使用轨迹测试，而不只测试最终文本。

## v0.2 的 Agent 重点

v0.2 虽然建设 Provider 和 Tool Gateway，但目的不是展示 HTTP 集成，而是完成可靠 Tool Use：

```text
Agent 形成检索意图
→ Graph 进入工具节点
→ Tool Gateway 调用选定 Provider
→ 标准化结果写回 State
→ Agent 根据结果形成候选计划
→ Validator 发现问题
→ Conditional Edge 决定 Select / Replan / Stop
```

验收和文档必须展示工具调用前后的 State 变化、工具事件、重试轨迹、条件路由和 Replan，而不能只展示“成功调用了高德 API”。

## 简历与演示导向

项目材料优先呈现：

- Agent 架构和循环；
- Tool Use 与可靠工具层；
- 上下文和状态管理；
- 约束验证与自修复；
- 可观测执行轨迹；
- 可量化评测结果。

地图 API、缓存和后端组件应作为上述 Agent 能力的工程支撑来描述，而不是项目主角。
