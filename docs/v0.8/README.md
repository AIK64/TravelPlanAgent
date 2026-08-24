# v0.8 计划生命周期 HITL

v0.8 把一次性规划结果扩展为可暂停、可恢复、可审批的计划生命周期。用户选择候选形成 `Plan V1`，随后可以锁定日期或项目、提交结构化/自然语言编辑；Agent 只修改受影响日期，复用已有路线并补查 Route Delta，重新经过 Hard Validator 和 Grounded Soft Critic，最后由用户批准生成 `Plan V2`。

```text
Candidate Selection Interrupt
  → Persist V1
  → Lock / Edit Interrupt
  → EditModel → Grounding → Impact Analysis → Lock Guard
  → Local Draft → POI/Route Delta Tool Use
  → Hard Validator → Soft Critic → Locality Guard → Diff
  → Approval Interrupt
      ├─ approve → CAS Commit V2
      └─ reject  → Keep V1
```

完整设计与取舍见 [设计报告](design.md)。

## 1. 已实现能力

- 独立 `PlanLifecycleWorkflow`，选择、锁、编辑解析、影响分析、Preview 和审批均为 Graph 可见节点。
- 稳定 `item_id`、不可变 `PlanVersion`、`PlanPreview`、日期/项目锁和结构化 `PlanDiff`。
- `EditModel` Protocol 及 Mock、DeepSeek、OpenAI Provider；编辑模型与需求解析、Soft Critic 可以独立配置。
- LLM 只产生最多三个白名单 Edit Operation；实体 Grounding、Impact、锁、硬约束和提交由确定性代码负责。
- `move/reorder/remove/add/replace`；新增和替换缺少本地 POI 时经 Tool Gateway 检索并标准化。
- 最多影响两个日期；更大修改返回 `requires_new_plan`，不会静默全量重生成。
- Route Delta、未受影响日期 Hash、日期锁和项目锁双重守卫。
- Preview/Commit 两阶段；Approval Token、Active Version 和 Session Revision 共同阻止旧审批。
- request ID 幂等、单 Session Resume 串行化、Repository CAS 与旧 Interrupt 409。
- 内存和 SQLite Plan Repository；SQLite Checkpoint + Repository 支持单机重启恢复。
- 结构化和自然语言生命周期 API；原有一次性规划 API 保持兼容。

## 2. 运行配置

默认全 Mock，不需要 API Key：

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

生命周期配置：

```text
EDIT_PROVIDER=mock
EDIT_MODEL=mock-plan-edit-v1
EDIT_TIMEOUT_SECONDS=20
EDIT_MAX_ATTEMPTS=2
EDIT_MAX_OUTPUT_TOKENS=1200
PLAN_MAX_VERSIONS=20
PLAN_MAX_AFFECTED_DAYS=2
```

使用 DeepSeek 编辑解析：

```text
EDIT_PROVIDER=deepseek
EDIT_MODEL=<当前可用的明确模型名>
DEEPSEEK_API_KEY=<仅保存在本机环境变量>
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm-deepseek]"
```

DeepSeek 使用 OpenAI-compatible Client，但有独立 Edit Prompt、Schema、Gateway、超时和重试。`EDIT_PROVIDER=deepseek` 不会在失败后回退到 Mock。真实模型名必须根据当前供应商控制台显式设置，项目不硬编码可能过期的别名。

SQLite 单机恢复：

```text
CHECKPOINT_BACKEND=sqlite
CHECKPOINT_SQLITE_PATH=.data/travel-agent-checkpoints.sqlite3
PLAN_SQLITE_PATH=.data/travel-agent-plans.sqlite3
```

这些 SQLite 文件包含短期需求和计划内容，位于 Git 忽略目录，不应提交或共享。它们不包含多实例锁、租户权限、加密和生产备份。

## 3. API 使用

### 3.1 创建会话

结构化入口：

```text
POST /api/v1/plan-sessions
```

请求体复用 `PlanningRequest`。自然语言入口：

```text
POST /api/v1/plan-sessions/from-text
```

```json
{
  "text": "2026年10月2日到4日去杭州……",
  "reference_date": "2026-08-24"
}
```

完整需求会在候选选择处暂停：

```json
{
  "session_id": "...",
  "status": "awaiting_candidate_selection",
  "session_revision": 0,
  "allowed_actions": ["accept_recommendation", "select_candidate"],
  "interrupt": {
    "id": "...",
    "payload": {
      "kind": "candidate_selection",
      "recommended_candidate_id": "exploration-opt-r0"
    }
  }
}
```

需求不完整时先返回 `needs_requirement_clarification`，使用同一个生命周期 Resume 接口提交 `clarify_requirement`；完成 TripSpec 后自动转到候选选择。

### 3.2 选择 V1

```text
POST /api/v1/plan-sessions/{session_id}/resume
```

```json
{
  "interrupt_id": "...",
  "request_id": "e90bc26b-2ab0-4fe6-b733-df8f04081a14",
  "expected_session_revision": 0,
  "action": {"kind": "accept_recommendation"}
}
```

也可以提交 `{"kind":"select_candidate","candidate_id":"..."}`。只有硬合法候选可以形成 V1。

### 3.3 锁定

锁定日期：

```json
{
  "interrupt_id": "...",
  "request_id": "uuid",
  "expected_active_version_id": "V1",
  "expected_session_revision": 1,
  "action": {
    "kind": "lock",
    "lock_kind": "day",
    "target_id": "2026-10-03"
  }
}
```

项目锁把 `lock_kind` 改为 `item`，`target_id` 使用响应中的稳定 `item_id`。解锁使用 `kind=unlock`。LLM 无权生成 unlock。

### 3.4 编辑与澄清

自然语言编辑：

```json
{
  "interrupt_id": "...",
  "request_id": "uuid",
  "expected_active_version_id": "V1",
  "expected_session_revision": 2,
  "action": {
    "kind": "edit_text",
    "text": "把第一天下午的博物馆挪到第三天上午"
  }
}
```

自动化客户端可以直接提交 `kind=edit` 和 `EditPatch`，但仍会经过相同 Grounding、Impact、Tool、Validator、Critic 和锁守卫。

如果项目引用不能唯一确定，Graph 返回 `edit_clarification` Interrupt，客户端使用响应列出的 `item_id`：

```json
{
  "interrupt_id": "...",
  "request_id": "uuid",
  "expected_active_version_id": "V1",
  "expected_session_revision": 3,
  "action": {"kind": "clarify_edit", "item_id": "item_..."}
}
```

### 3.5 审批并生成 V2

硬合法 Preview 返回 `awaiting_change_approval`，Interrupt Payload 包含一次性 `approval_token`：

```json
{
  "interrupt_id": "...",
  "request_id": "uuid",
  "expected_active_version_id": "V1",
  "expected_session_revision": 3,
  "action": {
    "kind": "approve_preview",
    "preview_id": "P1",
    "approval_token": "..."
  }
}
```

拒绝使用 `reject_preview`，不需要 Token。拒绝、Validator 失败、Tool 失败和 Critic 降级都不会覆盖 V1。

查询接口：

```text
GET /api/v1/plan-sessions/{session_id}
GET /api/v1/plan-sessions/{session_id}/versions
GET /api/v1/plan-sessions/{session_id}/versions/{version_id}
GET /api/v1/plan-sessions/{session_id}/diff?from_id=V1&to_id=V2
```

## 4. 失败语义

| 场景 | HTTP / 状态 | Active Version |
|---|---|---|
| 等待选择、澄清、编辑或审批 | 200 + `interrupt` | 不变 |
| 修改超过两个日期 | 200 + `requires_new_plan` | 不变 |
| Preview 硬非法 | 200 + `change_rejected` | 不变 |
| 锁冲突 | 409 `lock_conflict` | 不变 |
| 旧 Interrupt/Version/Revision/Token | 409 | 不变 |
| Session 不存在 | 404 | 无 |
| 非法动作或实体 | 422 | 不变 |
| Edit Model、地图、Checkpoint 暂不可用 | 503 | 不变 |
| Soft Critic 不可用 | 硬合法 Preview 降级审批 | 不变直到批准 |

## 5. Trace

代表事件：

```text
lifecycle.interrupted/resumed
selection.committed
lock.changed
edit.parse.started/completed
impact.analyzed
lock_guard.rejected
preview.hard_validated
locality_guard.completed/failed
preview.rejected
version.committed
lifecycle.action.replayed
```

事件使用 `session_id`、`lifecycle_thread_id`、request/version/preview ID 关联；不记录用户原始编辑文本、完整 Prompt、Provider 原始响应、API Key 或 Approval Token。

## 6. 测试与 Benchmark

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing --cov-fail-under=90
.\.venv\Scripts\python.exe scripts\evaluate_plan_lifecycle.py
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
.\.venv\Scripts\python.exe -m pip check
```

当前 15 条离线生命周期 Fixture 的结构化基线为：Intent、Grounding、Impact、锁定保持、未影响日期保持、Diff、Commit、幂等和有界终止均为 100%，硬约束回归为 0%，标注 Route Reuse Rate 为 36.84%。这些数字证明固定 Fixture 和确定性边界没有回归，不代表真实 DeepSeek 的线上语义准确率；真实 Provider 必须按模型、Prompt version、数据集版本和日期单独评测。

2026-08-24 本机全 Mock 回归共收集 372 项测试，其中 370 项通过、2 项 Live Smoke 跳过；`travel_agent` 总覆盖率为 90.03%，通过 90% 覆盖率门禁。依赖一致性检查与 `src/tests/scripts` 编译检查均通过。

关键测试还实际执行：候选选择、日期锁、跨日局部编辑、Route Delta、Preview、批准 V2、拒绝保持 V1、锁冲突、旧 Interrupt、request ID 幂等、需求/编辑澄清和 SQLite 重启恢复。

## 7. 当前边界

- `add/replace` 会先复用当前标准化 POI；缺失时调用 POI Search，但不包含 OTA 库存或交易。
- 编辑澄清当前只补充唯一 `item_id`；修改日期范围、预算、交通锚点、住宿、行动能力或 `must_visit` 仍要求新建计划。
- 单次 Patch 最多三个操作、两个 affected days；版本历史为最多 20 个的线性链。
- Preview 阶段不运行用户未授权的多轮硬修复，也不会为通过验证静默删除其他活动。
- v0.9 才加入天气 `ChangeEvent`；Memory、MCP、完整前端和生产存储仍属于后续版本。
