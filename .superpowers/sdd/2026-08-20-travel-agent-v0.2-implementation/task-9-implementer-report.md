# Task 9 实现报告：可感知告警的验证与诚实成本模型

## 完成内容

- `ValidationResult` 以 `ValidationStatus`（`valid`、`valid_with_warnings`、`invalid`）作为稳定路由语义，`valid` 保留为可序列化兼容计算字段。
- `PlanItem.estimated_cost` 允许为 `None`；日计划和指标区分 `known_estimated_cost` 与 `unknown_cost_item_count`，同时继续序列化旧的 `estimated_cost` 字段。
- Validator 仅累计非空成本。预算存在未知成本时产生 `budget_unverified` WARNING；已知成本下界超预算时产生 `budget_exceeded` ERROR。
- Validator 只将 `ValueSource.DEFAULT` 的 `PlanningAssumption` 转为告警，并按语义类型去重；覆盖营业时间、时长、步行距离及费用等默认事实。
- 修复了 Task 2 provenance 类型的隐藏导入顺序依赖：仅导入 `domain.models` 即可构造相关模型并生成 schema，不再依赖 `planning.defaults` 的偶然 `model_rebuild` 副作用。

## TDD 证据

1. RED：新增状态测试后，pytest 收集失败，报 `ValidationStatus` 不存在。
2. GREEN：实现状态模型和 `from_violations` 后，状态测试 2/2 通过。
3. RED：未知成本测试暴露 `Decimal + None` 的 TypeError；已知超预算测试暴露旧的 `ValidationResult(valid=...)` 构造不再有效。
4. RED：隔离子进程只导入 `domain.models` 并生成 schema，报 `PlanCandidate` 未定义 `ValueSource`。
5. GREEN：领域层显式重建 provenance 类型、Validator 完成成本与告警规则后，数据质量测试 6/6 通过。

## 验证

- `\.venv\Scripts\python.exe -m pytest tests/test_domain_models.py tests/test_data_quality_validation.py -v`：9 passed。
- `\.venv\Scripts\python.exe -m pytest`：85 passed，1 个既有的 Starlette/httpx 弃用警告。
- `git diff --check`：无空白错误。

## 顾虑与后续接口约定

- Validator 只消费候选计划级 `assumptions` 以产生每类一次的告警；Task 10 必须在路线回退时为每个受影响 `PlanItem` 标记 `walking_distance_estimated=True`，并同时写入候选计划级 `walking_distance` 默认假设。否则不会产生该候选计划级告警。
- 当已知成本本身已超预算且仍有未知成本时，Validator 会同时保留 ERROR 和 WARNING：前者是确定的硬违规，后者说明总成本仍不完整；状态因此为 `invalid`。
