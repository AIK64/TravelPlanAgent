# Travel Agent v0.1 学习与实现文档

这套文档只描述仓库中已经完成的 **v0.1 版本**。它面向第一次接触 FastAPI、Pydantic、LangGraph 和 Agent 工作流的开发者，目标是让你不仅能运行项目，还能解释每一层为什么存在。

## v0.1 的准确定位

v0.1 是一个：

> 使用确定性代码实现的旅行规划 Agent 工作流骨架。

它已经拥有 Agent 系统最基础的控制结构：

```text
输入结构化需求
  ↓
加载旅行上下文
  ↓
生成多个候选计划
  ↓
验证计划
  ↓
不合法则重新规划
  ↓
选择最佳合法计划或返回无解
```

但 v0.1 **还没有调用大语言模型**，所以目前不能直接理解一段中文自然语言，也不能称为完整的智能旅行助手。现在先用确定性实现打通控制流，是为了让后续接入 LLM 时，LLM 只替换适合它的节点，而不会破坏约束检查、循环终止和测试体系。

## 当前基线

- 项目版本：`0.1.0`
- Python：3.11+
- 核心框架：FastAPI、Pydantic、LangGraph
- 数据来源：杭州 Mock POI
- Checkpoint：`InMemorySaver`
- 测试数量：6
- 当前测试结果：6 passed
- 当前代码覆盖率：93%

## 推荐阅读顺序

1. [v0.1 实现总览](01-implementation-overview.md)
2. [一次请求的完整生命周期](02-request-lifecycle.md)
3. [代码结构逐层导读](03-code-walkthrough.md)
4. [LangGraph 在项目中如何工作](04-langgraph-workflow.md)
5. [约束验证、评分与 Replan](05-validation-and-replanning.md)
6. [运行、测试与新手练习](06-running-testing-and-exercises.md)
7. [从 v0.1 到 v0.2 的路线](07-roadmap-to-v0.2.md)

## 文档与代码的对应关系

| 想学习的内容 | 主要代码 | 对应文档 |
|---|---|---|
| 数据怎样定义 | `domain/models.py` | 03 |
| 候选行程怎样生成 | `planning/planner.py` | 02、03 |
| 距离和交通时间怎样估算 | `planning/routing.py` | 03 |
| 约束怎样检查 | `planning/validator.py` | 05 |
| Loop 怎样形成 | `graph/workflow.py` | 04 |
| API 怎样接收请求 | `api/routes.py`、`app.py` | 02、03 |
| 怎样保证功能没有被改坏 | `tests/` | 06 |

## 关键术语

### Domain Model

对业务数据的明确建模。例如 `TripSpec` 表示旅行需求，`PlanCandidate` 表示一个候选行程。它们不是随意的字典，而是具有类型和校验规则的数据对象。

### State

一次 LangGraph 运行过程中共享的数据。每个节点读取 State，并返回自己要更新的字段。

### Node

工作流中的一个执行步骤，例如加载 POI、生成候选或验证候选。

### Edge

节点之间的连接，决定执行顺序。

### Conditional Edge

根据当前 State 选择不同下一节点的条件边。v0.1 用它决定选择合法方案、进入 Replan，还是返回无解。

### Candidate

一份候选旅行计划。v0.1 生成 relaxed、balanced 和 exploration 三种风格。

### Validator

使用确定性代码检查行程是否合法的模块。它不依赖 LLM，因此同样输入会得到同样结果。

### Replan

当前候选全部不合法时重新生成计划。v0.1 使用“降低活动密度、提高低成本地点优先级”的简化重规划。真正的局部重规划将在后续版本实现。

### Checkpoint

LangGraph 对执行状态的保存。v0.1 使用内存 Checkpoint，只能用于开发和测试，进程退出后数据会消失。

## 学习建议

不要从头背代码。每学习一个模块，都按下面四个问题理解：

1. 这个模块接收什么输入？
2. 它输出什么结果？
3. 如果没有它，系统会出现什么问题？
4. 它应该由 LLM、算法还是普通代码实现？

能够回答这四个问题，比记住某个框架 API 更重要。

