# v0.7 Grounded LLM Soft Critic

v0.7 在 v0.6 硬合法候选之后加入受控的 LLM 软质量评价。LLM 负责节奏、兴趣覆盖、多样性、休息友好度和区域连贯性等语义判断；预算、时间窗、步行上限、营业事实、路线和 `must_visit` 仍由确定性代码裁决。

## 已实现 Graph

```text
validate_candidates
  ├─ 无硬合法候选 → 现有 Hard Critic / Local Repair
  └─ 有硬合法候选
       → prepare_critic_context
       → soft_constraint_critic
       → validate_critic_evidence
            ├─ 引用无效且有预算 → soft_constraint_critic
            └─ success / unavailable / disabled
                 → quality_gate
                 → compile_soft_repair_plan（可选，最多一次）
                 → Route Delta → Hard Validate → 再评价 → 接受或恢复 baseline
                 → select_by_quality
                 → explain_selection
```

关键实现：

- `CandidateEvidenceDigest` 只包含白名单标准化事实，最多三个候选、每候选 48 条、总计 24000 字符。
- Evidence ID 由候选、类型、日期、实体和字段生成稳定 Hash；Provider 原始响应和 Prompt 不进入 State。
- `SoftCritique` 强制结构化五维评价，模型不返回最终总分。
- Grounding Gate 检查候选、维度、Evidence、POI、日期和 `must_visit` 删除保护。
- Quality Gate 由固定权重计算分数，硬验证等级始终优先。
- LLM 只提出动作 Intent；本地 Compiler 只允许移动、重排或移除非必去 POI。
- 软修复最多一个动作、一次迭代；必须重新经过 Route Delta、Hard Validator 和 Soft Critic，提升不足 5 分就恢复 baseline。
- Critic 超时、认证、Schema 或 Grounding 失败仍返回硬合法计划，不会伪装成 `infeasible`。
- `GroundedExplanation` 由已验证 Critique 和 Evidence 模板化生成，不发生第二次模型调用。

## Provider 配置

默认离线 Mock：

```powershell
$env:CRITIC_PROVIDER = "mock"
$env:CRITIC_MODEL = "mock-soft-critic-v1"
```

DeepSeek：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm-deepseek]"
$env:CRITIC_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "replace-with-your-own-key"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:CRITIC_MODEL = "replace-with-an-explicit-supported-model"
```

OpenAI：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm-openai]"
$env:CRITIC_PROVIDER = "openai"
$env:OPENAI_API_KEY = "replace-with-your-own-key"
$env:CRITIC_MODEL = "replace-with-an-explicit-supported-model"
```

完全关闭：

```powershell
$env:CRITIC_PROVIDER = "disabled"
```

需求解析的 `REQUIREMENT_PROVIDER` 与软评审的 `CRITIC_PROVIDER` 相互独立。例如，可以使用 Mock 解析需求、DeepSeek 评价候选，也可以反向组合。真实 Provider 失败不会回退 Mock。

## API 新字段

`PlanningResponse` 增加：

- `critic_status`：`success`、`unavailable`、`invalid_grounding`、`disabled`，或硬约束阶段即终止时的 `not_run`；
- `critic_summary`：Provider、模型、Prompt 版本、调用次数、延迟和 Token；
- `candidate_critiques`：仅在 Grounding 成功时返回；
- `grounded_explanation`：带 Evidence ID 的选择说明；
- `soft_iterations`：软修复轮次，v0.7 最大为 1。

## 评测与验证

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_soft_critic.py
.\.venv\Scripts\python.exe -m pytest tests\test_critic_grounding.py tests\test_critic_providers.py tests\test_soft_critic_trajectory.py tests\test_soft_critic_benchmark.py
```

`evals/soft_critic/cases.jsonl` 当前包含 15 条人工构造的离线回归 Fixture，用于验证指标、动作安全、Grounding 和消融脚本；它不是线上 DeepSeek/OpenAI 的质量声明。要报告真实模型效果，应固定候选与模型版本后重新标注并运行同一评测接口。

当前 Fixture 回归快照：Referential Grounding 100%，人工 Semantic Grounding 93.33%，Issue F1 97.14%，选择一致率从 hard-only 的 20% 提升到 critic-rank 的 93.33%，动作安全率 100%，Hard Constraint Regression 0%。这些数值只证明评测管线和门禁可重复，不代表真实 Provider 泛化效果。

## 关键 Trace

```text
critic.context_prepared
critic.started / retry_scheduled / completed / failed
critic.grounding_validated / grounding_rejected
quality_gate.decision
soft_repair.plan_created / action_applied / routes_reused
soft_repair.validation_delta / accepted / rejected
explanation.generated
```

日志不记录用户原文、完整 Prompt、Provider 原始响应或 API Key。
