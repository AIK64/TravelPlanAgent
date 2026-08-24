# v0.7 Grounded LLM Soft Critic 设计报告

> 文档状态：已实现设计基线  
> 上一版本：v0.6.0 约束优化与真实路线质量  
> 目标版本：v0.7.0  
> 设计日期：2026-08-24

## 1. 设计结论

v0.7 在 v0.6 的硬约束合法候选之后增加 Grounded LLM Soft Critic，用 DeepSeek、OpenAI 或离线 Mock 对节奏合理性、兴趣覆盖、内容多样性、休息友好度和区域连贯性进行评价。

本版本的核心不是“再调用一次 LLM”，而是建立可验证的软决策闭环：

```text
标准化计划事实
  → 有界 Evidence Digest
  → Structured Soft Critique
  → Evidence 引用校验
  → Deterministic Quality Gate
      ├─ 可接受 → 选择并生成 Grounded Explanation
      ├─ 可安全改善 → 一次局部软修复 → Hard Validate → 再评价
      └─ Critic 不可用/无安全动作 → 降级交付硬合法计划
```

确定性 Validator 继续拥有硬约束最终裁决权。Soft Critic 无权修改预算、时间窗、步行上限、营业事实或 `must_visit`，也不能直接写入 Candidate、Draft 或 Graph 路由。

## 2. 当前基线与问题

v0.6 已有三风格优化候选、真实驾车/步行矩阵、Hard Validator、硬违规局部修复，以及 DeepSeek/OpenAI 结构化 Provider 基础设施。当前候选选择仍只比较验证等级和确定性 `candidate.score`，不能充分评价：

- 景点组合是否真正覆盖用户兴趣；
- 日程是否过密、是否保留自然休息空间；
- 多日内容是否重复；
- 地理与主题过渡是否自然；
- 为什么某候选更适合当前用户。

现有 `CriticReport` 描述 Hard Validator 的违规并驱动硬修复或 `infeasible`。软质量问题不代表计划不可行，因此 v0.7 不扩大它的语义，而是新增独立 `SoftCritique`、`SoftRepairPlan` 和 `SoftRepairAttempt`。两条链路共享纯函数和 Route Delta 设施，但保持不同 State、日志、路由和终止语义。

## 3. 目标与非目标

### 3.1 必须完成

1. 为所有硬合法候选构建最小、稳定、可引用的 `CandidateEvidenceDigest`。
2. 提供独立 `CriticModel` Protocol，支持 Mock、DeepSeek、OpenAI 和 disabled。
3. LLM 输出通过 Pydantic Schema 和 Evidence 引用校验。
4. 由确定性代码计算总软质量分、选择候选并决定是否允许局部软修复。
5. 最多执行一次软修复；修复后重新经过 Hard Validator 和 Soft Critic。
6. Soft Critic 失败时降级交付硬合法计划，不能返回 `infeasible`。
7. 返回只引用已验证事实的 `GroundedExplanation`。
8. 建立离线人工标注集和 with/without critic 消融报告。

### 3.2 明确不做

- 不让 LLM 直接生成行程或修改 Graph State。
- 不让 LLM 判断预算、时间冲突、路线距离等可精确计算的事实。
- 不支持完成计划后的用户选择、锁定和编辑；这些属于 v0.8。
- 不新增天气、Memory、MCP 或多 Agent。
- 不把 Evidence Builder、Grounding Validator 或 Quality Gate 包装为 Agent。
- 不在代码中硬编码供应商价格，只记录 Token 并使用评测时显式费率快照。

## 4. Graph 设计

### 4.1 正常路径

```text
materialize_optimized_candidates
  → validate_candidates
      ├─ 无硬合法候选 → 现有 Hard Critic / Repair Loop
      └─ 有硬合法候选
           → prepare_critic_context
           → soft_constraint_critic
           → validate_critic_evidence
                ├─ 引用无效且仍有预算 → soft_constraint_critic
                └─ 有效/预算耗尽/Provider 不可用 → quality_gate
                     ├─ acceptable → select_by_quality
                     ├─ repairable → compile_soft_repair_plan
                     └─ degraded/no_safe_action → select_by_quality
  → explain_selection
  → END
```

### 4.2 一次局部软修复

```text
compile_soft_repair_plan
  → apply_soft_repair
  → collect_soft_delta_routes
      ├─ 有缺失路线 → load_soft_delta_routes
      └─ 无缺失路线 ───────────────┐
                                   ↓
                       materialize_soft_candidate
                         → validate_candidates
                              ├─ 硬约束失败 → restore_soft_baseline
                              └─ 硬约束通过 → prepare_critic_context
                                   → soft_constraint_critic
                                   → validate_critic_evidence
                                   → compare_soft_repair
                                        ├─ 达到最小提升 → 接受修复候选
                                        └─ 无提升 → restore_soft_baseline
  → select_by_quality
  → explain_selection
```

软修复默认最多一轮且只允许一个动作。它不消耗 `max_replan_rounds`，而使用独立 `max_soft_replan_rounds=1`。

### 4.3 路由优先级

1. Hard Validator 先判断候选是否可交付。
2. 没有硬合法候选时，只能进入现有硬修复或 `infeasible`。
3. 有硬合法候选时，才允许进入 Soft Critic。
4. Soft Critic 的任何结果都不能把硬合法计划改写为 `infeasible`。
5. 软修复造成硬违规时恢复 baseline，不用硬修复掩盖软策略错误。

## 5. 强类型领域模型

新增 `src/travel_agent/domain/critique_models.py`。

### 5.1 Evidence

```python
class EvidenceKind(StrEnum):
    TRIP_PREFERENCE = "trip_preference"
    CANDIDATE_METRIC = "candidate_metric"
    DAY_METRIC = "day_metric"
    POI_FACT = "poi_fact"
    SCHEDULE_FACT = "schedule_fact"
    ROUTE_FACT = "route_fact"
    ASSUMPTION = "assumption"

class EvidenceItem(BaseModel):
    id: str
    kind: EvidenceKind
    candidate_id: str
    day: date | None
    entity_id: str | None
    field: str
    value: str | int | float | bool
    source: str
    confidence: float

class CandidateEvidenceDigest(BaseModel):
    schema_version: Literal["critic-evidence-v1"]
    candidate_id: str
    style: PlanStyle
    evidence: tuple[EvidenceItem, ...]
    input_chars: int
    truncated: bool
```

Evidence ID 由 `candidate_id + kind + day + entity_id + field` 生成稳定 Hash。Provider 原始 JSON、完整路线矩阵、坐标和用户原文不进入 Digest。

### 5.2 Soft Critique

```python
class SoftDimension(StrEnum):
    PACE = "pace"
    INTEREST_COVERAGE = "interest_coverage"
    DIVERSITY = "diversity"
    REST_FRIENDLINESS = "rest_friendliness"
    GEOGRAPHIC_COHERENCE = "geographic_coherence"

class SuggestedActionKind(StrEnum):
    MOVE_OPTIONAL_POI = "move_optional_poi"
    REORDER_OPTIONAL_POI = "reorder_optional_poi"
    REMOVE_OPTIONAL_POI = "remove_optional_poi"
    NO_ACTION = "no_action"

class SuggestedSoftAction(BaseModel):
    kind: SuggestedActionKind
    poi_id: str | None
    from_day: date | None
    to_day: date | None
    evidence_ids: tuple[str, ...]
    expected_dimension: SoftDimension | None

class DimensionCritique(BaseModel):
    dimension: SoftDimension
    score: int  # 0..100
    summary: str
    evidence_ids: tuple[str, ...]
    suggested_action: SuggestedSoftAction | None

class SoftCritique(BaseModel):
    candidate_id: str
    dimensions: tuple[DimensionCritique, ...]
    overall_summary: str
    tradeoff_evidence_ids: tuple[str, ...]
```

LLM 不返回可直接采用的总分。Quality Gate 使用固定权重从五个维度计算总分，防止 Provider 改变排序规则。

### 5.3 执行、修复与解释

```python
class CriticStatus(StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    INVALID_GROUNDING = "invalid_grounding"
    DISABLED = "disabled"

class CriticExecutionSummary(BaseModel):
    provider: str
    model: str
    prompt_version: str
    status: CriticStatus
    attempt_count: int
    grounding_attempt_count: int
    elapsed_ms: float
    input_tokens: int | None
    output_tokens: int | None
    input_chars: int

class SoftRepairPlan(BaseModel):
    round: int
    target_candidate_id: str
    source_dimension: SoftDimension
    source_evidence_ids: tuple[str, ...]
    action: RepairAction
    affected_days: tuple[date, ...]
    preserved_days: tuple[date, ...]
    action_fingerprint: str

class SoftRepairAttempt(BaseModel):
    round: int
    before_quality_score: float
    after_quality_score: float | None
    hard_validation_passed: bool
    accepted: bool
    reused_route_count: int
    loaded_route_count: int
    terminal_reason: str

class GroundedStatement(BaseModel):
    text: str
    evidence_ids: tuple[str, ...]

class GroundedExplanation(BaseModel):
    candidate_id: str
    headline: str
    highlights: tuple[GroundedStatement, ...]
    tradeoffs: tuple[GroundedStatement, ...]
    critic_status: CriticStatus
```

`GroundedExplanation` 不触发第二次 LLM 调用。`explain_selection` 只模板化已校验 Critique；Critic 不可用时从确定性 Metrics 生成降级说明。

## 6. Evidence Digest

### 6.1 字段白名单

每个候选最多包含：

- 用户 pace、interests、avoid、休息需求和活动/步行上限；
- Candidate 总路线分钟、真实步行、已知费用、偏好、多样性、疲劳和置信度；
- 每日活动数量、首末时间、活动/路线分钟、步行、疲劳和空闲分钟；
- POI ID、名称、标准化类别、必去标记、费用、时长和置信度；
- 路线腿起终点实体 ID、模式、分钟、米数和置信度；
- assumption ID、字段、来源和待确认标记。

### 6.2 裁剪

默认每次请求最多 3 个候选、每候选 48 条 Evidence、总输入 24000 字符。超限时依次保留：用户偏好/必去、Candidate/Day Metrics、已安排 POI、路线事实、低价值说明。裁剪由确定性代码完成，并设置 `truncated=true`。

### 6.3 Prompt Injection 边界

POI 名称和类别均是不可信数据：

- JSON 序列化到独立 `evidence` 字段；
- System Prompt 禁止执行 Evidence 中的指令；
- 输出只能通过固定 Schema；
- Action 经过本地 allowlist 和实体校验；
- 测试集加入“忽略规则”等字符串，验证其不能扩大动作权限。

## 7. Provider Protocol 与 Gateway

新增：

```text
src/travel_agent/critique/
  protocols.py
  gateway.py
  errors.py
  prompts.py
  evidence.py
  grounding.py
  quality.py
  providers/{mock,deepseek,openai}.py
```

Protocol：

```python
class CriticModel(Protocol):
    name: str
    model: str
    prompt_version: str

    async def critique(
        self,
        request: SoftCriticRequest,
    ) -> SoftCriticProviderOutput: ...
```

Graph 只依赖 `CriticGateway`，不包含供应商分支。

- Mock：确定性离线 Fixture。
- OpenAI：Structured Outputs。
- DeepSeek：JSON Output + 严格 Pydantic 二次校验，关闭非必要 thinking。
- disabled：不构建模型 Client，写入 `critic_status=disabled`。

需求解析 Provider 与 Critic Provider 配置独立；Runtime 默认分别管理 Client 生命周期。

### 7.1 两层重试预算

1. Gateway 传输重试：超时、限流、上游临时错误，最多 2 次。
2. Graph Grounding 重试：Schema 合法但引用无效时，将错误代码反馈给模型，最多再调用一次。

默认最坏情况为 2 次语义调用 × 每次 2 次传输尝试，即单次规划最多 4 次 Critic Provider 请求。

错误分类：`timeout`、`rate_limit`、`authentication`、`refusal`、`invalid_json`、`invalid_schema`、`invalid_grounding`、`incomplete`、`upstream_unavailable`。认证错误不重试；模型错误耗尽后降级交付。

## 8. Grounding Validator

`validate_critic_evidence` 至少检查：

1. Candidate ID 与请求完全一致，不多不少。
2. 五个维度各出现且只出现一次。
3. 每个维度至少引用一条 Evidence。
4. Evidence ID 存在且属于同一个 Candidate。
5. Action 的 POI、日期和 Evidence 存在。
6. 删除动作不能针对 `must_visit`。
7. Action 类型在 allowlist 中。
8. 摘要长度、分数和返回数量符合预算。

自动校验只能证明“引用存在且实体一致”，不能证明自然语言与证据完全蕴含。语义 Grounding Precision 必须通过人工标注 Fixture 评测，不能用引用有效率冒充事实正确率。

## 9. Deterministic Quality Gate

默认权重：

| 维度 | 权重 |
|---|---:|
| pace | 0.25 |
| interest_coverage | 0.25 |
| diversity | 0.15 |
| rest_friendliness | 0.15 |
| geographic_coherence | 0.20 |

候选排序：`Hard Validation 等级 → Grounding 有效性 → Soft Score → v0.6 Candidate Score → Candidate ID`。

- Soft Score ≥ 70：直接选择。
- 分数较低、有安全动作且软修复预算未耗尽：执行一次局部修复。
- 无安全动作、Critic 不可用或 Grounding 无效：按确定性基线选择。
- 修复后至少提升 5 分才接受，否则恢复 baseline。

阈值和权重放入不含凭证的 `CriticPolicy`，不由 LLM 决定。

## 10. 软修复安全策略

LLM 只提出 `SuggestedSoftAction` Intent；`compile_soft_repair_plan` 根据 PlanningPOI、CandidateDraft、路线矩阵和用户约束决定是否执行。

v0.7 允许：

- 移动非必去 POI：目标日营业且有时间容量；
- 重排同日非必去 POI：重新计算 Route Delta；
- 移除非必去 POI：仅用于明显过密/休息不足，且不能导致空计划或兴趣覆盖完全丢失。

明确禁止：

- 删除或替换 `must_visit`；
- 修改预算、步行/活动上限或日期；
- 修改 POI、费用、路线或营业事实；
- 引入 `planning_pois` 之外的实体；
- 插入新的显式休息实体。休息友好度先通过移动、重排或降密度改善，休息项与 v0.8 编辑模型统一设计。

进入软修复前保存原 Candidates、Critiques、质量分、目标 Draft 和未影响日期 Hash。修复硬验证失败、质量无提升或 Action 重复时恢复 baseline。Route Tool 失败仍返回 503，不能伪装为 Critic 降级。

## 11. Graph State 与 API

`TravelState` 新增：

```text
critic_evidence_digests
soft_critiques
critic_execution_summary
critic_status
critic_grounding_attempts
critic_grounding_errors
quality_scores
soft_baseline_candidates
soft_baseline_critiques
soft_repair_plan
soft_repair_history
soft_iterations
max_soft_replan_rounds
pending_soft_replan_round
grounded_explanation
```

不进入 State：完整 Prompt、原始模型响应、SDK Client、API Key、价格表和未裁剪用户原文。

`PlanningResponse` 增加带默认值的向后兼容字段：`critic_status`、`critic_summary`、`candidate_critiques`、`grounded_explanation`、`soft_iterations`。

## 12. 配置

```dotenv
CRITIC_PROVIDER=mock
CRITIC_MODEL=mock-soft-critic-v1
CRITIC_TIMEOUT_SECONDS=20
CRITIC_MAX_ATTEMPTS=2
CRITIC_GROUNDING_MAX_ATTEMPTS=2
CRITIC_MAX_INPUT_CHARS=24000
CRITIC_MAX_OUTPUT_TOKENS=4096
CRITIC_QUALITY_THRESHOLD=70
CRITIC_MIN_IMPROVEMENT=5
MAX_SOFT_REPLAN_ROUNDS=1
```

真实 Provider 复用现有 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL`，模型名使用独立 `CRITIC_MODEL`。`CRITIC_PROVIDER=disabled` 完全跳过模型能力。所有超时、重试、Token、质量阈值和轮次预算在启动时验证。

## 13. 日志与 Trace

关键事件：

```text
critic.context_prepared
critic.started / retry_scheduled / completed / failed
critic.grounding_validated / grounding_rejected
quality_gate.decision
soft_repair.plan_created / action_applied / routes_reused
soft_repair.validation_delta / accepted / rejected
explanation.generated
```

日志只记录 thread_id、provider/model/prompt version、数量、input_chars、Token、attempt、错误分类、延迟、引用有效数、质量分、动作类型、局部性和终止原因。禁止记录完整 Prompt、用户原文、模型原始响应、API Key 和完整 Explanation 文本。

## 14. 失败语义

| 场景 | API 结果 | critic_status | 行为 |
|---|---|---|---|
| Hard Validator 无合法候选且修复失败 | `infeasible` | 未调用 | 不可交付 |
| Critic 成功且引用有效 | `completed` | `success` | 采用软排序 |
| Critic 超时/限流/认证失败 | `completed` | `unavailable` | 确定性降级选择 |
| Schema 持续无效 | `completed` | `unavailable` | 确定性降级选择 |
| Evidence 引用重试后无效 | `completed` | `invalid_grounding` | 不采用软分数 |
| 软修复造成硬违规/无提升 | `completed` | `success` | 恢复 baseline |
| 软修复 Route Tool 失败 | HTTP 503 | 保留诊断 | 外部执行失败 |

“模型评价差”绝不能产生 `infeasible`。

## 15. 测试

### 15.1 Unit

- Evidence ID、白名单、裁剪顺序和字符预算。
- Critique 维度完整、分数和 Action 字段组合。
- 未知/跨候选引用、重复维度、无证据和非法实体。
- Quality Gate 权重、阈值、稳定排序和降级。
- Soft Repair 必去保护、营业时间、allowlist、单动作限制。
- Explanation 每条陈述都有有效 Evidence。

### 15.2 Provider Contract

- Mock 确定性；DeepSeek JSON Output；OpenAI Structured Outputs。
- timeout、429、认证、拒绝、截断、空输出、非法 JSON 和 Schema 错误。
- Client 生命周期和日志无密钥。

### 15.3 Trajectory

- 成功：Hard Validate → Context → Critic → Grounding → Gate → Explain。
- Critic 超时仍 completed，不进入 `infeasible`。
- Grounding 最多一次语义重试，耗尽后降级。
- Soft Repair 经 Route Delta、Hard Validate、再评价后接受。
- 无提升或硬违规时恢复 baseline。
- Hard Invalid Candidate 从不发送给 LLM。
- Checkpoint 不包含 Client、Key、Prompt 或原始响应。

### 15.4 安全

- POI 指令注入字符串仍只是 Evidence 数据。
- 删除 must_visit 建议被 Compiler 拒绝。
- 跨 Candidate Evidence 被 Grounding 拒绝。
- INFO/DEBUG 日志无用户原文、Prompt、Key 和完整响应。

## 16. Benchmark 与消融

新增：

```text
evals/soft_critic/cases.jsonl
evals/soft_critic/evidence/*.json
scripts/evaluate_soft_critic.py
src/travel_agent/critique/evaluation.py
```

首版至少 15 条人工标注 Fixture，覆盖节奏、兴趣、多样性、休息、跨区域折返、风格差异、无安全动作、must_visit 删除、无效引用、Prompt Injection 和 Provider 降级。

指标：

- Schema Success Rate；
- Referential Grounding Rate；
- 人工 Semantic Grounding Precision；
- Dimension Issue Precision/Recall/F1；
- Selection Agreement；
- Suggested Action Safety Rate；
- Soft Repair Acceptance/No-progress Rate；
- Hard Constraint Regression Rate；
- 降级交付成功率；
- P50/P95 延迟、调用次数、Token 和费率快照成本。

消融固定使用同一批 v0.6 候选：

1. `without_critic`：只使用原 Candidate Score。
2. `critic_rank_only`：Critic 只参与排序。
3. `critic_with_one_repair`：排序 + 一次软修复。
4. `critic_without_grounding_gate`：只用于证明 Grounding Gate 必要性，不作为生产配置。

### 16.1 验收门禁

- Mock Schema Success Rate = 100%。
- Referential Grounding Rate = 100%。
- Suggested Action Safety Rate = 100%。
- Hard Constraint Regression Rate = 0%。
- Critic 故障降级交付成功率 = 100%。
- 所有循环满足调用和轮次预算。
- Semantic Grounding、选择一致率和软质量只报告实测值。
- 全量覆盖率不低于 90%，新增核心模块覆盖率不低于 90%。

## 17. 计划文件结构

```text
src/travel_agent/domain/critique_models.py
src/travel_agent/critique/{evidence,errors,evaluation,gateway,grounding,policy,prompts,protocols,quality}.py
src/travel_agent/critique/providers/{mock,deepseek,openai}.py
src/travel_agent/planning/soft_repair.py
evals/soft_critic/cases.jsonl
evals/soft_critic/evidence/*.json
scripts/evaluate_soft_critic.py
tests/test_critic_evidence.py
tests/test_critic_gateway.py
tests/test_critic_grounding.py
tests/test_critic_providers.py
tests/test_soft_critic_trajectory.py
tests/test_soft_repair.py
tests/test_soft_critic_benchmark.py
docs/v0.7/README.md
```

现有 `config.py`、`.env.example`、`runtime.py`、`domain/models.py`、`graph/state.py` 和 `graph/workflow.py` 需要接入配置、生命周期、响应字段和 Graph。只有在共享重排动作时扩充 `RepairActionKind`，不改变硬 Critic 语义。

## 18. 实施顺序

### A. 领域与 Evidence

Critique Models → Evidence Builder → Grounding Validator → Quality Gate → Unit Tests。

### B. Provider 可靠性层

Protocol/Gateway/错误分类/Prompt v1 → Mock → DeepSeek/OpenAI Contract → Runtime 生命周期。

### C. 只评价、不修复

接入 Context、Critic、Grounding、Quality Gate、选择和 Explanation，先证明 LLM 只影响软排序。

### D. 一次局部软修复

安全 Action Compiler → 独立 Soft Repair State/Nodes → Route Delta → Hard Validator → baseline 恢复和质量差分。

### E. 评测与文档

15+ Fixture → 消融 → 延迟/Token/成本快照 → 全量回归 → 发布 v0.7.0，并把本设计整理为 `docs/v0.7/README.md`。

## 19. 关键取舍

- 不拆多 Agent：Soft Critic 没有独立长期目标或工具闭环，作为受控 Node 更清晰。
- LLM 不返回总分：语义评价交给模型，权重和排序留给确定性代码。
- Critic 故障仍 completed：调用前计划已经通过 Hard Validator，模型故障不等于业务不可行。
- 一轮一个动作：软质量缺少精确单调目标，限制轮次才能保持局部性和可解释性。
- Explanation 不再调用 LLM：避免一次额外费用和选择后的新幻觉。

## 20. 面试演示

1. 三个硬合法候选 → Digest → 五维评价 → 引用校验 → Quality Gate → Grounded Explanation。
2. 节奏过密 → 建议移动非必去 POI → 本地 Compiler → Route Delta → Hard Validate → 再评价提升 → 接受修复，未影响日期 Hash 不变。
3. Critic 超时或跨候选引用 → 有界重试耗尽 → `unavailable/invalid_grounding` → 仍交付 v0.6 硬合法计划。

三条轨迹分别证明：LLM 软判断有证据、安全动作由代码控制、模型故障与业务不可行具有不同语义。
