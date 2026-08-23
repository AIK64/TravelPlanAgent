# v0.4 Human-in-the-loop 澄清恢复

v0.4 把 v0.3 的一次性 `needs_clarification` 分支改造成可暂停、可恢复、可评测的 Agent 循环。用户补充回答不会重新生成完整需求，而是经过独立 Provider Prompt 生成 `RequirementPatch`，再由确定性代码按本轮目标字段合并。

```text
parse_requirement → validate_requirement
  ├─ valid → resolve_anchors → evaluate_anchors → planning
  └─ blocking issue → request_clarification
                         ↓
                   await_clarification
                         ↓ interrupt
                    Graph 暂停
                         ↓ Command(resume)
                parse_clarification_patch
                         ↓
                apply_clarification_patch
                  ├─ no change → 再次澄清
                  └─ changed → validate_requirement
```

## State 与职责边界

- `clarification_round` 与 `max_clarification_rounds` 构成业务终止预算，默认三轮，最大五轮。
- `clarification_target_fields` 是本轮唯一可写白名单。
- `clarification_input` 只在 Resume 后到 Patch 合并前存在于当前 State；日志不输出正文。
- `changed_fields` 驱动重新验证和锚点缓存失效。
- `RequirementExecutionSummary.operation` 区分 `initial_parse` 与 `clarification_patch`，便于统计模型调用轨迹。
- 工具、LLM 或 Checkpoint 故障保持外部失败语义，不会伪装成 `needs_clarification` 或规划 `infeasible`。

`await_clarification` 在 `interrupt()` 前不执行外部调用。LangGraph 恢复时会从节点开头重新执行，因此模型调用、工具调用和 State 修改都位于 Interrupt 返回之后的独立节点。

## Patch 与局部重跑

Provider 可以解析所有 `RequirementPatch` 字段，但 `merge_requirement_patch` 只接受当前 Issue 展开的字段。例如 `date_range` 展开为 `start_date/end_date`，`departure.name` 不允许修改目的地或预算。

字段变化与地图结果失效关系：

| 字段 | 失效角色 |
|---|---|
| `arrival.name` | arrival |
| `departure.name` | departure |
| `accommodation_name` | accommodation |
| `arrival.at` / `departure.at` | 无 |
| `destination` | arrival、departure、accommodation |

`resolve_anchors` 只为缺失 Resolution 的角色生成 `AnchorSearchIntent`。锚点歧义场景中，已经唯一解析的角色保留在 State，Resume 后不会再次调用对应地图工具。

## API

创建入口保持不变：

```text
POST /api/v1/plans/from-text
```

中断响应新增：

```json
{
  "thread_id": "thread-id",
  "status": "needs_clarification",
  "clarification_round": 1,
  "can_resume": true,
  "interrupt": {
    "id": "interrupt-id",
    "payload": {
      "kind": "requirement_clarification",
      "round": 1,
      "max_rounds": 3,
      "target_fields": ["departure.name", "departure.at"],
      "issues": [
        {
          "code": "missing",
          "field": "departure.name",
          "message": "缺少必要字段 departure.name",
          "question": "你会从哪个地点离开？",
          "blocking": true
        }
      ],
      "questions": ["你会从哪个地点离开？"]
    }
  }
}
```

恢复入口：

```text
POST /api/v1/plans/from-text/{thread_id}/resume
```

```json
{
  "interrupt_id": "interrupt-id",
  "request_id": "e90bc26b-2ab0-4fe6-b733-df8f04081a14",
  "answer": "10月4日19:00从杭州东站离开。"
}
```

- 不存在的线程返回 404。
- 旧 Interrupt、已完成线程或并发重复恢复返回 409。
- 空输入返回 422。
- Provider/Tool 不可用返回 503。
- 达到澄清上限后仍返回 `needs_clarification`，但 `can_resume=false`。

## Checkpoint

测试和默认开发模式使用 `InMemorySaver`。设置 `CHECKPOINT_BACKEND=sqlite` 后使用 `AsyncSqliteSaver`，可以在单机服务重启后沿相同 `thread_id` Resume。

SQLite 模式是本地演示能力：不包含多实例锁、租户隔离、访问控制、TTL 或生产备份。Checkpoint 会保存原始需求和短期补充回答，因此数据库位于 Git 忽略的 `.data/`，不能提交或共享。生产化应换成 PostgreSQL Checkpointer，并增加加密、保留期和线程所有权校验。

## 轨迹与评测

关键事件：

```text
clarification.prepared
clarification.interrupted
clarification.resumed
clarification.patch.started/completed
clarification.patch.applied
clarification.routing_decision
anchor.resolution.plan
clarification.exhausted
```

运行初始解析与澄清 Patch Benchmark：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_requirement_parser.py
.\.venv\Scripts\python.exe scripts\evaluate_clarification_parser.py
```

澄清报告包含目标字段准确率、整例准确率、Provider 失败数、字段保持率与平均耗时。Mock 100% 只证明固定 Fixture 与确定性合并没有回归；OpenAI/DeepSeek 线上结果必须按 Provider、模型、Prompt version、数据集版本和日期分别记录。

## 版本边界

本版本不实现完成计划后的审批、局部编辑、计划锁定、版本 Diff、长期 Memory、天气或多 Agent。这些能力不能混入需求澄清循环的验收指标。
