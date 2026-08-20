# Task 10 实施报告：两阶段、路线感知 Planner

## 完成内容

- Phase 1 新增 frozen `CandidateDraft` / `DraftDay`。Draft 只保存候选 ID、风格、日期和 POI ID 顺序，不保存路线时间、道路距离或 Provider raw payload。
- `prepare_candidate_drafts` 负责三种风格、必去优先、重规划密度下界、轮询分日，以及仅用于排序的 Haversine 最近邻。
- `collect_route_queries` 生成住宿地或到达锚点到首个 POI、以及相邻 POI 间的方向敏感驾车查询；使用 strategy 32，并按首见 `route_key` 去重。
- Phase 2 `materialize_candidates` 只消费标准化 `RouteResult`。所有 draft segment 在排程前预检，缺少任何 key 都抛出带 `route_key` 的 `MissingRouteResult`，不会把工具失败解释为业务不可行。
- 日程中的道路距离和交通时间直接采用真实驾车 `RouteResult`；接驳步行量明确按 `min(round(distance_meters * 0.12), 2000)` 派生，不伪装为 Provider 步行路线。
- 每个受影响 `PlanItem` 标记 `walking_distance_estimated=True`；每个候选只追加一次 `walking_distance` DEFAULT assumption，同时保留上游 POI assumptions，供 Validator 产生候选级 warning。
- 物化使用按日有效营业窗口，只累计已知团队费用并统计未知费用项；指标和评分纳入 POI/路线数据置信度及 warning risk。

## 接口裁决

原简报的 `collect_route_queries(trip, drafts)` 与 ID-only draft 无法同时构造带坐标的 `RouteQuery`。经任务控制者裁决，接口调整为 `collect_route_queries(trip, drafts, pois)`：函数内部一次构建 ID 到 `PlanningPOI` 的 lookup；缺失 ID 抛 `MissingPlanningPOI`。这样不复制坐标到 draft、不使用全局 registry，并保持 State/Checkpoint 可恢复。

路线“fallback”也经裁决澄清：这里没有 Route Provider fallback 或 sentinel。真实驾车路线是 Provider 事实，只有从驾车距离派生的步行量属于 DEFAULT assumption；缺失驾车路线仍严格报错。

## TDD 证据

1. Phase 1 RED：先创建 draft/query 测试，聚焦 pytest 在收集期按预期报 `ModuleNotFoundError: travel_agent.planning.drafts`。
2. Phase 1 GREEN：实现 frozen draft、确定性选择/Haversine 排序、三参数 query 收集和缺失 POI 错误后，聚焦测试 `5 passed`。
3. Phase 2 RED：先加入物化测试，聚焦 pytest 在收集期按预期报 planner 缺少 `MissingRouteResult` / `materialize_candidates`。
4. Phase 2 GREEN：实现 RouteResult 物化、按日窗口、诚实成本与 assumption 后，首轮聚焦测试 `9 passed`。
5. 自审 RED/GREEN：新增“即使前一活动无法排入，也必须预检后续 draft segment”的测试；旧实现未抛错，加入整体 route preflight 后通过。

## 验证

- `\.venv\Scripts\python.exe -m pytest tests\test_route_aware_planner.py tests\test_workflow.py -v`：15 passed。
- `\.venv\Scripts\python.exe -m pytest`：108 passed，1 个既有 Starlette/httpx 弃用 warning。
- `git diff --check`：通过；仅有工作区 LF 到 CRLF 的 Git 提示。

## 自审与顾虑

- 测试覆盖三风格、必去优先、密度下界、draft 深层不可变结构、方向性/去重、Provider 路线值、禁止 materializer 调用估算器、全 segment 缺失路线、assumption 去重、按日窗口、未知费用、路线置信度与 warning risk。
- 根据迁移裁决，`generate_candidates(POI)` 暂作为 v0.1 Graph 的 compatibility-only 桥保留，因此既有 workflow 回归无需改写。新 `materialize_candidates` 调用链完全不使用 `estimate_route`。
- Task 11 切换 Graph 到两阶段 Planner 后，必须删除或彻底停用该 legacy 桥，并以轨迹/调用测试证明生产链路只消费 ToolGateway 的 `RouteResult`。

## 第 1 轮评审修复：must-visit 优先的路线连续排序

### 根因与修复

- 原 Phase 1 虽在候选选择时提高 must-visit 分数，但分日后会对整日 POI 重新执行无优先级 Haversine 最近邻。更近但无法排入营业窗口的 optional POI 可能排在 must-visit 前；Phase 2 为保持预取相邻路线与实际 origin 一致会 `break`，导致后续可行 must-visit 永远不被评估。
- 修复限定在 Phase 1：每日先分 must-visit 与 optional 两层，must-visit 层优先；每层内部继续使用 Haversine 最近邻与 POI ID 稳定 tie-break。Phase 2 不改为 `continue`，因此 draft、route query 和实际日程仍保持同一连续顺序。

### TDD 证据

1. RED：新增单日“几何上更近但营业时间不可行的 optional + 可行 must-visit”端到端测试。旧实现的 route query 顺序虽与 draft 一致，但 materialized candidate 活动为空，断言 `lingyin` 在日程中失败。
2. GREEN：加入 must-visit 分层最近邻后，同一测试通过；must-visit 先物化，后续不可行 optional 才终止该日排程。
3. 回归保护：新增无 must-visit 时普通 POI 仍按最近邻排序的测试；既有测试继续覆盖三风格、密度收缩下界、方向敏感去重和 route/materializer 顺序。

### 第 1 轮验证与自审

- `\.venv\Scripts\python.exe -m pytest tests\test_route_aware_planner.py tests\test_workflow.py -v`：17 passed。
- `\.venv\Scripts\python.exe -m pytest`：110 passed，1 个既有 Starlette/httpx 弃用 warning。
- `git diff --check`：通过；仅有工作区 LF 到 CRLF 的 Git 提示。
- 自审确认本轮只改变 Phase 1 的每日排序优先层，不改变候选选择数量、三种风格、密度收缩、query 去重、Phase 2 缺失路线语义或 compatibility-only bridge。
